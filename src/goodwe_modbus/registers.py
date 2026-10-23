"""GoodWe DT register map: the single source for the bridge, the Loxone templates and the docs.

Addresses are Modbus PDU addresses exactly as GoodWe (and the goodwe library) number them, so
Loxone's ModbusAddress is the register number itself. Sources: goodwe library (dt.py) and a
read of a GW10KT-DT (firmware 15.15.35).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Kind(Enum):
    U16 = (1, False)
    S16 = (1, True)
    U32 = (2, False)
    S32 = (2, True)

    @property
    def words(self) -> int:
        return self.value[0]

    @property
    def signed(self) -> bool:
        return self.value[1]


# What the bridge serves while the inverter doesn't answer (DT inverters switch off at night).
KEEP = "keep"  # last known value (totals, codes, settings)
ZERO = "zero"  # instantaneous values: 0
DAILY = "daily"  # last value until midnight, then 0 (energy today)

# Register groups, used to pick what goes into each Loxone template.
MAIN = "main"  # what Home Assistant shows for a GW10KT-DT
EXTRA = "extra"  # diagnostics
PV34 = "pv34"  # 3rd/4th MPPT, 65535 on 2-MPPT models
METER = "meter"  # GoodWe smart meter (no reply at all when none is connected)
SETTING = "setting"  # writable settings
BRIDGE = "bridge"  # values the bridge adds; not on the inverter

NOT_AVAILABLE = 0xFFFF  # raw value of a sensor the model doesn't have


@dataclass(frozen=True)
class Register:
    address: int
    name: str
    kind: Kind = Kind.U16
    scale: int = 1  # engineering value = raw / scale
    unit: str = ""
    group: str = MAIN
    offline: str = KEEP
    poll: int = 10  # Loxone polling cycle in seconds
    hint: str = ""
    write: tuple[int, int] | None = None  # allowed raw range for writes
    tags: frozenset[str] = field(default_factory=frozenset)

    @property
    def words(self) -> int:
        return self.kind.words

    def decode(self, words: list[int]) -> float:
        raw = 0
        for word in words:
            raw = (raw << 16) | word
        bits = 16 * len(words)
        if self.kind.signed and raw >= 1 << (bits - 1):
            raw -= 1 << bits
        return raw / self.scale if self.scale != 1 else raw


R = Register
WORK_MODES = "0 Wait, 1 Normal, 2 Error, 4 Check"
ERROR_HINT = "Bitmap, 0 = no error. See docs/registers.md for the bits."
DERATING_HINT = "Bitmap. Bit 2 (4) = reactive power derating (PF/QU/FixQ)."

REGISTERS: tuple[Register, ...] = (
    # Running data, read by GoodWe tools as one block 30100-30172
    R(30102, "Clock minutes/seconds", group=EXTRA, poll=30,
      hint="Inverter clock, minute x 256 + second. Changes with every read: use it to see that the inverter answers."),
    R(30103, "PV1 voltage", scale=10, unit="V", offline=ZERO),
    R(30104, "PV1 current", scale=10, unit="A", offline=ZERO),
    R(30105, "PV2 voltage", scale=10, unit="V", offline=ZERO),
    R(30106, "PV2 current", scale=10, unit="A", offline=ZERO),
    R(30107, "PV3 voltage", scale=10, unit="V", group=PV34, offline=ZERO),
    R(30108, "PV3 current", scale=10, unit="A", group=PV34, offline=ZERO),
    R(30109, "PV4 voltage", scale=10, unit="V", group=PV34, offline=ZERO),
    R(30110, "PV4 current", scale=10, unit="A", group=PV34, offline=ZERO),
    R(30115, "Grid L1-L2 voltage", scale=10, unit="V", group=EXTRA, offline=ZERO, poll=60),
    R(30116, "Grid L2-L3 voltage", scale=10, unit="V", group=EXTRA, offline=ZERO, poll=60),
    R(30117, "Grid L3-L1 voltage", scale=10, unit="V", group=EXTRA, offline=ZERO, poll=60),
    R(30118, "Grid L1 voltage", scale=10, unit="V", offline=ZERO, poll=30),
    R(30119, "Grid L2 voltage", scale=10, unit="V", offline=ZERO, poll=30),
    R(30120, "Grid L3 voltage", scale=10, unit="V", offline=ZERO, poll=30),
    R(30121, "Grid L1 current", scale=10, unit="A", offline=ZERO),
    R(30122, "Grid L2 current", scale=10, unit="A", offline=ZERO),
    R(30123, "Grid L3 current", scale=10, unit="A", offline=ZERO),
    R(30124, "Grid L1 frequency", scale=100, unit="Hz", offline=ZERO, poll=30),
    R(30125, "Grid L2 frequency", scale=100, unit="Hz", group=EXTRA, offline=ZERO, poll=60),
    R(30126, "Grid L3 frequency", scale=100, unit="Hz", group=EXTRA, offline=ZERO, poll=60),
    R(30127, "AC output power", Kind.U32, unit="W", offline=ZERO),
    R(30129, "Work mode", unit="", offline=ZERO, poll=30, hint=WORK_MODES),
    R(30130, "Error codes", Kind.U32, poll=30, hint=ERROR_HINT),
    R(30132, "Warning code", group=EXTRA, poll=60),
    R(30133, "Apparent power", Kind.S32, unit="VA", group=EXTRA, offline=ZERO, hint="0 on some models"),
    R(30135, "Reactive power", Kind.S32, unit="var", group=EXTRA, offline=ZERO),
    R(30139, "Power factor", Kind.S16, scale=1000, group=EXTRA, poll=60),
    R(30141, "Inverter temperature", Kind.S16, scale=10, unit="°C", poll=60),
    R(30144, "Energy today", scale=10, unit="kWh", offline=DAILY, poll=60),
    R(30145, "Energy total", Kind.U32, scale=10, unit="kWh", poll=60),
    R(30147, "Operating hours", Kind.U32, unit="h", group=EXTRA, poll=300),
    R(30149, "Safety country code", group=EXTRA, poll=300, hint="32 = 50Hz 230Vac Default"),
    R(30162, "Function bits", group=EXTRA, poll=300),
    R(30163, "Bus voltage", scale=10, unit="V", group=EXTRA, offline=ZERO, poll=60),
    R(30164, "NBus voltage", scale=10, unit="V", group=EXTRA, offline=ZERO, poll=60),
    R(30165, "Derating mode", Kind.U32, group=EXTRA, poll=60, hint=DERATING_HINT),
    R(30172, "WiFi signal (RSSI)", group=EXTRA, poll=300),
    # Smart meter block 30195-30209
    R(30195, "Meter active power", Kind.S32, unit="W", group=METER, offline=ZERO,
      hint="Positive = export, negative = import"),
    R(30197, "Meter energy export total", Kind.U32, scale=1000, unit="kWh", group=METER, poll=60),
    R(30199, "Meter energy import total", Kind.U32, scale=1000, unit="kWh", group=METER, poll=60),
    R(30209, "Meter communication status", group=METER, poll=60, hint="1 normal, 2 disconnected"),
    # Settings (three-phase DT)
    R(40327, "Grid export limit enabled", group=SETTING, poll=300, write=(0, 1), hint="0 off, 1 on"),
    R(40336, "Grid export limit", unit="%", group=SETTING, poll=300, write=(0, 100),
      hint="Percent of rated power. Only applies when the export limit is enabled (40327)."),
)

# Blocks the bridge reads from the inverter in one request each.
RUNTIME_BLOCK = (30100, 73)  # 30100-30172, same request the goodwe library uses
METER_BLOCK = (30195, 15)  # 30195-30209
DEVICE_BLOCK = (30001, 40)  # 30001-30040: serial number, model name, firmware
SETTING_ADDRESSES = tuple(r.address for r in REGISTERS if r.group == SETTING)
CLOCK_ADDRESS = 40313  # 3 words: YY MM, DD hh, mm ss

# ---- bridge extension registers (not on the inverter) -------------------------------------

STATUS_ONLINE = 0
STATUS_OFFLINE = 1  # inverter not answering; normal at night
STATUS_STARTING = 2  # no data yet since the bridge started

WRITE_NONE, WRITE_OK, WRITE_PENDING, WRITE_FAILED, WRITE_REJECTED, WRITE_BLOCKED = range(6)

BRIDGE_STATUS = 1000
BRIDGE_DATA_AGE = 1001
BRIDGE_FAILURES = 1002
BRIDGE_WRITES_ENABLED = 1003
BRIDGE_LAST_WRITE = 1004
PV_POWER = 1010
PV1_POWER, PV2_POWER, PV3_POWER, PV4_POWER = 1011, 1012, 1013, 1014
GRID_L1_POWER, GRID_L2_POWER, GRID_L3_POWER = 1015, 1016, 1017
SYNC_CLOCK = 1020

BRIDGE_REGISTERS: tuple[Register, ...] = (
    R(BRIDGE_STATUS, "Bridge status", group=BRIDGE, poll=30,
      hint="0 inverter online, 1 inverter not answering (normal at night), 2 starting"),
    R(BRIDGE_DATA_AGE, "Data age", unit="s", group=BRIDGE, poll=30, hint="Seconds since the last good read"),
    R(BRIDGE_FAILURES, "Failed reads in a row", group=BRIDGE, poll=60),
    R(BRIDGE_WRITES_ENABLED, "Writes enabled", group=BRIDGE, poll=300, hint="GOODWE_ALLOW_WRITES on the bridge"),
    R(BRIDGE_LAST_WRITE, "Last write result", group=BRIDGE, poll=60,
      hint="0 none, 1 ok, 2 waiting for the inverter, 3 failed, 4 rejected by the inverter, 5 blocked (rate limit)"),
    R(PV_POWER, "PV power", unit="W", group=BRIDGE, hint="Sum of PV voltage x current, as Home Assistant shows it"),
    R(PV1_POWER, "PV1 power", unit="W", group=BRIDGE),
    R(PV2_POWER, "PV2 power", unit="W", group=BRIDGE),
    R(PV3_POWER, "PV3 power", unit="W", group=BRIDGE, tags=frozenset({PV34})),
    R(PV4_POWER, "PV4 power", unit="W", group=BRIDGE, tags=frozenset({PV34})),
    R(GRID_L1_POWER, "Grid L1 power", unit="W", group=BRIDGE),
    R(GRID_L2_POWER, "Grid L2 power", unit="W", group=BRIDGE),
    R(GRID_L3_POWER, "Grid L3 power", unit="W", group=BRIDGE),
    R(SYNC_CLOCK, "Sync inverter clock", group=BRIDGE, write=(0, 1), poll=300,
      hint="Write 1: the bridge sets the inverter clock to its own time"),
)

ALL_REGISTERS = REGISTERS + BRIDGE_REGISTERS
BY_ADDRESS: dict[int, Register] = {r.address: r for r in ALL_REGISTERS}
WRITABLE: dict[int, Register] = {r.address: r for r in ALL_REGISTERS if r.write}

# PV strings and grid phases whose power the bridge calculates: (power register, voltage, current)
CALCULATED_POWER = (
    (PV1_POWER, 30103, 30104), (PV2_POWER, 30105, 30106), (PV3_POWER, 30107, 30108), (PV4_POWER, 30109, 30110),
    (GRID_L1_POWER, 30118, 30121), (GRID_L2_POWER, 30119, 30122), (GRID_L3_POWER, 30120, 30123),
)

# Bits of the error code register (30130), from the goodwe library.
ERROR_BITS = {
    0: "GFCI Device Check Failure", 1: "AC HCT Check Failure", 3: "DCI Consistency Failure",
    4: "GFCI Consistency Failure", 6: "GFCI Device Failure", 7: "Relay Device Failure", 8: "AC HCT Failure",
    9: "Utility Loss", 10: "Ground I Failure", 11: "DC Bus High", 12: "Internal Fan Failure",
    13: "Over Temperature", 14: "Utility Phase Failure", 15: "PV Over Voltage", 16: "External Fan Failure",
    17: "Vac Failure", 18: "Isolation Failure", 19: "DC Injection High", 20: "Back-Up Over Load",
    22: "Fac Consistency Failure", 23: "Vac Consistency Failure", 25: "Relay Check Failure",
    27: "Phase Angle Failure", 28: "DSP Communication Failure", 29: "Fac Failure", 30: "EEPROM R/W Failure",
    31: "Internal Communication Failure",
}
DERATING_BITS = {
    0: "Overtemperature derating", 1: "Active power derating", 2: "Reactive power derating (PF/QU/FixQ)",
    3: "Power vs frequency", 4: "PU curve", 5: "Export power derating", 6: "DRED derating",
    7: "Limited power start derating", 8: "Maximum current derating", 9: "Overvoltage derating",
    10: "Power calibration limit power",
}


def bits_text(value: int, names: dict[int, str]) -> str:
    return ", ".join(names.get(bit, f"bit {bit}") for bit in range(32) if value >> bit & 1)
