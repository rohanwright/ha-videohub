"""Config flow for Blackmagic Videohub integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import (
    DOMAIN, 
    DEFAULT_PORT, 
    DEFAULT_NAME, 
    CONF_INIT_TIMEOUT
)

_LOGGER = logging.getLogger(__name__)

# Default settings
DEFAULT_INIT_TIMEOUT = 15  # seconds

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
    }
)

STEP_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_INIT_TIMEOUT, default=DEFAULT_INIT_TIMEOUT): int,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    from .pyvideohub import BlackmagicVideohub

    hub = BlackmagicVideohub(data[CONF_HOST], data[CONF_PORT], hass.loop)
    
    try:
        _LOGGER.info("Attempting to connect to Videohub at %s:%s", data[CONF_HOST], data[CONF_PORT])
        hub.start()
        
        # Set timeout for initialization
        init_timeout = data.get(CONF_INIT_TIMEOUT, DEFAULT_INIT_TIMEOUT)
        start_time = asyncio.get_event_loop().time()
        
        # Wait for connection to be established
        connection_timeout = 10  # seconds
        connection_start_time = asyncio.get_event_loop().time()
        
        # First wait for connection to be established
        while not hub.connected:
            if asyncio.get_event_loop().time() - connection_start_time > connection_timeout:
                _LOGGER.error("Connection timed out to Videohub at %s:%s", data[CONF_HOST], data[CONF_PORT])
                raise CannotConnect(f"Connection timed out after {connection_timeout} seconds - please check network settings")
            
            await asyncio.sleep(0.5)  # Check more frequently
        
        _LOGGER.info("TCP connection established, waiting for initialization")
        
        # Then wait for initialization to complete
        while not hub.is_initialised:
            if not hub.connected:
                _LOGGER.error("Connection lost to Videohub at %s:%s", data[CONF_HOST], data[CONF_PORT])
                raise CannotConnect(f"Connection established but then lost to {data[CONF_HOST]}:{data[CONF_PORT]}")
                
            if asyncio.get_event_loop().time() - start_time > init_timeout:
                _LOGGER.error("Initialization timed out for Videohub at %s:%s", data[CONF_HOST], data[CONF_PORT])
                raise CannotConnect(f"Connected but initialization timed out after {init_timeout} seconds. Try increasing the timeout or check device compatibility.")
            
            await asyncio.sleep(0.5)  # Check more frequently
            
        # Allow a brief moment for any final data processing
        await asyncio.sleep(0.5)
        
        # Force data collection if needed
        if len(hub.outputs) == 0:
            _LOGGER.debug("Outputs dictionary is empty, requesting data directly")
            # Request key data
            await hub.request_status_dump("VIDEOHUB DEVICE")
            await asyncio.sleep(0.5)
            await hub.request_status_dump("OUTPUT LABELS")
            await asyncio.sleep(0.5)
            await hub.request_status_dump("VIDEO OUTPUT ROUTING")
            await asyncio.sleep(2)  # Give time for data to process
            
        # Check for outputs with additional debug information
        outputs = hub.get_outputs()
        _LOGGER.debug("Found %d outputs during validation", len(outputs))
        
        if not outputs:
            # As a last resort, create default outputs from device info
            if hub.video_outputs_count > 0:
                _LOGGER.warning("No outputs found but device reports %d outputs, creating defaults", hub.video_outputs_count)
                for i in range(1, hub.video_outputs_count + 1):
                    hub.outputs[i-1] = {  # Use zero-based IDs internally
                        "label": f"Output {i:03d}",  # Use zero-padded format
                        "display_id": i,
                        "input": 0,  # Default to first input (zero-based)
                    }
                outputs = hub.get_outputs()
                _LOGGER.debug("Created %d default outputs", len(outputs))
            else:
                # Additional debug info to help understand why no outputs were found
                _LOGGER.error("No outputs found on device - device info: Model=%s, Inputs=%d, Outputs=%d, Is Initialized=%s",
                            hub.model_name, len(hub.inputs), hub.video_outputs_count, hub.is_initialised)
                raise NoOutputs("No outputs found on the device")
            
        # Return validated data
        return {
            "title": data[CONF_NAME],
            "device_info": {
                "model": hub.model_name,
                "inputs": len(hub.inputs),
                "outputs": len(outputs),
                "initialized": hub.is_initialised
            }
        }
    
    except ConnectionRefusedError:
        _LOGGER.error("Connection refused to Videohub at %s:%s", data[CONF_HOST], data[CONF_PORT])
        raise CannotConnect(f"Connection refused - please check if device is online at {data[CONF_HOST]}:{data[CONF_PORT]}")
    except asyncio.TimeoutError:
        _LOGGER.error("Connection timed out to Videohub at %s:%s", data[CONF_HOST], data[CONF_PORT])
        raise CannotConnect(f"Connection timed out - please check network settings and device IP")
    except Exception as ex:
        _LOGGER.error("Error connecting to Videohub: %s", str(ex))
        raise CannotConnect from ex
    finally:
        # Important: When stopping during config flow validation, don't allow reconnect
        if hub.connected or hub._connecting:
            hub.stop(allow_reconnect=False)


class BlackmagicVideohubConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Videohub."""

    VERSION = 2  # Updated version to support the enhanced configuration model

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)

                return self.async_create_entry(title=info["title"], data=user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except NoOutputs:
                errors["base"] = "no_outputs"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )
        
    async def async_step_import(self, user_input: dict[str, Any]) -> FlowResult:
        """Import a config entry from configuration.yaml."""
        return await self.async_step_user(user_input=user_input)
        
    @staticmethod
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return BlackmagicVideohubOptionsFlow(config_entry)


class BlackmagicVideohubOptionsFlow(config_entries.OptionsFlow):
    """Handle options for the Videohub component."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Use existing values or defaults
        options = {
            vol.Optional(
                CONF_INIT_TIMEOUT,
                default=self.config_entry.options.get(CONF_INIT_TIMEOUT, DEFAULT_INIT_TIMEOUT),
            ): int,
        }

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(options),
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class NoOutputs(HomeAssistantError):
    """Error to indicate no outputs were found on the device."""