"""Support for Blackmagic Videohub outputs as Select entities."""
from __future__ import annotations

import logging
from typing import Any, Final, List

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, DATA_VIDEOHUB, DATA_COORDINATOR

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Blackmagic Videohub select entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    hub = data[DATA_VIDEOHUB]
    coordinator = data[DATA_COORDINATOR]
    device_name = data["name"]

    # Wait for the coordinator to get initial data
    await coordinator.async_refresh()

    entities = []

    # Get outputs from the hub - will be in one-based ID format for external use
    outputs = hub.get_outputs()
    
    for output_id, output_data in outputs.items():
        _LOGGER.debug("Creating entity for output %d: %s", output_id, output_data.get("label", "Unknown"))
        entities.append(
            BlackmagicVideohubOutputSelect(
                coordinator,
                hub,
                device_name,
                entry.entry_id,
                output_id,
                output_data
            )
        )

    if entities:
        _LOGGER.info("Adding %d Blackmagic Videohub output entities", len(entities))
        async_add_entities(entities)
    else:
        _LOGGER.warning("No outputs found to create entities")


class BlackmagicVideohubOutputSelect(CoordinatorEntity, SelectEntity):
    """Representation of a Blackmagic Videohub output as a select entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, hub, device_name, entry_id, output_id, output_data):
        """Initialize the Blackmagic Videohub output."""
        super().__init__(coordinator)
        
        self._hub = hub
        self._device_name = device_name
        self._entry_id = entry_id
        self._output_id = output_id  # This is one-based
        safe_device_name = device_name.lower().replace(' ', '_')

        # Get output information
        self._output_label = output_data.get("label", f"Output {output_id:03d}")
        self._current_input_id = output_data.get("input", 1)  # one-based
        
        # Set entity attributes
        self._attr_unique_id = f"{entry_id}_{safe_device_name}_output_{output_id:03d}"

        # Explicitly set the entity_id (without the label)
        self.entity_id = f"select.{safe_device_name}_output_{output_id:03d}"

        # Just use Output ID and label since device name will be prepended automatically
        self._attr_name = f"Output {output_id:03d} - {self._output_label}"
        # Explicitly set friendly name for device view
        self._attr_translation_key = None  # Ensure no translation key is used
        self._attr_friendly_name = self._attr_name
        
        # Initialize options list
        self._update_options()
        
        # Set device info
        model = hub.model_name if hub.model_name else "Blackmagic Videohub"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}")},
            name=device_name,
            manufacturer="Blackmagic Design",
            model=model,
        )
        


    def _update_options(self) -> None:
        """Update the list of input options."""
        # Get all inputs (1-based)
        inputs = self._hub.get_inputs()
        
        options = []
        for input_id, input_data in inputs.items():
            # Add the option with the Home Assistant naming format
            options.append(input_data["ha_name"])
        
        # Sort options by input number
        options.sort(key=lambda x: int(x.split(' - ')[0].replace('Input ', '')))
        
        self._attr_options = options
        
        # Update current option
        self._update_current_option()

    def _update_current_option(self) -> None:
        """Update the currently selected option based on routing state."""
        current_input_id = self._current_input_id  # one-based
        
        # Get the input data
        inputs = self._hub.get_inputs()
        if current_input_id in inputs:
            self._attr_current_option = inputs[current_input_id]["ha_name"]
        else:
            # Fallback option if input is not found
            self._attr_current_option = f"Input {current_input_id} - Unknown"

    async def async_select_option(self, option: str) -> None:
        """Change the selected input."""
        _LOGGER.debug("Selected option for output %d: %s", self._output_id, option)
        
        success = self._hub.set_input_by_name(self._output_id, option)
        if success:
            # Update current option immediately for responsiveness
            self._attr_current_option = option
            self.async_write_ha_state()
    
    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Only available if the hub is connected and initialized
        if not self.coordinator.data:
            return False
        return self.coordinator.data.get("connected", False) and self.coordinator.data.get("initialized", False)

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not self.coordinator.data:
            self._attr_available = False
            self.async_write_ha_state()
            return
            
        # Update availability
        self._attr_available = (
            self.coordinator.data.get("connected", False) and 
            self.coordinator.data.get("initialized", False)
        )
        
        # Get updated outputs
        outputs = self.coordinator.data.get("outputs", {})
        if not outputs or self._output_id not in outputs:
            self.async_write_ha_state()
            return
            
        # Get this output's data
        output_data = outputs[self._output_id]
        
        # Update output label if changed
        if "label" in output_data and output_data["label"] != self._output_label:
            self._output_label = output_data["label"]
            # Set both name and friendly_name
            self._attr_name = f"Output {self._output_id:03d} - {self._output_label}"
            self._attr_friendly_name = self._attr_name
            
        # Update current input if changed
        if "input" in output_data and output_data["input"] != self._current_input_id:
            self._current_input_id = output_data["input"]
            
        # Always update options when coordinator refreshes to catch input name changes
        self._update_options()
        
        # Update entity state
        self.async_write_ha_state()