# CloudBBQ Thermometer for Home Assistant

Home Assistant integration for CloudBBQ Bluetooth meat thermometers — the
units sold under the **CloudBBQ** brand and driven by the
`com.bobwen.ble.cloudbbq` Android app. Temperatures stream over BLE straight
into Home Assistant, with per-probe alerts, alarm tone and interval control,
battery level, and a switch to hand the device back to your phone.

No cloud, no account, no vendor bridge. Everything happens over a local
Bluetooth connection.

> **This is not an iBBQ integration.** These units advertise the same FFF0
> service UUID and the same Nordic manufacturer-data layout as iBBQ/Inkbird
> hardware, but the firmware underneath is DongGuan BBK's and shares *none*
> of iBBQ's commands. If you have genuine iBBQ hardware, this integration will
> connect and then never receive a reading. See [Protocol](#protocol).

## Features

- Live probe temperatures, pushed about once a second
- Per-probe high-temperature alerts with a target you set in °C
- Alarm tone (Silent / A / B / C) and alarm repeat interval
- Silence a sounding alarm
- Battery level
- Firmware revision on the device page
- **Connection switch** — release the thermometer for the phone app without
  disabling the integration
- Configurable probe count, so a 4-probe unit isn't shown as a 6-probe one

## Requirements

- Home Assistant **2026.8.0** or newer
- A working Bluetooth adapter, or — strongly recommended — an
  [ESPHome Bluetooth Proxy](https://esphome.io/projects/?type=bluetooth)

### On Bluetooth reliability

The thermometer accepts **exactly one connection at a time**. If the CloudBBQ
app on your phone is in range and remembers the device, it will take that slot
and Home Assistant will sit there retrying. Either use the Connection switch,
or "forget" the device in the phone app.

Built-in adapters on a Raspberry Pi (`bcm43438` in particular) handle long-held
BLE connections poorly. If connections take tens of seconds or drop repeatedly,
a Bluetooth proxy is the real fix, not a software setting.

## Installation

### HACS (recommended)

1. HACS → three-dot menu → **Custom repositories**
2. Add `https://github.com/lachatak/ha-cloudbbq`, category **Integration**
3. Install **CloudBBQ Thermometer**, then restart Home Assistant

### Manual

Copy `custom_components/cloudbbq` into your Home Assistant `config/custom_components/`
directory and restart.

## Setup

Switch the thermometer on. It should be discovered automatically — look for a
notification, or go to **Settings → Devices & Services → Add Integration →
CloudBBQ Thermometer**.

Set the number of probe sockets your unit physically has via **Configure** on
the integration entry (default 4). The wire protocol always carries six slots;
only the configured number get entities.

## Entities

| Entity | Type | Notes |
|---|---|---|
| `Probe N` | sensor | °C. `Unknown` means nothing is plugged into that socket |
| `Battery` | sensor | %, diagnostic |
| `Probe N alert` | switch | Arms the high-temperature alert |
| `Probe N alert target` | number | °C, 0–300. Converted to °F on the wire |
| `Alarm tone` | select | Silent / Tone A / Tone B / Tone C |
| `Alarm interval` | number | 0–60 min, in steps of 5. How often a triggered alarm repeats |
| `Silence` | button | Silences an alarm that is *currently sounding* |
| `Connection` | switch | Off releases the device for the phone app |

**Alert state is not readable from the device.** It never reports its stored
targets or tone back, so Home Assistant remembers what you set and restores it
across restarts. If you change a setting from the phone app, Home Assistant
will not know, and may overwrite it on its next reconnect.

## Protocol

Recovered by decompiling the vendor's Android app
(`com.bobwen.ble.cloudbbq`, class `utils/TempMonitorManager`) and reading its
own logcat output during a working session. Documented here because it does
not appear to be written down anywhere else.

### GATT

Service `0000fff0-0000-1000-8000-00805f9b34fb`:

| Characteristic | Direction | Purpose |
|---|---|---|
| `fff1` | notify | Command acknowledgements |
| `fff2` | write | Login |
| `fff3` | notify | History |
| `fff4` | write | Commands |
| `fff5` | notify | Realtime temperatures |

Firmware revision is the standard `0x2A26` characteristic.

### Commands

Every command is **8 bytes**, `<cmd> <args…> <cmd>` — the command byte is
repeated as a trailer, and the app rejects any frame whose first and last bytes
differ.

| Command | Bytes | Meaning |
|---|---|---|
| Login | `21 65 43 21 00 00 00 21` | PIN 654321 as BCD, written to `fff2` |
| Set target | `22 <probe> <on> <lo> <hi> 00 00 22` | Probe is **1-based**; target in **°F** |
| Settings | `23 <unit> <minutes> 00 00 00 00 23` | Byte 2 = alarm repeat interval in minutes |
| Battery | `24 00 00 00 00 00 00 24` | Replies on `fff1` with the level in byte 1 |
| Silence | `25 00 00 00 00 00 00 25` | Silences a sounding alarm; momentary |
| Alarm tone | `29 <tone> 00 00 00 00 00 29` | 0 = silent, 1–3 = tones A–C |

### Realtime packet

`fff5` pushes **12 bytes** roughly once a second: six probes, `uint16`
little-endian, in **whole degrees Celsius**. `65526` (`0xFFF6`) means that
socket is empty. The device always reports Celsius; the unit setting only
changes its own display.

### Things that cost real debugging time

- **Targets go out in Fahrenheit** while readings come back in Celsius. The
  app does `f * 1.8 + 32` before sending. Send Celsius directly and every
  alert fires instantly, because 25 is read as 25 °F = −4 °C.
- **Probe indices are 1–6**, not 0–5.
- **Byte 2 of `0x23` is the alarm repeat interval, not volume.** This firmware
  appears to ignore volume entirely. Sending `23 00 00` mutes the device and
  puts the crossed-speaker icon on its display — which is not what it looks
  like it should do.
- **`0x29 00` is the real mute**, via the tone selector.
- **No login is needed.** The app sends one; the device streams fine without
  it. There is no BLE pairing or bonding either, which is why handing the
  device between Home Assistant and the phone requires nothing but dropping
  the connection.
- **The advertisement cannot positively identify these units.** It is
  byte-for-byte the iBBQ format, embedded MAC and all. Discovery therefore
  matches on the advertised name (`BBQ*`) and rejects known iBBQ names, which
  is the best signal available rather than a lazy one.

## Known limitations

- Genuine iBBQ/Inkbird hardware may pass discovery and then fail to stream.
- Settings changed from the phone app are invisible to Home Assistant.
- History (`fff3`) is subscribed to but not parsed.
- The device's own display unit (°C/°F) is left alone deliberately.

## Credits

Protocol reverse-engineered from the CloudBBQ Android app. Not affiliated with
CloudBBQ or DongGuan BBK Electronic Technology.

## License

MIT — see [LICENSE](LICENSE).
