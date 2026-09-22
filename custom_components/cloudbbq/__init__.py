"""CloudBBQ Bluetooth meat thermometer (DongGuan BBK protocol, not iBBQ)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import CloudBBQCoordinator

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = CloudBBQCoordinator(
        hass, entry, entry.data[CONF_ADDRESS], entry.title
    )

    # Do NOT await the first refresh. A poll that walks several login
    # variants can take over a minute, and awaiting it here blocked Home
    # Assistant's startup for that long ("Waiting for integrations to
    # complete setup"). Kick it off in the background instead: setup returns
    # immediately, entities appear unavailable, and the first poll fills them.
    entry.async_create_background_task(
        hass, coordinator.async_refresh(), "cloudbbq-first-refresh"
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_disconnect()
    return unloaded
