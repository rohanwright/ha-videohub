"""Support for Blackmagic Videohub sensors."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN, 
    DATA_VIDEOHUB, 
    DATA_COORDINATOR,
    STATE_CONNECTED,
    STATE_DISCONNECTED,
    ATTR_MODEL_NAME,
    ATTR_VIDEO_INPUTS,
    ATTR_VIDEO_OUTPUTS,
)

_LOGGER = logging.getLogger(__name__)

# Define the sensor entity description
CONNECTION_STATUS_DESCRIPTION = SensorEntityDescription(
    key="connection_status",
    name="Connection Status",
    device_class=SensorDeviceClass.ENUM,
    entity_category=EntityCategory.DIAGNOSTIC,
    options=[STATE_CONNECTED, STATE_DISCONNECTED],
    has_entity_name=True,
)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Blackmagic Videohub sensors."""
    data = hass.data[DOMAIN][entry.entry_id]
    hub = data[DATA_VIDEOHUB]
    coordinator = data[DATA_COORDINATOR]
    name = data["name"]

    # Add connection status sensor
    entities = [ConnectionStatusSensor(coordinator, hub, name, entry.entry_id)]
    async_add_entities(entities)

class ConnectionStatusSensor(CoordinatorEntity, SensorEntity):
    """Connection status sensor for Blackmagic Videohub."""

    entity_description = CONNECTION_STATUS_DESCRIPTION

    def __init__(self, coordinator, hub, name, entry_id):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._hub = hub
        self._name = name
        self._entry_id = entry_id
        
        # Set unique ID and device info
        self._attr_unique_id = f"{entry_id}_connection_status"
        
        # Set device info
        model = hub.model_name if hub.model_name else "Blackmagic Videohub"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}")},
            name=name,
            manufacturer="Blackmagic Design",
            model=model,
            configuration_url=f"http://{hub._cmdServer}:{hub._cmdServerPort}",
        )
        
    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        if self.coordinator.data:
            return STATE_CONNECTED if self.coordinator.data.get("connected", False) else STATE_DISCONNECTED
        return STATE_DISCONNECTED
        
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        if not self.coordinator.data or not self.coordinator.data.get("device_info"):
            return {}
            
        device_info = self.coordinator.data.get("device_info", {})
        return {
            ATTR_MODEL_NAME: device_info.get("model_name", "Unknown"),
            ATTR_VIDEO_INPUTS: device_info.get("video_inputs_count", 0),
            ATTR_VIDEO_OUTPUTS: device_info.get("video_outputs_count", 0),
        }
        
    @property
    def available(self) -> bool:
        """Sensor is always available as it reports connection status."""
        return True