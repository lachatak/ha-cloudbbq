"""Alarm tone, including silent."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import ALARM_TONES, ALARM_TONE_DEFAULT, DOMAIN
from .entity import CloudBBQEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([CloudBBQTone(hass.data[DOMAIN][entry.entry_id], entry)])


class CloudBBQTone(CloudBBQEntity, RestoreEntity, SelectEntity):
    """The device's alarm tone: Silent, or one of three tones.

    Mirrors the vendor app's mute picker, which sends 0x29 <index>.
    Selecting Silent shows the crossed-speaker icon on the device.

    The device never reports its current tone back, so the last choice is
    restored across restarts rather than read from the hardware.
    """

    _attr_name = "Alarm tone"
    _attr_icon = "mdi:music-note"
    _attr_options = list(ALARM_TONES.values())

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._address}_alarm_tone"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            if last.state in self._attr_options:
                for value, label in ALARM_TONES.items():
                    if label == last.state:
                        self.coordinator.tone = value
                        break

    @property
    def current_option(self) -> str:
        return ALARM_TONES.get(self.coordinator.tone, ALARM_TONES[ALARM_TONE_DEFAULT])

    async def async_select_option(self, option: str) -> None:
        for value, label in ALARM_TONES.items():
            if label == option:
                await self.coordinator.async_set_tone(value)
                return
