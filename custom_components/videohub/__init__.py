"""Smart Videohub integration for controlling Blackmagic Smart Videohub devices."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.helpers.device_registry as dr
import homeassistant.helpers.entity_registry as er
import voluptuous as vol

from .const import (
    DOMAIN,
    DATA_SMARTVIDEOHUB,
    DATA_COORDINATOR,
    CONF_INIT_TIMEOUT,
    DEFAULT_RECONNECT_DELAY,
    SERVICE_SET_INPUT,
    SERVICE_SET_OUTPUT_LABEL,
    SERVICE_SET_INPUT_LABEL,
)
from .pyvideohub import SmartVideoHub

_LOGGER = logging.getLogger(__name__)

# List of platforms to support
PLATFORMS = [Platform.SENSOR, Platform.SELECT]

# Service schemas
SET_INPUT_SCHEMA = vol.Schema({
    vol.Required("entity_id"): str,
    vol.Required("input"): vol.Any(int, str),
})

SET_LABEL_SCHEMA = vol.Schema({
    vol.Required("entity_id"): str,
    vol.Required("label"): str, 
})

SET_INPUT_LABEL_SCHEMA = vol.Schema({
    vol.Required("entity_id"): str,
    vol.Required("input"): vol.Any(int, str),
    vol.Required("label"): str,
})

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Smart Videohub component from YAML configuration."""
    # Initialize the domain data structure
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Smart Videohub from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    name = entry.data[CONF_NAME]
    init_timeout = entry.options.get(CONF_INIT_TIMEOUT, 30)

    _LOGGER.info("Establishing connection with SmartVideoHub at %s:%i", host, port)
    smartvideohub = SmartVideoHub(host, port, hass.loop)
    
    # Start the connection
    smartvideohub.start()
    
    # Wait for initialization with timeout
    start_time = asyncio.get_event_loop().time()
    initialization_complete = False
    
    # First, wait for connection
    while not smartvideohub.connected:
        if asyncio.get_event_loop().time() - start_time > 10:  # 10 second connection timeout
            _LOGGER.warning("Timeout waiting for Videohub connection")
            # Don't abort setup - the coordinator will retry
            break
        await asyncio.sleep(1)
    
    if smartvideohub.connected:
        _LOGGER.info("Connected to Videohub, waiting for initialization")
        
        # Now wait for initialization
        while not smartvideohub.is_initialised:
            if asyncio.get_event_loop().time() - start_time > init_timeout:
                _LOGGER.warning("Timeout waiting for Videohub initialization")
                # Don't abort setup - entities will be marked unavailable
                break
                
            if not smartvideohub.connected:
                _LOGGER.warning("Lost connection while waiting for initialization")
                break
                
            await asyncio.sleep(1)
            
        # If we get here and the device is initialized, consider initialization complete
        initialization_complete = smartvideohub.is_initialised
        
    # Even if initialization failed, try to create coordinator
    async def async_update_data():
        """Fetch data from the VideoHub device."""
        try:
            if not smartvideohub.connected:
                raise UpdateFailed("VideoHub connection lost")
            
            # Get one-based outputs and inputs for external use
            outputs = smartvideohub.get_outputs()
            inputs = smartvideohub.get_inputs()
            
            # Request fresh data if no outputs but we're connected
            if not outputs and smartvideohub.connected:
                _LOGGER.debug("No outputs in coordinator update - requesting refresh")
                await smartvideohub.request_status_dump("VIDEO OUTPUT ROUTING")
                await asyncio.sleep(0.5)
                outputs = smartvideohub.get_outputs()
            
            # Prepare data for components
            data = {
                "connected": smartvideohub.connected,
                "initialized": smartvideohub.is_initialised,
                "inputs": inputs,
                "outputs": outputs,
                "device_info": {
                    "model_name": smartvideohub.model_name,
                    "video_inputs_count": smartvideohub.video_inputs_count,
                    "video_outputs_count": smartvideohub.video_outputs_count,
                },
            }
            
            _LOGGER.debug(
                "Coordinator data: connected=%s, initialized=%s, inputs=%d, outputs=%d",
                data["connected"],
                data["initialized"],
                len(inputs),
                len(outputs)
            )
            
            return data
        except Exception as ex:
            _LOGGER.error("Error updating coordinator data: %s", str(ex))
            raise UpdateFailed(f"Error updating data: {str(ex)}") from ex

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{name} Coordinator",
        update_method=async_update_data,
        # Increase update frequency for better responsiveness
        update_interval=timedelta(seconds=15),
    )
    
    # Store data in hass
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_SMARTVIDEOHUB: smartvideohub,
        DATA_COORDINATOR: coordinator,
        "name": name,
    }
    
    # Create callback for updating the coordinator
    def hub_update_callback(output_id=0):
        """Handle hub updates."""
        _LOGGER.debug("Hub update callback triggered for output: %s", output_id)
        hass.async_create_task(coordinator.async_refresh())

    # Add callback to hub
    smartvideohub.add_update_callback(hub_update_callback)
    
    # Initial manual refresh to ensure we have data
    await coordinator.async_refresh()
    _LOGGER.info("Finished fetching Videohub Coordinator data in %.3f seconds (success: %s)",
                asyncio.get_event_loop().time() - start_time,
                coordinator.last_update_success)
    
    # Request additional data if needed
    if not coordinator.data or not coordinator.data.get("outputs"):
        _LOGGER.warning("No outputs in coordinator data after initial refresh - requesting data manually")
        
        # Request key data directly
        if smartvideohub.connected:
            await smartvideohub.request_status_dump("VIDEOHUB DEVICE")
            await asyncio.sleep(0.5)
            await smartvideohub.request_status_dump("INPUT LABELS")
            await asyncio.sleep(0.5)
            await smartvideohub.request_status_dump("OUTPUT LABELS")
            await asyncio.sleep(0.5)
            await smartvideohub.request_status_dump("VIDEO OUTPUT ROUTING")
            await asyncio.sleep(1)
            
            # Update coordinator after manual requests
            await coordinator.async_refresh()
            _LOGGER.info("After manual data requests: coordinator has %d outputs", 
                         len(coordinator.data.get("outputs", {})) if coordinator.data else 0)

    # Register services only once for the domain
    if not hass.services.has_service(DOMAIN, SERVICE_SET_INPUT):
        hass.services.async_register(
            DOMAIN, 
            SERVICE_SET_INPUT, 
            handle_set_input, 
            schema=SET_INPUT_SCHEMA
        )
        
        hass.services.async_register(
            DOMAIN, 
            SERVICE_SET_OUTPUT_LABEL, 
            handle_set_output_label, 
            schema=SET_LABEL_SCHEMA
        )
        
        hass.services.async_register(
            DOMAIN, 
            SERVICE_SET_INPUT_LABEL, 
            handle_set_input_label, 
            schema=SET_INPUT_LABEL_SCHEMA  # Use the correct schema here
        )

    # Set up all platform entities
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # First, try to stop the hub connection regardless of whether unload succeeds
    if entry.entry_id in hass.data[DOMAIN]:
        hub = hass.data[DOMAIN][entry.entry_id].get(DATA_SMARTVIDEOHUB)
        if hub:
            # Log the connection state before stopping
            _LOGGER.info("Shutting down VideoHub connection (currently %s)", 
                        "connected" if hub.connected else "disconnected")
            # Stop the connection - set allow_reconnect=False since we're unloading
            hub.stop(allow_reconnect=False)
            # Give the connection a moment to close properly
            await asyncio.sleep(1)
            
            # Verify it's actually stopped
            if hub.connected:
                _LOGGER.warning("VideoHub connection didn't close properly, forcing transport closure")
                if hasattr(hub, '_transport') and hub._transport:
                    hub._transport.abort()
            else:
                _LOGGER.info("VideoHub connection successfully closed")
                
    # Now unload the platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        # Only remove services if this is the last config entry
        if len(hass.data[DOMAIN]) == 0:
            for service in (SERVICE_SET_INPUT, SERVICE_SET_OUTPUT_LABEL, SERVICE_SET_INPUT_LABEL):
                if hass.services.has_service(DOMAIN, service):
                    hass.services.async_remove(DOMAIN, service)
        
        # Clean up data entry
        if entry.entry_id in hass.data[DOMAIN]:
            hass.data[DOMAIN].pop(entry.entry_id)
    else:
        _LOGGER.warning("Failed to unload platforms for VideoHub integration")
            
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate an old config entry."""
    version = config_entry.version

    return True


async def handle_set_input(call: ServiceCall) -> None:
    """Handle the service call to set an input."""
    entity_id = call.data.get("entity_id")
    input_value = call.data.get("input")
    
    entity_registry = er.async_get(hass)
    entity_entry = entity_registry.async_get(entity_id)
    
    if not entity_entry or not entity_entry.config_entry_id:
        _LOGGER.error("Entity %s not found or not from this integration", entity_id)
        return
        
    hub_data = hass.data[DOMAIN].get(entity_entry.config_entry_id)
    if not hub_data:
        _LOGGER.error("No VideoHub associated with entity %s", entity_id)
        return
        
    hub = hub_data[DATA_SMARTVIDEOHUB]
    coordinator = hub_data[DATA_COORDINATOR]
    
    # Extract output number from unique_id
    # Format is: entry_id_output_NNN (where NNN is zero-padded)
    unique_id_parts = entity_entry.unique_id.split("_")
    if len(unique_id_parts) < 3 or unique_id_parts[-2] != "output":
        _LOGGER.error("Entity %s is not a VideoHub output entity", entity_id)
        return
        
    try:
        # Log the unique_id for debugging
        _LOGGER.debug("Processing entity with unique_id: %s", entity_entry.unique_id)
        
        # Strip leading zeros when converting to int
        output_number = int(unique_id_parts[-1])
        
        # Handle input as number or name
        if isinstance(input_value, int):
            success = hub.set_input(output_number, input_value)
        else:
            # For string inputs, check if it's just a number
            if isinstance(input_value, str) and input_value.isdigit():
                success = hub.set_input(output_number, int(input_value))
            else:
                success = hub.set_input_by_name(output_number, input_value)
            
        if not success:
            _LOGGER.error("Failed to set input %s for output %s", input_value, output_number)
        else:
            # Refresh coordinator data after successful operation
            await coordinator.async_refresh()
    except ValueError:
        _LOGGER.error("Invalid output ID format in entity %s with unique_id %s", 
                     entity_id, entity_entry.unique_id)

async def handle_set_output_label(call: ServiceCall) -> None:
    """Handle the service call to set an output label."""
    entity_id = call.data.get("entity_id")
    label = call.data.get("label")
    
    entity_registry = er.async_get(hass)
    entity_entry = entity_registry.async_get(entity_id)
    
    if not entity_entry or not entity_entry.config_entry_id:
        _LOGGER.error("Entity %s not found or not from this integration", entity_id)
        return
        
    hub_data = hass.data[DOMAIN].get(entity_entry.config_entry_id)
    if not hub_data:
        _LOGGER.error("No VideoHub associated with entity %s", entity_id)
        return
        
    hub = hub_data[DATA_SMARTVIDEOHUB]
    
    # Extract output number from unique_id
    unique_id_parts = entity_entry.unique_id.split("_")
    if len(unique_id_parts) < 3 or unique_id_parts[-2] != "output":
        _LOGGER.error("Entity %s is not a VideoHub output entity", entity_id)
        return
        
    try:
        # Strip leading zeros when converting to int
        output_number = int(unique_id_parts[-1])
        success = hub.set_output_label(output_number, label)
        if not success:
            _LOGGER.error("Failed to set label for output %s", output_number)
        else:
            # Refresh coordinator data after successful operation
            await coordinator.async_refresh()
    except ValueError:
        _LOGGER.error("Invalid output ID format in entity %s", entity_id)

async def handle_set_input_label(call: ServiceCall) -> None:
    """Handle the service call to set an input label."""
    entity_id = call.data.get("entity_id")
    label = call.data.get("label")
    input_number = call.data.get("input")  # Now properly defined in the schema
    
    entity_registry = er.async_get(hass)
    entity_entry = entity_registry.async_get(entity_id)
    
    if not entity_entry or not entity_entry.config_entry_id:
        _LOGGER.error("Entity %s not found or not from this integration", entity_id)
        return
        
    hub_data = hass.data[DOMAIN].get(entity_entry.config_entry_id)
    if not hub_data:
        _LOGGER.error("No VideoHub associated with entity %s", entity_id)
        return
        
    hub = hub_data[DATA_SMARTVIDEOHUB]
    
    success = hub.set_input_label(input_number, label)
    if not success:
        _LOGGER.error("Failed to set label for input %s", input_number)
    else:
        # Refresh coordinator data after successful operation
        await coordinator.async_refresh()