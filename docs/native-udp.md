# Route A: native UDP (Loxone only)

The Miniserver talks to the inverter's WiFi module directly, the way Home Assistant does:

- A **Virtual Output** sends an 8-byte Modbus RTU request to `<inverter-ip>:8899` every 30 s.
- The WiFi module answers with `AA 55` + the Modbus response (153 bytes for registers
  30100–30172), sent back to the port the request came from.
- A **Virtual UDP Input** on port 8899 picks the values out of that reply with command
  recognition, e.g. `\s11\2\1` = skip 11 bytes, then a 16-bit value, high byte first.

No extra hardware or software. Confirmed on a GW10KT-DT (firmware 15.15.35) with a Miniserver on
firmware 17.1, including byte order, 32-bit values (*Energy total*) and negative values
(*Reactive power*).

## Templates

| Inverter | Virtual UDP Input | Virtual Output |
|---|---|---|
| GW10KT-DT, English names | [`VI_GoodWe_GW10KT-DT_EN.xml`](../loxone/templates/VI_GoodWe_GW10KT-DT_EN.xml) | [`VO_GoodWe_GW10KT-DT_EN.xml`](../loxone/templates/VO_GoodWe_GW10KT-DT_EN.xml) |
| GW10KT-DT, Dutch names | [`VI_GoodWe_GW10KT-DT_NL.xml`](../loxone/templates/VI_GoodWe_GW10KT-DT_NL.xml) | [`VO_GoodWe_GW10KT-DT_NL.xml`](../loxone/templates/VO_GoodWe_GW10KT-DT_NL.xml) |
| Other DT models (PV3/PV4, diagnostics) | [`VI_GoodWe_DT_UDP.xml`](../loxone/templates/VI_GoodWe_DT_UDP.xml) | [`VO_GoodWe_DT_UDP.xml`](../loxone/templates/VO_GoodWe_DT_UDP.xml) |

The GW10KT-DT templates only contain what that inverter has, in a sensible order. Object names
are the names the app shows: no display names are set. The full list is in
[registers.md](registers.md#gw10kt-dt-templates).

## Setup

1. Give the inverter a fixed IP (DHCP reservation).
2. Loxone Config → **Virtual Inputs → Device templates → Import** the `VI_` file, then create the
   Virtual UDP Input from it. Sender address: the inverter IP. Port: `8899`.
3. **Virtual Outputs → Device templates → Import** the `VO_` file, then create the Virtual Output
   from it. Address: `/dev/udp/<inverter-ip>/8899`.
4. Connect **Poll data** (*Gegevens ophalen*) to a constant On. It sends a request every 30 s.
5. Save to the Miniserver. Within a minute *Energy total* shows the inverter's lifetime yield and
   *Operating state* shows `1` while the sun is up. If it shows `256`, the byte order is reversed:
   please open an issue.

## Using the values

| What | How |
|---|---|
| PV power | Formula block: `PV1 voltage × PV1 current + PV2 voltage × PV2 current` (Home Assistant shows the same calculation) |
| AC power | *AC power* (W) |
| Energy | *Energy today*, *Energy total* (kWh). Use *Energy total* as the meter reading in a Meter block |
| Inverter answering? | *Heartbeat* changes with every reply. Feed it through a change detector into a retriggerable monoflop of 120 s: output on = inverter online |
| Night | The Virtual UDP Input **keeps the last value** when the inverter switches off at dusk. Multiply power values by the "online" monoflop, otherwise the last daytime value stays visible all night |
| Meter block | Formula `I1 * I2 / 1000` with I1 = *AC power*, I2 = the "online" monoflop, into **Pf** (kW). *Energy total* into **Mr**, so the energy count comes from the inverter itself |
| Errors | *Error code* > 0 or *Operating state* = 2. Bit meanings in [registers.md](registers.md#error-codes-30130) |

## Export limit (writes)

The Virtual Output also contains ready-made write commands, because a Virtual Output can't compute
the Modbus checksum for an arbitrary value:

- **Export limit on/off**: register 40327 (on = 1, off = 0)
- **Export limit 0% … 100%**: register 40336, one command per 10 %

They are untested on a real inverter. They change a setting stored in the inverter's EEPROM, so
trigger them on a real change only, never from a repeating pulse. What the export limit does
without a GoodWe smart meter is model-dependent: check the inverter manual or SEMS before relying
on it for curtailment.

The smart meter (30195+) and the settings read-back can't be read on this route: their replies
can't be told apart from the running-data reply in command recognition. Use
[route B](native-modbus.md) or [C](bridge.md) for those.

## Troubleshooting: no values

The UDP Monitor in Loxone Config (Virtual UDP Input → *Start monitor*) stays empty:

1. Check that *Poll data* is on and the addresses are right.
2. Run [`tools/udp_probe.py`](../tools/udp_probe.py) on a computer in the same network
   (`python3 tools/udp_probe.py --relay <inverter-ip>`, no installation), and point the Virtual
   Output at that computer instead of the inverter. It prints where the Miniserver's requests come
   from:

| Probe output | Meaning |
|---|---|
| `from <miniserver>:8899` | Fine: replies come back to port 8899. Check the input's sender address and port |
| `fixed port N` | Set the Virtual UDP Input's port to N |
| `source port changes` | Toggle **Close connection after send** on the Virtual Output and try again. Still changing: this Miniserver can't do route A, use [route C](bridge.md) |
| nothing | The Virtual Output doesn't send: check its address and that the command is on |
