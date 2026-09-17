# hil-node — ESP32-S3 HIL firmware

Streams a replayed spike-train waveform (10 kHz, int16, 0.1 µV units) as
`HilRawFrame`s over MQTT and reacts to intervention commands. Wire contract:
`proto/WIRE.md` §4.1 and §8.

| Item | Value |
|---|---|
| Board | ESP32-S3-DevKitC-1 (Arduino framework, PlatformIO) |
| Node id | `0xFFFF0000 + HIL_INDEX` → `ffff0000` (env `hil0`), `ffff0001` (env `hil1`) |
| Publishes | `hil/{node_hex}/raw` every 100 ms (2032 B binary), `hil/{node_hex}/status` every 2 s (retained JSON) |
| Subscribes | `hil/{node_hex}/cmd` → `{"mode":0..3,"intensity":0..1}` |
| Button | BOOT (GPIO0) cycles baseline → toxin → drug → radiation (intensity 0.8) |

## 1. Configure

```bash
cd firmware/hil-node
cp secrets.example.ini secrets.ini     # gitignored
```

Edit `secrets.ini`:

```ini
[secrets]
wifi_ssid = MyLabWifi        ; 2.4 GHz network (ESP32 has no 5 GHz)
wifi_pass = supersecret
mqtt_host = 192.168.1.10     ; LAN IP of the laptop running `just up`
mqtt_port = 1883
```

**Finding the laptop's LAN IP for `mqtt_host`**

* Windows (PowerShell): `Get-NetIPAddress -AddressFamily IPv4 | ? {$_.InterfaceAlias -match 'Wi-Fi|Ethernet'} | select IPAddress,InterfaceAlias`
  or `ipconfig` → "IPv4 Address" under your Wi-Fi/Ethernet adapter (not the WSL/Docker/vEthernet ones).
* macOS: `ipconfig getifaddr en0`
* Linux: `ip -4 addr show | grep inet`

The board and the laptop must be on the same network, and Windows Firewall
must allow inbound TCP 1883 (Docker Desktop normally adds this; if the board
reports `[mqtt] failed, rc=-2`, add a rule: `netsh advfirewall firewall add rule name=mosquitto dir=in action=allow protocol=TCP localport=1883`).

Both boards share one `secrets.ini`; only the env differs.

## 2. Build

```bash
pio run -e hil0            # board #1 -> ffff0000
pio run -e hil1            # board #2 -> ffff0001
pio run                    # both (default_envs = hil0; add hil1 to build both)
```

First build downloads the ESP32-S3 toolchain (~1 min). Firmware is ~1 MB.

## 3. Flash the two boards

Plug **one board at a time** into the DevKit's **UART** micro-USB port (the one
labelled *UART*/*COM*, driven by the CP2102 — not the *USB* OTG port).

```bash
# board #1
pio run -e hil0 -t upload                     # auto-detects the only serial port
pio device monitor -e hil0                    # 115200 baud, Ctrl+C to exit

# board #2
pio run -e hil1 -t upload
pio device monitor -e hil1
```

With several boards attached, pick the port explicitly:
`pio device list` then `pio run -e hil1 -t upload --upload-port COM7` (Windows)
or `--upload-port /dev/ttyUSB1` (Linux) / `/dev/cu.usbserial-xxxx` (macOS).

If upload fails with *"Failed to connect"*, hold **BOOT**, tap **RESET**,
release BOOT, and retry; press RESET afterwards to start the firmware.

Expected serial output:

```
[hil] node ffff0000 (HIL_INDEX=0) fw 0.1.0
[hil] sampling at 10 kHz
[wifi] connecting to MyLabWifi ...
[mqtt] connecting to 192.168.1.10:1883 as hil-ffff0000 ...
[mqtt] connected; subscribed hil/ffff0000/cmd
```

## 4. Verify from the laptop

```bash
mosquitto_sub -h localhost -t 'hil/+/status' -v                  # JSON every 2 s
mosquitto_sub -h localhost -t 'hil/ffff0000/raw' -C 1 | xxd | head  # "HILR" header
mosquitto_pub -h localhost -t hil/ffff0000/cmd -m '{"mode":1,"intensity":0.8}'
```

Or via the bridge: `curl localhost:9102/hil` and `curl localhost:9102/metrics`.

## 5. Playback semantics

`src/playback.{h,cpp}` renders `include/template.h` (spike times + biphasic
waveform + pink-noise RMS) in the 10 kHz timer ISR. Modes (WIRE §8):

| Mode | Effect |
|---|---|
| baseline | template as recorded; residual severity fades over ~60 s |
| toxin | severity ramps to `intensity` over 20 s: spikes dropped with p = severity, amplitude × (1 − 0.7·severity) |
| drug | severity ramps back to 0 (15 s at intensity 1) |
| radiation | 2 s burst (+150·intensity Hz extra spikes, +30 % amplitude) then noise × (1 + 3·intensity) and severity → 0.6·intensity |

Pure functions (`step_pathology`, `compute_effects`, `quantize`, `next_mode`)
are unit-tested on the host: `pio test -e native` (needs a native g++).

## 6. Template (`include/template.h`)

Generated, do not edit. Placeholder = gamma-renewal spike train (shape 2,
20 Hz, 2 ms refractory) + biphasic 1.6 ms waveform + 4.5 µV pink noise:

```bash
python tools/make_template.py                     # regenerate placeholder
python tools/make_template.py --render tpl.npy    # + rendered 10 s int16 reference
```

`ml/scripts/make_template.jl` (`just template`) replaces it with a real CRCNS/Zenodo-derived segment through the same
script: `--spikes times.npy [--wave wave.npy --amps amps.npy --source "..."]`.
The header stores the ingredients (times/amplitudes/waveform), not the
rendered array, so it stays a few KB; the device renders identically at run time.
