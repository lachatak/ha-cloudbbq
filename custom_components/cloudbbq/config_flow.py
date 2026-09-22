"""Config flow for the CloudBBQ thermometer."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS

from homeassistant.core import callback

from .const import CONF_PROBES, DEFAULT_PROBES, DOMAIN, MANUFACTURER_ID, PROBE_SLOTS


def _title(address: str) -> str:
    return f"BBQ Thermometer {address.replace(':', '')[-6:]}"


# Names used by genuine iBBQ/Inkbird hardware. Those units advertise an
# identical envelope -- same FFF0 service, same Nordic manufacturer ID, same
# embedded-MAC payload -- which is exactly why this integration was first
# built on the wrong protocol. They are a different device: their command set
# is iBBQ's, not DongGuan BBK's, so this integration would connect and then
# never stream. The advertisement carries nothing that positively identifies
# a CloudBBQ unit, so the best available discriminator is the name.
_FOREIGN_NAME_PREFIXES = ("IBBQ", "XBBQ")


def _is_cloudbbq(info: BluetoothServiceInfoBleak) -> bool:
    """Weed out other devices that also advertise the generic FFF0 service.

    A genuine unit carries Nordic manufacturer data holding its own MAC in
    reverse byte order at offset 2, followed by two bytes per probe slot.
    Checking the MAC matches is a much stronger signal than the service UUID,
    which plenty of unrelated cheap BLE hardware also uses.

    Deliberately permissive about the name: an advertisement that has not
    carried its name yet still passes, because refusing it would leave a real
    device undiscoverable with no way in. Only names that positively identify
    OTHER hardware are rejected. The manifest matcher is the strict half of
    this pair -- it only auto-prompts for BBQ*, while this function also
    governs the manual picker, which has to stay usable.
    """
    payload = info.manufacturer_data.get(MANUFACTURER_ID)
    if payload is None or len(payload) < 10:
        return False
    embedded = ":".join(f"{b:02X}" for b in reversed(payload[2:8]))
    if embedded != info.address.upper():
        return False
    name = (info.name or "").strip().upper()
    return not name.startswith(_FOREIGN_NAME_PREFIXES)


class CloudBBQConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovered: BluetoothServiceInfoBleak | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        if not _is_cloudbbq(discovery_info):
            return self.async_abort(reason="not_supported")

        self._discovered = discovery_info
        self.context["title_placeholders"] = {"name": _title(discovery_info.address)}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._discovered is not None
        address = self._discovered.address

        if user_input is not None:
            return self.async_create_entry(
                title=_title(address), data={CONF_ADDRESS: address}
            )

        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"name": _title(address), "address": address},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=_title(address), data={CONF_ADDRESS: address}
            )

        configured = self._async_current_ids()
        candidates = {
            info.address: f"{_title(info.address)} ({info.address})"
            for info in async_discovered_service_info(self.hass, connectable=True)
            if info.address not in configured and _is_cloudbbq(info)
        }
        if not candidates:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(candidates)}),
        )


    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return CloudBBQOptionsFlow()


class CloudBBQOptionsFlow(config_entries.OptionsFlow):
    """How many probe sockets this unit physically has."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(CONF_PROBES, DEFAULT_PROBES)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PROBES, default=current): vol.In(
                        list(range(1, PROBE_SLOTS + 1))
                    )
                }
            ),
        )
