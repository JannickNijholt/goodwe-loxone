# Route B: native Modbus (RS485 or Modbus TCP)

Loxone's own Modbus support reads the inverter directly. This is the standard, fully supported
way, but it needs a physical Modbus connection: the WiFi module on a GW10KT-DT only speaks
Modbus over UDP, which Loxone's Modbus server can't use.

Templates:
- [`MB_GoodWe_DT.xml`](../loxone/templates/MB_GoodWe_DT.xml): what Home Assistant shows, plus the export limit
- [`MB_GoodWe_DT_full.xml`](../loxone/templates/MB_GoodWe_DT_full.xml): every register, incl. smart meter, MPPT 3/4 and diagnostics

## Options

| | Hardware | Loxone object |
|---|---|---|
| **RS485** | Loxone Modbus Extension, 2-wire cable to the inverter's RS485 terminal | Modbus device under the extension: 9600 baud, 8N1, address 247 |
| **Modbus TCP** | A GoodWe dongle with Modbus TCP (LAN/WiFi kit 2.0, WLA0000-01-00P), if available for your model | Modbus Server: inverter IP, port 502, unit ID 247 |
| **RS485 → TCP gateway** | e.g. a Waveshare RS485-to-Ethernet converter in "Modbus TCP to RTU" mode | Modbus Server: gateway IP, port 502, unit ID 247 |

Check the inverter manual for the RS485 terminal and whether it can be used together with the
WiFi module. On some GoodWe models both share one communication port. If RS485 replaces the WiFi
module, the SEMS portal stops receiving data.

Address 247 is GoodWe's default (also used by [evcc](https://docs.evcc.io/en/meters/goodwe-sdt-dt-inverter)).
If the inverter doesn't answer, try 127 (0x7F, what the goodwe library uses over UDP for DT
inverters) or 1.

## Import

1. Loxone Config → **Device templates → Import template** → `MB_GoodWe_DT.xml`.
2. Add the Modbus device from the template (under the Modbus Extension or as a Modbus Server)
   and set the address/unit ID.
3. The objects are named like Home Assistant's sensors; the IO address is the GoodWe register
   number (see [registers.md](registers.md)).

## Notes

- **32-bit values** (AC output power, energy total, error codes) use data type 98 (32-bit
  unsigned, high word first), the type Loxone's own SMA and Huawei templates use for the same
  layout. Verify *Energy total* against the SEMS app after import.
- **65535** raw means "not available on this model". Delete those objects.
- **Night**: the inverter switches off with the sun. Loxone marks the Modbus objects as failing and
  keeps the last value. Use *AC output power* only while the device communicates, or use the bridge,
  which serves 0 at night.
- **Export limit** actuators write on change only (no repeat): the value is stored in EEPROM.
  *Grid export limit* (40336) only applies while *Grid export limit enabled* (40327) is 1.
