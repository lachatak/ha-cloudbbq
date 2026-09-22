"""Hold a connection to a CloudBBQ thermometer and stream its temperatures.

Sequence, replayed byte-for-byte from the vendor app's own logcat output:
    connect -> subscribe fff1, fff3, fff5 -> startup batch to fff4
    -> the device pushes a 12-byte packet to fff5 about once a second.

The connection is kept open rather than polled: reconnecting costs about
three seconds of setup and would discard ~14 readings out of every 15.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CHAR_ACK,
    CHAR_FIRMWARE,
    CMD_BATTERY,
    CMD_SILENCE,
    ALARM_TONE_DEFAULT,
    INTERVAL_DEFAULT,
    PROBE_SLOTS,
    TARGET_DEFAULT,
    build_interval,
    build_tone,
    build_target,
    CHAR_COMMAND,
    CHAR_HISTORY,
    CHAR_REALTIME,
    FIRST_PACKET_TIMEOUT,
    MIN_PUSH_INTERVAL,
    PROBE_NOT_CONNECTED,
    REALTIME_LENGTH,
    SERVICE_UUID,
    STALE_AFTER,
    MINIMAL_TIMEOUT,
    STARTUP_MINIMAL,
    WATCHDOG_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


def _short(uuid: str) -> str:
    u = uuid.lower()
    parts = u.split("-")
    return parts[0][-4:] if len(parts) == 5 and parts[0].startswith("0000") else u


def parse_realtime(payload: bytes) -> list[float | None]:
    """Six probes, uint16 little-endian, whole degrees Celsius."""
    out: list[float | None] = []
    for index in range(len(payload) // 2):
        raw = int.from_bytes(payload[index * 2 : index * 2 + 2], "little")
        out.append(None if raw == PROBE_NOT_CONNECTED else float(raw))
    return out


class CloudBBQCoordinator(DataUpdateCoordinator[list[float | None]]):
    """Keeps one BLE connection open and pushes every notification through."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, address: str, name: str
    ) -> None:
        # config_entry is passed explicitly: Home Assistant deprecated the
        # implicit context lookup, and having it here is also what lets the
        # firmware update below scope its device lookup to this entry.
        super().__init__(
            hass, _LOGGER, name=name, config_entry=entry,
            update_interval=timedelta(seconds=WATCHDOG_INTERVAL),
        )
        self.address = address
        self.battery: int | None = None
        self.firmware: str | None = None
        # False hands the single connection slot to the phone app. The
        # watchdog then stops reconnecting, so this survives as long as the
        # user leaves it off -- it is not a one-shot disconnect.
        self.link_enabled = True
        # Per-probe alert state, probe number (1-based) -> (enabled, target C).
        self.alerts: dict[int, tuple[bool, int]] = {}
        self.tone = ALARM_TONE_DEFAULT
        self.interval = INTERVAL_DEFAULT
        self._needs_full_batch = False
        self._failures = 0
        # Deliberately NOT called _retry_after: DataUpdateCoordinator already
        # owns an attribute by that name and resets it to None in
        # _schedule_refresh after every refresh. Shadowing it meant our float
        # silently turned into None, and `now < None` then raised on every
        # reconnect attempt -- the integration could not recover from a
        # power-cycle without restarting Home Assistant.
        self._backoff_until = 0.0
        self._client: BleakClientWithServiceCache | None = None
        self._lock = asyncio.Lock()
        self._last_packet = 0.0
        self._last_push = 0.0
        self._first_packet = asyncio.Event()

    # ---- lifecycle ----------------------------------------------------

    async def async_disconnect(self) -> None:
        async with self._lock:
            client, self._client = self._client, None
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:  # noqa: BLE001
                    pass

    def _on_disconnected(self, _client) -> None:
        _LOGGER.debug("disconnected from %s", self.address)
        self._client = None

    # ---- the watchdog -------------------------------------------------

    async def _async_update_data(self) -> list[float | None]:
        """Only reconnects; real updates arrive via notifications."""
        if not self.link_enabled:
            # Deliberately released. Returning the last data (rather than
            # raising) keeps this out of the error log; entities show as
            # unavailable via CloudBBQEntity.available instead.
            return self.data or []

        if self._client is None or not self._client.is_connected:
            try:
                await self._async_connect()
            except UpdateFailed:
                raise
            except Exception as err:  # noqa: BLE001
                # A device that is switched off, out of range, or held by the
                # phone app raises BleakNotFoundError / TimeoutError here.
                # That is an ordinary unavailable state, not a crash, so it
                # must not escape as an "Unexpected error" with a traceback.
                raise UpdateFailed(f"cannot reach the thermometer: {err}") from err

        if self._client is not None and self._client.is_connected:
            try:
                await self._async_write(CMD_BATTERY)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("battery poll failed: %s", err)

        age = time.monotonic() - self._last_packet
        if self._last_packet and age > STALE_AFTER:
            await self.async_disconnect()
            raise UpdateFailed(f"no packet for {age:.0f}s; reconnecting")

        if self.data is None:
            raise UpdateFailed("connected but no packet yet")
        return self.data

    async def _async_connect(self) -> None:
        async with self._lock:
            if self._client is not None and self._client.is_connected:
                return
            if not self.link_enabled:
                raise UpdateFailed(
                    "the connection is switched off; turn the Connection "
                    "switch back on to let Home Assistant use the thermometer"
                )

            # Exponential backoff. Without this, a device that accepts a
            # connection and immediately drops it produces a reconnect storm
            # -- roughly one attempt per second, forever.
            now = time.monotonic()
            if now < self._backoff_until:
                raise UpdateFailed(
                    f"backing off after {self._failures} failed attempts; "
                    f"retrying in {self._backoff_until - now:.0f}s"
                )

            device = bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            if device is None:
                raise UpdateFailed(f"{self.address} is not in range")

            started = time.monotonic()
            try:
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    device,
                    self.address,
                    disconnected_callback=self._on_disconnected,
                )
            except Exception:
                self._failures += 1
                delay = min(5 * 2 ** (self._failures - 1), 300)
                self._backoff_until = time.monotonic() + delay
                _LOGGER.debug(
                    "link failed (%d in a row); next attempt in %ds",
                    self._failures, delay,
                )
                raise
            linked = time.monotonic()
            try:
                await self._async_start_stream(client)
            except Exception:
                await client.disconnect()
                self._failures += 1
                delay = min(5 * 2 ** (self._failures - 1), 300)
                self._backoff_until = time.monotonic() + delay
                _LOGGER.debug(
                    "startup failed (%d in a row); next attempt in %ds",
                    self._failures, delay,
                )
                raise
            self._client = client
            self._failures = 0
            self._backoff_until = 0.0
            await self._async_read_firmware(client)
            _LOGGER.info(
                "CloudBBQ ready in %.1fs (link %.1fs, stream %.1fs)",
                time.monotonic() - started,
                linked - started,
                time.monotonic() - linked,
            )

    async def _async_read_firmware(self, client) -> None:
        """Read the standard Firmware Revision string (0x2A26), once.

        Purely cosmetic -- it fills in the version on the device page. A unit
        that does not expose the characteristic is not a problem, so every
        failure here is swallowed rather than allowed to abort a connection
        that is otherwise working.
        """
        if self.firmware is not None:
            return
        try:
            raw = await client.read_gatt_char(CHAR_FIRMWARE)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("firmware revision unavailable: %s", err)
            return
        version = raw.decode("utf-8", "replace").strip("\x00").strip()
        if not version:
            return
        self.firmware = version
        _LOGGER.debug("firmware revision %s", version)

        # NOT async_get_device(connections=...): deprecated, and removed in
        # 2027.8, because identifiers and connections are no longer unique
        # across config entries. Its replacement takes the entry id, which is
        # what disambiguates them. The address is passed verbatim -- the
        # registry only runs format_mac over CONNECTION_NETWORK_MAC, so a
        # CONNECTION_BLUETOOTH value is stored exactly as we wrote it.
        registry = dr.async_get(self.hass)
        device = registry.async_get_device_by_connection(
            (CONNECTION_BLUETOOTH, self.address),
            self.config_entry.entry_id,
        )
        if device is None:
            _LOGGER.debug("no device registry entry yet; skipping sw_version")
            return
        registry.async_update_device(device.id, sw_version=version)

    @staticmethod
    def _find_service(client):
        return next(
            (s for s in client.services if _short(s.uuid) == _short(SERVICE_UUID)),
            None,
        )

    def _full_batch(self) -> tuple[bytes, ...]:
        """The sequence that reliably starts the stream on this firmware.

        Built from CURRENT state rather than the hardcoded values captured
        from the app: these are all settings writes, so replaying a snapshot
        on every reconnect would silently undo whatever the user had set
        through the alert and interval entities.
        """
        commands: list[bytes] = [build_interval(self.interval)]
        for probe in range(1, PROBE_SLOTS + 1):
            enabled, target = self.alerts.get(probe, (False, TARGET_DEFAULT))
            commands.append(build_target(probe, target if enabled else None))
        commands.append(CMD_BATTERY)
        commands.append(build_tone(self.tone))
        return tuple(commands)

    async def _async_start_stream(self, client) -> None:
        # A freshly connected client occasionally reports an empty GATT table
        # for a moment, so give it one retry before blaming the cache.
        service = self._find_service(client)
        if service is None:
            await asyncio.sleep(1.0)
            service = self._find_service(client)
        if service is None:
            found = sorted({_short(s.uuid) for s in client.services})
            await client.clear_cache()
            raise UpdateFailed(
                f"fff0 service not found (saw {found or 'nothing'}); cache "
                "cleared, will rediscover on the next attempt"
            )

        chars = {_short(c.uuid): c for c in service.characteristics}
        missing = [
            _short(u) for u in (CHAR_ACK, CHAR_HISTORY, CHAR_REALTIME, CHAR_COMMAND)
            if _short(u) not in chars
        ]
        if missing:
            await client.clear_cache()
            raise UpdateFailed(f"missing {missing}; cache cleared")

        self._first_packet.clear()

        # App order, paced: back-to-back CCCD writes are not acknowledged
        # individually by this firmware.
        for uuid, handler in (
            (CHAR_ACK, self._on_ack),
            (CHAR_HISTORY, None),
            (CHAR_REALTIME, self._on_realtime),
        ):
            await client.start_notify(
                chars[_short(uuid)],
                handler if handler else (lambda _c, _d: None),
            )

        command_char = chars[_short(CHAR_COMMAND)]

        async def send(commands) -> None:
            for command in commands:
                await client.write_gatt_char(command_char, command, response=True)
                await asyncio.sleep(0.05)

        # Try the harmless commands first and only escalate if the device
        # stays quiet, so a normal reconnect never touches user settings.
        if self._needs_full_batch:
            # Learned on a previous connect: skip straight to what works.
            await send(self._full_batch())
            await asyncio.wait_for(self._first_packet.wait(), FIRST_PACKET_TIMEOUT)
            return

        await send(STARTUP_MINIMAL)
        try:
            await asyncio.wait_for(self._first_packet.wait(), MINIMAL_TIMEOUT)
            _LOGGER.debug("stream started with the minimal batch")
            return
        except TimeoutError:
            _LOGGER.debug("minimal batch did not start the stream; escalating")
            self._needs_full_batch = True

        await send(self._full_batch())
        await asyncio.wait_for(self._first_packet.wait(), FIRST_PACKET_TIMEOUT)
        _LOGGER.info(
            "CloudBBQ needed the full startup batch (this rewrites alarm "
            "volume and probe targets)"
        )

    # ---- outgoing commands --------------------------------------------

    async def _async_write(self, command: bytes) -> None:
        if not self.link_enabled:
            # Distinct from "cannot reach it": nothing is wrong, the user
            # handed the device to the phone on purpose.
            raise HomeAssistantError(
                "The connection is switched off - turn the Connection switch "
                "back on to let Home Assistant use the thermometer"
            )
        # Connect on demand: a button press should not fail just because the
        # link happens to be down at that moment.
        if self._client is None or not self._client.is_connected:
            _LOGGER.debug("command needs a connection; connecting")
            try:
                await self._async_connect()
            except Exception as err:  # noqa: BLE001
                raise HomeAssistantError(
                    "Cannot reach the thermometer - is it switched on, in "
                    "range, and not connected to the phone app?"
                ) from err
        client = self._client
        if client is None or not client.is_connected:
            raise HomeAssistantError(
                "Cannot reach the thermometer - is it switched on and in range, "
                "and not connected to the phone app?"
            )
        service = next(
            (s for s in client.services if _short(s.uuid) == _short(SERVICE_UUID)),
            None,
        )
        if service is None:
            raise HomeAssistantError("The fff0 service is not available")
        char = next(
            (c for c in service.characteristics
             if _short(c.uuid) == _short(CHAR_COMMAND)),
            None,
        )
        if char is None:
            raise HomeAssistantError("Command characteristic not found")
        await client.write_gatt_char(char, command, response=True)
        _LOGGER.debug("sent %s", command.hex(" "))

    async def async_silence(self) -> None:
        """Stop a sounding alarm."""
        await self._async_write(CMD_SILENCE)

    async def async_set_link(self, enabled: bool) -> None:
        """Take or release the thermometer's single connection slot.

        The device accepts one connection at a time, so while Home Assistant
        holds it the phone app cannot connect, and vice versa. Turning this
        off drops the link and stops the watchdog reconnecting, which is what
        makes the handover stick.
        """
        if enabled == self.link_enabled:
            return
        self.link_enabled = enabled
        if enabled:
            # Start from a clean slate: any backoff earned while the phone
            # held the device is meaningless now.
            self._failures = 0
            self._backoff_until = 0.0
            _LOGGER.debug("connection enabled; reconnecting")
            await self.async_request_refresh()
        else:
            _LOGGER.debug("connection released for the phone app")
            await self.async_disconnect()
        self.async_update_listeners()

    async def async_set_interval(self, minutes: int) -> None:
        """Alarm repeat interval in minutes. 0 shows the crossed speaker."""
        command = build_interval(minutes)
        _LOGGER.debug("alarm interval -> %dmin (%s)", minutes, command.hex(" "))
        await self._async_write(command)
        self.interval = minutes
        self.async_update_listeners()

    async def async_set_tone(self, tone: int) -> None:
        """Set the device's alarm tone. 0 is silent (crossed-speaker icon).

        This, not 0x23's volume byte, is the device's mute control -- the
        firmware appears to ignore 0x23 entirely.
        """
        command = build_tone(tone)
        _LOGGER.debug("alarm tone -> %d (%s)", tone, command.hex(" "))
        await self._async_write(command)
        self.tone = tone
        self.async_update_listeners()

    async def async_set_alert(
        self, probe: int, enabled: bool, target: int
    ) -> None:
        """Arm or clear the high-temperature alert for a probe (1-based)."""
        await self._async_write(build_target(probe, target if enabled else None))
        self.alerts[probe] = (enabled, target)
        self.async_update_listeners()

    # ---- notifications ------------------------------------------------

    def _on_ack(self, _char, payload: bytearray) -> None:
        """Command replies on fff1: <cmd> <args...> <cmd>."""
        data = bytes(payload)
        if len(data) != 8 or data[0] != data[7]:
            return

        changed = False

        if data[0] == 0x24:                      # battery reply
            if self.battery != data[1]:
                self.battery = data[1]
                changed = True

        elif data[0] == 0x23:                    # settings echo
            # Byte 2 is the buzzer volume; 0 means muted. Taking the state
            # from the device's own reply means the switch reflects reality
            # rather than what we last asked for.
            if self.interval != data[2]:
                self.interval = data[2]
                changed = True
            _LOGGER.debug("settings echo: interval=%d min", data[2])

        if changed:
            self.async_update_listeners()

    def _on_realtime(self, _char, payload: bytearray) -> None:
        data = bytes(payload)
        if len(data) != REALTIME_LENGTH:
            return

        now = time.monotonic()
        self._last_packet = now
        self._first_packet.set()

        values = parse_realtime(data)
        # The device repeats itself every second; only write state when
        # something changed, or occasionally to keep the data fresh.
        if values != self.data or now - self._last_push >= MIN_PUSH_INTERVAL:
            self._last_push = now
            self.async_set_updated_data(values)
