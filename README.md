# GoodWe × Loxone

GoodWe DT solar inverters (GW10KT-DT and the rest of the DT/MS/D-NS/XS family) in a Loxone
Miniserver, without Home Assistant.

Everything is built on the same register map as the Home Assistant
[GoodWe integration](https://www.home-assistant.io/integrations/goodwe) and its
[goodwe](https://github.com/marcelblijleven/goodwe) library, checked against a real GW10KT-DT. It
gives PV power, AC power, energy today/total, voltages, currents, frequency, temperature, work
mode, error codes and the grid export limit.

## Which route?

The inverter's WiFi module talks **Modbus RTU over UDP port 8899**. The Miniserver's Modbus
support only speaks Modbus TCP (port 502) and Modbus RTU over RS485. That leaves three routes, all
on the same registers, so you can switch later by swapping the Loxone template:

| Route | Needs | Read | Control | Guide |
|---|---|---|---|---|
| **A. Native UDP** ⭐: Virtual Output sends the request, Virtual UDP Input parses the reply | nothing | ✅ all running data | export limit on/off and 0–100 % in 10 % steps | [docs/native-udp.md](docs/native-udp.md) |
| **B. Native Modbus**: RS485 to a Loxone Modbus Extension, or a GoodWe Modbus TCP dongle | hardware | ✅ everything incl. smart meter | export limit, any value | [docs/native-modbus.md](docs/native-modbus.md) |
| **C. Bridge**: `goodwe-modbus` turns UDP 8899 into Modbus TCP | a small Linux box (LXC, Pi, Docker) | ✅ everything + PV power and night handling | export limit, any value, clock sync | [docs/bridge.md](docs/bridge.md) |

**Start with route A.** It runs on a GW10KT-DT with a Miniserver on firmware 17.1, with no extra
hardware or software. Ready-made templates with English or Dutch names:
`VI_/VO_GoodWe_GW10KT-DT_EN.xml` and `_NL.xml`. Routes B and C are there for when you need the smart
meter, free export-limit values, or a Miniserver that doesn't get the replies
([`tools/udp_probe.py`](tools/udp_probe.py) finds out why).

```text
A  Loxone ──UDP 8899 (Virtual Output / UDP Input)──────────────────────▶ WiFi module ─ inverter
B  Loxone ──RS485 (Modbus Extension) or Modbus TCP 502 (GoodWe dongle)──▶ inverter
C  Loxone ──Modbus TCP 502──▶ goodwe-modbus ──UDP 8899──▶ WiFi module ─ inverter
                               └─ status page :8080
```

## Repository layout

| Path | Contents |
|---|---|
| `loxone/templates/` | Route A: `VI_/VO_GoodWe_GW10KT-DT_EN/NL.xml` (GW10KT-DT), `VI_/VO_GoodWe_DT_UDP.xml` (every register, other DT models). Route B: `MB_GoodWe_DT*.xml`. Route C: `MB_GoodWe_DT_Bridge.xml` |
| `src/goodwe_modbus/` | The bridge (Python 3.9+, only dependency: goodwe). `registers.py` is the register map everything is generated from |
| `tools/` | Template generator, UDP probe for route A |
| `docs/` | Guides per route, [register map](docs/registers.md) |
| `tests/` | Tests, incl. a fixture read from a real GW10KT-DT (serial number replaced) |
| `deploy/`, `Dockerfile` | LXC/Debian installer, systemd unit, settings file, container |

## Development

```bash
pip install -e '.[test]' && pytest
python3 tools/build_templates.py           # templates + docs/registers.md from registers.py
GOODWE_HOST=<inverter-ip> GOODWE_MODBUS_PORT=5020 python -m goodwe_modbus
```

Not affiliated with GoodWe or Loxone. MIT licensed.
