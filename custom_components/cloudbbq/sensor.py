"""Probe temperature sensors."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_PROBES, DEFAULT_PROBES, DOMAIN
from .coordinator import CloudBBQCoordinator
from .entity import CloudBBQEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CloudBBQCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        CloudBBQProbe(coordinator, entry, index)
        for index in range(entry.options.get(CONF_PROBES, DEFAULT_PROBES))
    ]
    entities.append(CloudBBQBattery(coordinator, entry))
    async_add_entities(entities)


class CloudBBQBattery(CloudBBQEntity, SensorEntity):
    """Battery level, from the device's reply to the 0x24 command."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Battery"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._address}_battery"

    @property
    def native_value(self) -> int | None:
        return self.coordinator.battery


class CloudBBQProbe(CloudBBQEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CloudBBQCoordinator,
        entry: ConfigEntry,
        index: int,
    ) -> None:
        super().__init__(coordinator, entry)
        self._index = index
        self._attr_name = f"Probe {index + 1}"
        self._attr_unique_id = f"{self._address}_probe_{index + 1}"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        if not data or self._index >= len(data):
            return None
        return data[self._index]

    # No `available` override HERE on purpose -- CloudBBQEntity owns that
    # rule for every platform. What matters is that an empty probe socket
    # returns None ("Unknown") rather than marking the entity unavailable:
    # conflating the two made a working integration with no probes attached
    # look identical to a completely broken one.
