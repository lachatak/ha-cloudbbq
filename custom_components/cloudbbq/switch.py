"""Per-probe alert enable, plus the connection hand-over switch."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CONF_PROBES, DEFAULT_PROBES, DOMAIN, TARGET_DEFAULT
from .entity import CloudBBQEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = [
        CloudBBQAlert(coordinator, entry, probe)
        for probe in range(1, entry.options.get(CONF_PROBES, DEFAULT_PROBES) + 1)
    ]
    entities.append(CloudBBQConnection(coordinator, entry))
    async_add_entities(entities)


class CloudBBQAlert(CloudBBQEntity, RestoreEntity, SwitchEntity):
    """Arms or clears the high-temperature alert for one probe."""

    _attr_icon = "mdi:bell-alert"

    def __init__(self, coordinator, entry, probe: int) -> None:
        super().__init__(coordinator, entry)
        self._probe = probe
        self._attr_name = f"Probe {probe} alert"
        self._attr_unique_id = f"{self._address}_probe_{probe}_alert"
        self._enabled = False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._enabled = last.state == "on"
        _, target = self.coordinator.alerts.get(self._probe, (False, TARGET_DEFAULT))
        self.coordinator.alerts[self._probe] = (self._enabled, target)

    @property
    def is_on(self) -> bool:
        return self._enabled

    async def _apply(self, enabled: bool) -> None:
        _, target = self.coordinator.alerts.get(self._probe, (False, TARGET_DEFAULT))
        await self.coordinator.async_set_alert(self._probe, enabled, int(target))
        self._enabled = enabled
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._apply(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._apply(False)


class CloudBBQConnection(CloudBBQEntity, RestoreEntity, SwitchEntity):
    """Hands the thermometer's single connection slot to the phone app.

    The device accepts exactly one BLE connection, so Home Assistant and the
    CloudBBQ app cannot both hold it. Turning this off releases the device
    for a cook you want to run from the phone, without disabling the whole
    integration and losing the entity history; turning it back on reclaims
    it. There is nothing to unregister on either side -- no bonding and no
    login is involved -- so the hand-over is just the link going away.
    """

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "Connection"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._address}_connection"

    @property
    def available(self) -> bool:
        """Always operable.

        Deliberately bypasses CloudBBQEntity.available: this is the one
        entity that must still work while the link is off, or there would be
        no way to turn it back on.
        """
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.link_enabled

    @property
    def icon(self) -> str:
        return "mdi:bluetooth-connect" if self.is_on else "mdi:bluetooth-off"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Restore an off state, so a hand-over survives a restart. Only "off"
        # is acted on: the default is to hold the link, and a fresh install
        # with no stored state must not start disconnected.
        last = await self.async_get_last_state()
        if last is not None and last.state == "off":
            await self.coordinator.async_set_link(False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_link(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_link(False)
        self.async_write_ha_state()
