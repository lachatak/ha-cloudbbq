"""Silence button."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import CloudBBQEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([CloudBBQSilence(hass.data[DOMAIN][entry.entry_id], entry)])


class CloudBBQSilence(CloudBBQEntity, ButtonEntity):
    """Silences the device's buzzer (command 0x25).

    A button rather than a switch because the device gives us no way back:
    0x25 silences, and nothing we can send un-silences it -- not even 0x23
    with a non-zero volume byte, which is what the vendor app sends from its
    settings screen. The device appears to clear the state itself. Modelling
    this as a switch would mean showing a state we cannot read or reverse.
    """

    _attr_name = "Silence"
    _attr_icon = "mdi:bell-off"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._address}_silence"

    async def async_press(self) -> None:
        await self.coordinator.async_silence()
