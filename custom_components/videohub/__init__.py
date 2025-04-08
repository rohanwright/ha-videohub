"""Blackmagic Videohub integration for controlling Blackmagic Videohub devices."""
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
    DATA_VIDEOHUB,
    DATA_COORDINATOR,
    CONF_INIT_TIMEOUT,
    DEFAULT_RECONNECT_DELAY,
    SERVICE_SET_INPUT,
    SERVICE_SET_OUTPUT_LABEL,
    SERVICE_SET_INPUT_LABEL,
)
from .pyvideohub import BlackmagicVideohub

_LOGGER = logging.getLogger(__name__)

# List of platforms to support
PLATFORMS = [Platform.SENSOR, Platform.SELECT]

# Service schemas
SET_INPUT_SCHEMA = vol.Schema({
    vol.Required("input"): vol.Any(int, str),
})

SET_LABEL_SCHEMA = vol.Schema({
    vol.Required("label"): str,
})

SET_INPUT_LABEL_SCHEMA = vol.Schema({
    vol.Required("input"): vol.Any(int, str),
    vol.Required("label"): str,
})

# Define the service handler functions before they are registered
async def handle_set_input(call: ServiceCall) -> None:
    """Handle the service call to set an input."""
    # Get entity_id from the target
    entity_id = call.target.get("entity_id")
    
    if not entity_id:
        _LOGGER.error("No target entity provided for set_input service")
        return
    
    input_value = call.data.get("input")
    _LOGGER.debug("Set input service called for %s with input %s", entity_id, input_value)
    
    entity_registry = er.async_get(hass)
    entity_entry = entity_registry.async_get(entity_id)
    
    if not entity_entry or not entity_entry.config_entry_id:
        _LOGGER.error("Entity %s not found or not from this integration", entity_id)
        return
        
    hub_data = hass.data[DOMAIN].get(entity_entry.config_entry_id)
    if not hub_data:
        _LOGGER.error("No Videohub associated with entity %s", entity_id)
        return
        
    hub = hub_data[DATA_VIDEOHUB]
    coordinator = hub_data[DATA_COORDINATOR]
    
    # Extract output number from unique_id
    # Format is: entry_id_output_NNN (where NNN is zero-padded)
    unique_id_parts = entity_entry.unique_id.split("_")
    if len(unique_id_parts) < 3 or unique_id_parts[-2] != "output":
        _LOGGER.error("Entity %s is not a Videohub output entity (unique_id: %s)", 
                     entity_id, entity_entry.unique_id)
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
            _LOGGER.info("Successfully set input %s for output %s", input_value, output_number)
            # Refresh coordinator data after successful operation
            await coordinator.async_refresh()
    except ValueError as err:
        _LOGGER.error("Invalid output ID format in entity %s with unique_id %s: %s", 
                     entity_id, entity_entry.unique_id, str(err))
    except Exception as ex:
        _LOGGER.error("Error in set_input service: %s", str(ex), exc_info=True)

async def handle_set_output_label(call: ServiceCall) -> None:
    """Handle the service call to set an output label."""
    # Get entity_id from the target
    entity_id = call.target.get("entity_id")
    
    if not entity_id:
        _LOGGER.error("No target entity provided for set_output_label service")
        return
    
    label = call.data.get("label")
    _LOGGER.debug("Set output label service called for %s with label %s", entity_id, label)
    
    entity_registry = er.async_get(hass)
    entity_entry = entity_registry.async_get(entity_id)
    
    if not entity_entry or not entity_entry.config_entry_id:
        _LOGGER.error("Entity %s not found or not from this integration", entity_id)
        return
        
    hub_data = hass.data[DOMAIN].get(entity_entry.config_entry_id)
    if not hub_data:
        _LOGGER.error("No Videohub associated with entity %s", entity_id)
        return
        
    hub = hub_data[DATA_VIDEOHUB]
    coordinator = hub_data[DATA_COORDINATOR]
    
    # Extract output number from unique_id
    unique_id_parts = entity_entry.unique_id.split("_")
    if len(unique_id_parts) < 3 or unique_id_parts[-2] != "output":
        _LOGGER.error("Entity %s is not a Videohub output entity (unique_id: %s)", 
                     entity_id, entity_entry.unique_id)
        return
        
    try:
        # Strip leading zeros when converting to int
        output_number = int(unique_id_parts[-1])
        _LOGGER.debug("Setting label '%s' for output %s", label, output_number)
        success = hub.set_output_label(output_number, label)
        if not success:
            _LOGGER.error("Failed to set label for output %s", output_number)
        else:
            _LOGGER.info("Successfully set label '%s' for output %s", label, output_number)
            # Refresh coordinator data after successful operation
            await coordinator.async_refresh()
    except ValueError as err:
        _LOGGER.error("Invalid output ID format in entity %s: %s", entity_id, str(err))
    except Exception as ex:
        _LOGGER.error("Error in set_output_label service: %s", str(ex), exc_info=True)

async def handle_set_input_label(call: ServiceCall) -> None:
    """Handle the service call to set an input label."""
    device_id = call.target.get("device_id")
    
    if not device_id:
        _LOGGER.error("No target device provided for set_input_label service")
        return
    
    label = call.data.get("label")
    input_value = call.data.get("input")
    _LOGGER.debug("Set input label service called for device %s with input %s, label: %s", 
                 device_id, input_value, label)
    
    # Get device registry to find config entry associated with device
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)
    
    if not device:
        _LOGGER.error("Device %s not found", device_id)
        return
        
    # Get config entry for this device
    config_entry_id = None
    for entry_id in device.config_entries:
        if entry_id in hass.data.get(DOMAIN, {}):
            config_entry_id = entry_id
            break
    
    if not config_entry_id:
        _LOGGER.error("No Videohub config entry found for device %s", device_id)
        return
        
    hub_data = hass.data[DOMAIN].get(config_entry_id)
    if not hub_data:
        _LOGGER.error("No Videohub data found for config entry %s", config_entry_id)
        return
        
    hub = hub_data[DATA_VIDEOHUB]
    coordinator = hub_data[DATA_COORDINATOR]
    
    # Handle input as number or name
    input_number = None
    if isinstance(input_value, int):
        input_number = input_value
    elif isinstance(input_value, str):
        if input_value.isdigit():
            input_number = int(input_value)
        else:
            # For non-numeric inputs, find the input by name
            for num, name in coordinator.data.get("inputs", {}).items():
                if name == input_value:
                    input_number = num
                    break
    
    if input_number is None:
        _LOGGER.error("Could not determine input number from value: %s", input_value)
        return
    
    try:
        success = hub.set_input_label(input_number, label)
        if not success:
            _LOGGER.error("Failed to set label for input %s", input_number)
        else:
            _LOGGER.info("Successfully set label '%s' for input %s", label, input_number)
            # Force the coordinator to refresh immediately
            await coordinator.async_refresh()
    except Exception as ex:
        _LOGGER.error("Error in set_input_label service: %s", str(ex), exc_info=True)

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Videohub component from YAML configuration."""
    # Initialize the domain data structure
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Videohub from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    name = entry.data[CONF_NAME]
    init_timeout = entry.options.get(CONF_INIT_TIMEOUT, 30)

    _LOGGER.info("Establishing connection with Videohub at %s:%i", host, port)
    videohub = BlackmagicVideohub(host, port, hass.loop)
    
    # Start the connection
    videohub.start()
    
    # Wait for initialization with timeout
    start_time = asyncio.get_event_loop().time()
    initialization_complete = False
    
    # First, wait for connection
    while not videohub.connected:
        if asyncio.get_event_loop().time() - start_time > 10:  # 10 second connection timeout
            _LOGGER.warning("Timeout waiting for Videohub connection")
            # Don't abort setup - the coordinator will retry
            break
        await asyncio.sleep(1)
    
    if videohub.connected:
        _LOGGER.info("Connected to Videohub, waiting for initialization")
        
        # Now wait for initialization
        while not videohub.is_initialised:
            if asyncio.get_event_loop().time() - start_time > init_timeout:
                _LOGGER.warning("Timeout waiting for Videohub initialization")
                # Don't abort setup - entities will be marked unavailable
                break
                
            if not videohub.connected:
                _LOGGER.warning("Lost connection while waiting for initialization")
                break
                
            await asyncio.sleep(1)
            
        # If we get here and the device is initialized, consider initialization complete
        initialization_complete = videohub.is_initialised
        
    # Even if initialization failed, try to create coordinator
    async def async_update_data():
        """Fetch data from the Videohub device."""
        try:
            if not videohub.connected:
                raise UpdateFailed("Videohub connection lost")
            
            # Get one-based outputs and inputs for external use
            outputs = videohub.get_outputs()
            inputs = videohub.get_inputs()
            
            # Request fresh data if no outputs but we're connected
            if not outputs and videohub.connected:
                _LOGGER.debug("No outputs in coordinator update - requesting refresh")
                await videohub.request_status_dump("VIDEO OUTPUT ROUTING")
                await asyncio.sleep(0.5)
                outputs = videohub.get_outputs()
            
            # Prepare data for components
            data = {
                "connected": videohub.connected,
                "initialized": videohub.is_initialised,
                "inputs": inputs,
                "outputs": outputs,
                "device_info": {
                    "model_name": videohub.model_name,
                    "video_inputs_count": videohub.video_inputs_count,
                    "video_outputs_count": videohub.video_outputs_count,
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
        DATA_VIDEOHUB: videohub,
        DATA_COORDINATOR: coordinator,
        "name": name,
    }
    
    # Create callback for updating the coordinator
    def hub_update_callback(output_id=0):
        """Handle hub updates."""
        _LOGGER.debug("Hub update callback triggered for output: %s", output_id)
        hass.async_create_task(coordinator.async_refresh())

    # Add callback to hub
    videohub.add_update_callback(hub_update_callback)
    
    # Initial manual refresh to ensure we have data
    await coordinator.async_refresh()
    _LOGGER.info("Finished fetching Videohub Coordinator data in %.3f seconds (success: %s)",
                asyncio.get_event_loop().time() - start_time,
                coordinator.last_update_success)
    
    # Request additional data if needed
    if not coordinator.data or not coordinator.data.get("outputs"):
        _LOGGER.warning("No outputs in coordinator data after initial refresh - requesting data manually")
        
        # Request key data directly
        if videohub.connected:
            await videohub.request_status_dump("VIDEOHUB DEVICE")
            await asyncio.sleep(0.5)
            await videohub.request_status_dump("INPUT LABELS")
            await asyncio.sleep(0.5)
            await videohub.request_status_dump("OUTPUT LABELS")
            await asyncio.sleep(0.5)
            await videohub.request_status_dump("VIDEO OUTPUT ROUTING")
            await asyncio.sleep(1)
            
            # Update coordinator after manual requests
            await coordinator.async_refresh()
            _LOGGER.info("After manual data requests: coordinator has %d outputs", 
                         len(coordinator.data.get("outputs", {})) if coordinator.data else 0)

    # Register services for the domain - always register to ensure services are updated
    _LOGGER.info("Registering Videohub services")
    
    # Check if services already exist and remove them to ensure clean registration
    for service in (SERVICE_SET_INPUT, SERVICE_SET_OUTPUT_LABEL, SERVICE_SET_INPUT_LABEL):
        if hass.services.has_service(DOMAIN, service):
            _LOGGER.debug("Service %s.%s already exists, will be updated", DOMAIN, service)
    
    # Register all services
    try:
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
            schema=SET_INPUT_LABEL_SCHEMA
        )
        
        # Verify registration was successful
        all_services = True
        for service in (SERVICE_SET_INPUT, SERVICE_SET_OUTPUT_LABEL, SERVICE_SET_INPUT_LABEL):
            if not hass.services.has_service(DOMAIN, service):
                all_services = False
                _LOGGER.error("Failed to register service %s.%s", DOMAIN, service)
                
        if all_services:
            _LOGGER.info("All Videohub services registered successfully")
        else:
            _LOGGER.warning("Some Videohub services failed to register")
    
    except Exception as ex:
        _LOGGER.error("Error registering services: %s", str(ex))

    # Set up all platform entities
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # First, try to stop the hub connection regardless of whether unload succeeds
    if entry.entry_id in hass.data.get(DOMAIN, {}):
        hub = hass.data[DOMAIN][entry.entry_id].get(DATA_VIDEOHUB)
        if hub:
            # Log the connection state before stopping
            _LOGGER.info("Shutting down Videohub connection (currently %s)", 
                        "connected" if hub.connected else "disconnected")
            # Stop the connection - set allow_reconnect=False since we're unloading
            hub.stop(allow_reconnect=False)
            # Give the connection a moment to close properly
            await asyncio.sleep(1)
            
            # Verify it's actually stopped
            if hub.connected:
                _LOGGER.warning("Videohub connection didn't close properly, forcing cleanup")
                # Clear callbacks to prevent further Home Assistant interactions
                hub._update_callbacks = []
                
                # Safely close transport if available
                if hasattr(hub, '_transport') and hub._transport:
                    try:
                        _LOGGER.debug("Forcing transport closure")
                        if hasattr(hub._transport, 'close'):
                            hub._transport.close()
                        elif hasattr(hub._transport, 'abort'):
                            hub._transport.abort()
                        hub._transport = None  # Release reference
                    except Exception as ex:
                        _LOGGER.error("Error closing transport: %s", str(ex))
            else:
                _LOGGER.info("Videohub connection successfully closed")
                
    # Now unload the platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        # Remove the current entry from data
        if entry.entry_id in hass.data.get(DOMAIN, {}):
            hass.data[DOMAIN].pop(entry.entry_id)
        
        # Check if this was the last entry AFTER removing the current one
        if not hass.data.get(DOMAIN, {}):
            _LOGGER.info("Unloading last Videohub entry, removing services")
            for service in (SERVICE_SET_INPUT, SERVICE_SET_OUTPUT_LABEL, SERVICE_SET_INPUT_LABEL):
                if hass.services.has_service(DOMAIN, service):
                    _LOGGER.debug("Removing service %s.%s", DOMAIN, service)
                    hass.services.async_remove(DOMAIN, service)
                    # Add small delay to ensure service is fully removed
                    await asyncio.sleep(0.1)
            _LOGGER.info("All Videohub services have been removed")
        else:
            _LOGGER.debug("Other Videohub entries still exist, keeping services registered")
    else:
        _LOGGER.warning("Failed to unload platforms for Videohub integration")
            
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate an old config entry."""
    version = config_entry.version

    return True