"""Constants for the CloudBBQ thermometer integration.

Protocol recovered by decompiling the vendor's own Android app,
com.bobwen.ble.cloudbbq (class utils/TempMonitorManager), 2026-09-22.
This device is NOT iBBQ despite advertising the iBBQ service UUID and
manufacturer-data layout -- it is DongGuan BBK's own firmware and shares
none of iBBQ's commands.
"""

DOMAIN = "cloudbbq"

MANUFACTURER_ID = 0x0059  # Nordic, in the advertisement
SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"

CHAR_ACK = "0000fff1-0000-1000-8000-00805f9b34fb"       # notify: command ACKs
CHAR_LOGIN = "0000fff2-0000-1000-8000-00805f9b34fb"     # write:  login
CHAR_HISTORY = "0000fff3-0000-1000-8000-00805f9b34fb"   # notify: history
CHAR_COMMAND = "0000fff4-0000-1000-8000-00805f9b34fb"   # write:  commands
CHAR_REALTIME = "0000fff5-0000-1000-8000-00805f9b34fb"  # notify: temperatures

# Device Information; the app reads the firmware revision during startup.
CHAR_FIRMWARE = "00002a26-0000-1000-8000-00805f9b34fb"

# Commands are 8 bytes: <cmd> <args...> <cmd>. The command byte is repeated
# as a trailer, and the app rejects any reply whose first and last bytes
# differ.
#
# The app writes this login (PIN 654321 as BCD) to fff2 before anything else.
# Kept here as documentation only: this integration never sends it, and the
# device streams perfectly well without it. There is no pairing or bonding
# either, which is why handing the device between Home Assistant and the
# phone needs nothing but dropping the link.
LOGIN = bytes([0x21, 0x65, 0x43, 0x21, 0x00, 0x00, 0x00, 0x21])

# Startup commands, from a byte-for-byte reading of the vendor app's logcat
# during a working session. The app sends all of these and the stream starts
# ~0.3s after the last one.
#
# They are split because most of them WRITE SETTINGS, and an integration that
# reconnects all day must not keep overwriting the user's alarm volume and
# probe targets. So: send the harmless ones first, and only fall back to the
# full sequence if the device doesn't start streaming.
#
# Probe indices are 1..6, not 0..5.
CMD_BATTERY = bytes([0x24, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x24])
CMD_ALARM_MODE = bytes([0x29, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x29])  # tone B

# Silence an alarm that is CURRENTLY SOUNDING. The app sends this from its
# alarm popup. It is momentary -- with nothing sounding it does nothing.
CMD_SILENCE = bytes([0x25, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x25])

# Alarm tone selector: 0x29 <tone> ... 0x29
#
# From the app's SettingActivity.selectMute(i) -> d0((byte) i) -> 0x29 <i>,
# and initValues() which labels the stored value:
#     0 = mute_type_disable   (no sound -- the crossed-speaker icon)
#     1 = mute_type_a         2 = mute_type_b   3 = mute_type_c
# The app previews the tone after selecting anything other than 0.
#
# This, NOT 0x23's volume byte, is what muted the device by accident earlier:
# that run sent 29 00. This firmware appears to ignore the 0x23 volume byte
# entirely.
ALARM_TONE_MUTE = 0
ALARM_TONE_DEFAULT = 2  # tone B, this unit's stored setting

# The four choices the app's mute picker offers, in device order.
ALARM_TONES = {
    0: "Silent",
    1: "Tone A",
    2: "Tone B",
    3: "Tone C",
}


# Device settings: 0x23 <display in F?> <alarm repeat interval, minutes> ...
#
# Byte 2 is the ALARM REPEAT INTERVAL, not the volume as first assumed:
#     selectAlarmInterval(i) -> j.i((i + 1) * 5) -> o0() -> 0x23 <..> <minutes>
#     initValues() -> mtvAlarmInterval.setText(j.a() + " minutes")
# The app only offers multiples of 5. Sending 0 -- which its UI never does --
# is what put the crossed-speaker icon on the display.
#
# Byte 1 is the unit shown on the device's own screen; this unit is Celsius
# (0), so leave it alone.
INTERVAL_DEFAULT = 5
INTERVAL_MIN = 0
INTERVAL_MAX = 60
INTERVAL_STEP = 5


def build_interval(minutes: int) -> bytes:
    return bytes([0x23, 0x00, minutes & 0xFF, 0x00, 0x00, 0x00, 0x00, 0x23])


def build_tone(tone: int) -> bytes:
    return bytes([0x29, tone, 0x00, 0x00, 0x00, 0x00, 0x00, 0x29])


# Per-probe high-temperature alert, in whole degrees Celsius.
TARGET_MIN = 0
TARGET_MAX = 300
TARGET_DEFAULT = 90


def build_target(probe: int, target_c: int | None) -> bytes:
    """probe is 1-based; target_c in degrees Celsius, None clears the alert.

    ⚠ Targets go to the device in FAHRENHEIT even though readings come back
    in Celsius. From the app:

        j0(int i, float f) { if (deviceType >= 1) f = j.b(false, f); l0(i, f); }

    where b(false, f) is f * 1.8 + 32. This unit's stored targets were 0xA5
    (165) and 0xB4 (180), i.e. 74 C and 82 C -- sensible meat temperatures,
    not 165 C. Sending Celsius directly makes every alert fire immediately,
    because e.g. 25 is read as 25 F = -4 C.
    """
    if target_c is None:
        return bytes([0x22, probe, 0x00, 0x00, 0x00, 0x00, 0x00, 0x22])
    target_f = round(target_c * 1.8 + 32)
    return bytes(
        [0x22, probe, 0x01, target_f & 0xFF, (target_f >> 8) & 0xFF, 0x00, 0x00, 0x22]
    )

# Harmless: a battery query and the alarm-mode command the app sends last.
STARTUP_MINIMAL = (CMD_BATTERY, CMD_ALARM_MODE)

# There is no hardcoded full batch. The fallback sequence is built at
# runtime by CloudBBQCoordinator._full_batch() from the CURRENT interval,
# tone and per-probe targets -- replaying the snapshot captured from the app
# would silently undo the user's settings on every reconnect.

# How long to give the minimal batch before falling back to the full one.
MINIMAL_TIMEOUT = 4

# fff5 pushes 12 bytes: six probes, uint16 little-endian, WHOLE degrees
# Celsius (the app's formatter rounds the raw value and never divides).
# The device always reports Celsius; the unit setting only changes its own
# display. 65526 (0xFFF6) means nothing is plugged into that socket -- the
# same sentinel frozen in the advertisement.
REALTIME_LENGTH = 12

# The packet always carries six slots, but most units have fewer physical
# sockets. Entities are created for the configured count only.
PROBE_SLOTS = 6
CONF_PROBES = "probes"
DEFAULT_PROBES = 4
PROBE_NOT_CONNECTED = 65526

# The device pushes a packet about once a second while connected, so the
# connection is held open and every notification updates the sensors. The
# interval below is only a watchdog that reconnects if the link drops.
WATCHDOG_INTERVAL = 30

# How long to wait for the first packet after running the startup batch.
FIRST_PACKET_TIMEOUT = 20

# Treat the data as stale if nothing has arrived for this long.
STALE_AFTER = 90

# Don't write a state update more often than this unless a value changed.
MIN_PUSH_INTERVAL = 2
