"""Shared device info for all CloudBBQ entities.

Every platform builds its DeviceInfo from `build_device_info` here. Three
separate copies of the dict used to exist and they had already drifted apart
-- one still claimed "iBBQ 6-channel", a leftover from the days when this
integration was built on the wrong protocol. One builder, one truth.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_PROBES, DEFAULT_PROBES
from .coordinator import CloudBBQCoordinator

# The hardware is built by DongGuan BBK Electronic Technology (FCC ID
# 2ARBOCLOUDBBQ) and resold under several names; CloudBBQ is the brand on
# the unit and its app, which is what the owner recognises, so that is what
# goes in the device registry.
MANUFACTURER = "CloudBBQ"


def build_device_info(
    entry: ConfigEntry,
    coordinator: CloudBBQCoordinator | None = None,
) -> DeviceInfo:
    """Describe the thermometer as it is actually configured.

    The probe count comes from the options flow rather than the six slots the
    wire protocol always carries, so a four-probe unit is not advertised as a
    six-channel one.
    """
    probes = entry.options.get(CONF_PROBES, DEFAULT_PROBES)
    info = DeviceInfo(
        connections={(CONNECTION_BLUETOOTH, entry.data[CONF_ADDRESS])},
        name=entry.title,
        manufacturer=MANUFACTURER,
        model=f"{probes}-probe Bluetooth thermometer",
    )
    # Only set once the firmware revision has actually been read; passing
    # None here would blank a version the registry already holds.
    firmware = getattr(coordinator, "firmware", None)
    if firmware:
        info["sw_version"] = firmware
    return info


class CloudBBQEntity(CoordinatorEntity[CloudBBQCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: CloudBBQCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._address = entry.data[CONF_ADDRESS]
        self._attr_device_info = build_device_info(entry, coordinator)

    @property
    def available(self) -> bool:
        """Unavailable while the connection is deliberately switched off.

        Three states have to stay distinguishable here, and the first version
        of this integration conflated them:
          * a probe reading None  -> empty socket, shown as "Unknown";
          * the link having failed -> shown as unavailable by the coordinator;
          * the link released on purpose -> also unavailable, but with no
            error in the log, because nothing is wrong.
        The Connection switch overrides this: it must stay operable in order
        to turn the link back on.
        """
        return self.coordinator.link_enabled and super().available
