"""Per-probe alert target temperature."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_PROBES,
    DEFAULT_PROBES,
    DOMAIN,
    INTERVAL_MAX,
    INTERVAL_MIN,
    INTERVAL_STEP,
    TARGET_DEFAULT,
    TARGET_MAX,
    TARGET_MIN,
)
from .entity import CloudBBQEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = [
        CloudBBQTarget(coordinator, entry, probe)
        for probe in range(1, entry.options.get(CONF_PROBES, DEFAULT_PROBES) + 1)
    ]
    entities.append(CloudBBQInterval(coordinator, entry))
    async_add_entities(entities)


class CloudBBQInterval(CloudBBQEntity, NumberEntity):
    """How often a triggered alarm re-sounds, in minutes.

    Setting this to 0 silences the device and shows the crossed-speaker icon
    -- the vendor app only offers multiples of 5, so 0 is undocumented but
    is what the firmware treats as "never repeat".
    """

    _attr_name = "Alarm interval"
    _attr_native_unit_of_measurement = "min"
    _attr_native_min_value = INTERVAL_MIN
    _attr_native_max_value = INTERVAL_MAX
    _attr_native_step = INTERVAL_STEP
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:bell-ring"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._address}_alarm_interval"

    @property
    def native_value(self) -> float:
        return float(self.coordinator.interval)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_interval(int(value))


class CloudBBQTarget(CloudBBQEntity, RestoreNumber, NumberEntity):
    """The temperature at which this probe's alert fires."""

    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = TARGET_MIN
    _attr_native_max_value = TARGET_MAX
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_entity_category = None
    _attr_icon = "mdi:thermometer-alert"

    def __init__(self, coordinator, entry, probe: int) -> None:
        super().__init__(coordinator, entry)
        self._probe = probe
        self._attr_name = f"Probe {probe} alert target"
        self._attr_unique_id = f"{self._address}_probe_{probe}_target"
        self._value = float(TARGET_DEFAULT)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (restored := await self.async_get_last_number_data()) is not None:
            if restored.native_value is not None:
                self._value = restored.native_value
        enabled, _ = self.coordinator.alerts.get(self._probe, (False, 0))
        self.coordinator.alerts[self._probe] = (enabled, int(self._value))

    @property
    def native_value(self) -> float:
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        self._value = value
        enabled, _ = self.coordinator.alerts.get(self._probe, (False, 0))
        # Only push to the device if this probe's alert is actually armed.
        if enabled:
            await self.coordinator.async_set_alert(self._probe, True, int(value))
        else:
            self.coordinator.alerts[self._probe] = (False, int(value))
        self.async_write_ha_state()
