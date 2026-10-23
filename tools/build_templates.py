#!/usr/bin/env python3
"""Generate the Loxone templates and the register docs from src/goodwe_modbus/registers.py.

Usage: python3 tools/build_templates.py [--check]

Writes to loxone/templates/
  VI_/VO_GoodWe_GW10KT-DT_EN.xml, _NL.xml   native UDP for a GW10KT-DT, English / Dutch names
  VI_/VO_GoodWe_DT_UDP.xml                  native UDP, every register (other DT models)
  MB_GoodWe_DT.xml, MB_GoodWe_DT_full.xml   native Modbus (RS485 Modbus Extension or Modbus TCP dongle)
  MB_GoodWe_DT_Bridge.xml                   goodwe-modbus bridge
and docs/registers.md.

Objects never get a display name (Comment): Loxone shows that instead of the title in the app.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import quoteattr

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from goodwe_modbus.registers import (  # noqa: E402
    BRIDGE_REGISTERS, BY_ADDRESS, DERATING_BITS, ERROR_BITS, EXTRA, MAIN, METER, PV34, REGISTERS, RUNTIME_BLOCK,
    SETTING, Kind, Register,
)

UNIT_ID = 247  # GoodWe default Modbus address on RS485 and Modbus TCP
MIN_LOXONE_VERSION = "15000227"
PLACEHOLDER_IP = "192.168.1.50"  # replaced by the user after import
UDP_PORT = 8899
DT_COMM_ADDRESS = 0x7F
POLL_SECONDS = 30

# Loxone ModbusDataType codes. 32-bit codes taken from Loxone Library templates of inverters with
# high-word-first registers like GoodWe (SMA, Huawei): 98 = uint32, 99 = int32.
DATA_TYPE = {Kind.U16: 32, Kind.S16: 33, Kind.U32: 98, Kind.S32: 99}
READ_HOLDING, WRITE_SINGLE = 3, 6

UNIT_FORMAT = {"V": "<v.1> V", "A": "<v.1> A", "Hz": "<v.2> Hz", "W": "<v> W", "kWh": "<v.1> kWh", "°C": "<v.1>°C",
               "h": "<v> h", "%": "<v>%", "s": "<v> s", "VA": "<v> VA", "var": "<v> var"}

# Loxone Config's template import cuts an attribute at a raw apostrophe and then loses the
# following attributes. Loxone itself writes &apos;.
LOXONE_ENTITIES = {"\n": "&#10;", "'": "&apos;"}

# ---- GW10KT-DT: what the inverter actually has, in app order, with names per language ------

GW10KT_DT: dict[int, dict[str, str]] = {
    30127: {"en": "AC power", "nl": "AC-vermogen"},
    30144: {"en": "Energy today", "nl": "Opbrengst vandaag"},
    30145: {"en": "Energy total", "nl": "Opbrengst totaal"},
    30103: {"en": "PV1 voltage", "nl": "PV1 spanning"},
    30104: {"en": "PV1 current", "nl": "PV1 stroom"},
    30105: {"en": "PV2 voltage", "nl": "PV2 spanning"},
    30106: {"en": "PV2 current", "nl": "PV2 stroom"},
    30118: {"en": "Grid voltage L1", "nl": "Netspanning L1"},
    30119: {"en": "Grid voltage L2", "nl": "Netspanning L2"},
    30120: {"en": "Grid voltage L3", "nl": "Netspanning L3"},
    30121: {"en": "Grid current L1", "nl": "Netstroom L1"},
    30122: {"en": "Grid current L2", "nl": "Netstroom L2"},
    30123: {"en": "Grid current L3", "nl": "Netstroom L3"},
    30124: {"en": "Grid frequency", "nl": "Netfrequentie"},
    30141: {"en": "Inverter temperature", "nl": "Temperatuur omvormer"},
    30129: {"en": "Operating state", "nl": "Bedrijfstoestand"},
    30130: {"en": "Error code", "nl": "Foutcode"},
    30147: {"en": "Operating hours", "nl": "Bedrijfsuren"},
    30172: {"en": "WiFi signal", "nl": "WiFi-signaal"},
    30102: {"en": "Heartbeat", "nl": "Hartslag"},
}
HINTS = {
    30129: {"en": "0 = waiting, 1 = normal, 2 = error, 4 = checking",
            "nl": "0 = wachten, 1 = normaal, 2 = storing, 4 = controle"},
    30130: {"en": "0 = no error", "nl": "0 = geen storing"},
    30102: {"en": "Changes with every reply from the inverter", "nl": "Verandert bij elk antwoord van de omvormer"},
}
TEXT = {
    "en": {"title": "GoodWe GW10KT-DT", "poll": "Poll data", "limit_switch": "Export limit on/off",
           "limit": "Export limit {}%",
           "vi_hint": "Replies from the GoodWe WiFi module. Sender address: inverter IP, port 8899.",
           "vo_hint": "Requests to the GoodWe WiFi module. Address: /dev/udp/<inverter IP>/8899. "
                      "Keep Poll data on: it asks for new values every 30 s.",
           "poll_hint": "Keep on: asks for new values every 30 s",
           "limit_switch_hint": "Writes an inverter setting: on = 1, off = 0",
           "limit_hint": "Writes an inverter setting on a rising edge"},
    "nl": {"title": "GoodWe GW10KT-DT", "poll": "Gegevens ophalen", "limit_switch": "Exportlimiet aan/uit",
           "limit": "Exportlimiet {}%",
           "vi_hint": "Antwoorden van de GoodWe WiFi-module. Afzenderadres: IP van de omvormer, poort 8899.",
           "vo_hint": "Verzoeken aan de GoodWe WiFi-module. Adres: /dev/udp/<IP omvormer>/8899. "
                      "Laat Gegevens ophalen aan: vraagt elke 30 s nieuwe waarden op.",
           "poll_hint": "Aan laten: vraagt elke 30 s nieuwe waarden op",
           "limit_switch_hint": "Schrijft een instelling van de omvormer: aan = 1, uit = 0",
           "limit_hint": "Schrijft een instelling van de omvormer bij een stijgende flank"},
}


def attrs(**values: object) -> str:
    return " ".join(f"{k}={quoteattr(str(v), LOXONE_ENTITIES)}" for k, v in values.items() if v is not None)


def loxone_unit(reg: Register) -> str:
    if reg.address == 30139:
        return "<v.3>"
    if reg.unit == "kWh" and reg.scale == 1000:
        return "<v.3> kWh"
    return UNIT_FORMAT.get(reg.unit, "<v>")


def scaling(reg: Register, sensor: bool) -> dict:
    # Sensors map raw (Source) -> value (Dest); actuators map value (Source) -> raw (Dest).
    if reg.scale == 1:
        return {"SourceValHigh": 100, "DestValHigh": 100}
    return {"SourceValHigh": reg.scale, "DestValHigh": 1} if sensor else {"SourceValHigh": 1, "DestValHigh": reg.scale}


def header(tag: str, template_type: int, **values: object) -> list[str]:
    return ['<?xml version="1.0" encoding="utf-8"?>', f"<{tag} {attrs(**values)}>",
            f'\t<Info templateType="{template_type}" minVersion="{MIN_LOXONE_VERSION}"/>']


# ---- Modbus templates ----------------------------------------------------------------------

def modbus_cmd(reg: Register, sensor: bool) -> str:
    title = reg.name if sensor else f"Set {reg.name[0].lower()}{reg.name[1:]}"
    return "\t<ModbusCmd " + attrs(
        Title=title,
        Comment="",
        HintText=reg.hint or None,
        ModbusAddress=reg.address,
        ModbusCmd=READ_HOLDING if sensor else WRITE_SINGLE,
        ModbusDataType=DATA_TYPE[reg.kind],
        ModbusPollingCycle=reg.poll if sensor else None,
        Unit=loxone_unit(reg),
        Analog="true",
        Sensor="true" if sensor else "false",
        **scaling(reg, sensor),
        # Settings live in EEPROM: write on change only, never periodically.
        RepeatRate=None if sensor else 0,
    ) + "/>"


def modbus_template(registers: list[Register], title: str, hint_text: str) -> str:
    lines = header("Modbus", 7, Title=title, Comment="", HintText=hint_text, Channel=UNIT_ID)
    lines += [modbus_cmd(r, True) for r in registers]
    lines += [modbus_cmd(r, False) for r in registers if r.write]
    lines.append("</Modbus>")
    return "\n".join(lines) + "\n"


def native(groups: set[str]) -> list[Register]:
    return [r for r in REGISTERS if r.group in groups]


NATIVE_HINT = ("GoodWe DT inverter, native Modbus. RS485: Loxone Modbus Extension, 9600 baud 8N1, address 247. "
               "Modbus TCP: port 502, unit ID 247 (needs a GoodWe dongle with Modbus TCP).")
BRIDGE_HINT = ("GoodWe DT through the goodwe-modbus bridge. Address: bridge IP, port 502, any unit ID. "
               "Registers 30xxx/40xxx are the inverter's own; 1000+ are bridge extensions.")


# ---- native UDP templates ------------------------------------------------------------------

def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def rtu_frame(function: int, address: int, value: int) -> bytes:
    body = bytes([DT_COMM_ADDRESS, function, address >> 8, address & 0xFF, value >> 8, value & 0xFF])
    crc = crc16(body)
    return body + bytes([crc & 0xFF, crc >> 8])


def escaped(frame: bytes) -> str:
    return "".join(f"\\x{b:02x}" for b in frame)


def udp_check(reg: Register) -> str:
    # Reply: AA 55, address, function, byte count, data..., CRC. \sN skips N bytes; \1..\4 are value
    # bytes from least significant, so big-endian registers are written high byte first.
    skip = 5 + 2 * (reg.address - RUNTIME_BLOCK[0])
    return f"\\s{skip}" + ("\\2\\1" if reg.words == 1 else "\\4\\3\\2\\1")


def check_address(check: str) -> int:
    """Register address a command recognition string reads (inverse of udp_check)."""
    return RUNTIME_BLOCK[0] + (int(re.match(r"\\s(\d+)", check).group(1)) - 5) // 2


def udp_input(registers: list[tuple[Register, str, str | None]], title: str, hint_text: str) -> str:
    lines = header("VirtualInUdp", 1, Title=title, Comment="", HintText=hint_text, Address=PLACEHOLDER_IP,
                   Port=UDP_PORT)
    for reg, name, hint_text in registers:
        lines.append("\t<VirtualInUdpCmd " + attrs(
            Title=name,
            Comment="",
            Address="",
            Check=udp_check(reg),
            Signed="true" if reg.kind.signed else "false",
            Analog="true",
            SourceValLow=0, DestValLow=0,
            **{"SourceValHigh": reg.scale, "DestValHigh": 1} if reg.scale != 1 else {"SourceValHigh": 100, "DestValHigh": 100},
            DefVal=0,
            MinVal=-2147483648 if reg.kind.signed else 0,
            MaxVal=4294967295,
            Unit=loxone_unit(reg),
            HintText=hint_text or "",
        ) + "/>")
    lines.append("</VirtualInUdp>")
    return "\n".join(lines) + "\n"


def vo_cmd(title: str, hint_text: str, on: bytes, off: bytes | None = None, repeat: int = 0) -> str:
    return "\t<VirtualOutCmd " + attrs(
        Title=title, Comment="", HintText=hint_text, CmdOnMethod="GET", CmdOffMethod="GET",
        CmdOn=escaped(on), CmdOnHTTP="", CmdOnPost="",
        CmdOff=escaped(off) if off else "", CmdOffHTTP="", CmdOffPost="",
        Analog="false", Repeat=repeat, RepeatRate=repeat,
    ) + "/>"


def udp_output(text: dict[str, str], title: str) -> str:
    start, count = RUNTIME_BLOCK
    lines = header("VirtualOut", 3, Title=title, Comment="", HintText=text["vo_hint"],
                   Address=f"/dev/udp/{PLACEHOLDER_IP}/{UDP_PORT}", CmdInit="", CloseAfterSend="false", CmdSep="")
    lines.append(vo_cmd(text["poll"], text["poll_hint"], rtu_frame(READ_HOLDING, start, count), repeat=POLL_SECONDS))
    lines.append(vo_cmd(text["limit_switch"], text["limit_switch_hint"],
                        rtu_frame(WRITE_SINGLE, 40327, 1), rtu_frame(WRITE_SINGLE, 40327, 0)))
    for percent in range(0, 101, 10):
        lines.append(vo_cmd(text["limit"].format(percent), text["limit_hint"], rtu_frame(WRITE_SINGLE, 40336, percent)))
    lines.append("</VirtualOut>")
    return "\n".join(lines) + "\n"


def gw10kt_dt_input(language: str) -> str:
    registers = [(BY_ADDRESS[a], names[language], HINTS.get(a, {}).get(language)) for a, names in GW10KT_DT.items()]
    return udp_input(registers, f"{TEXT[language]['title']} ({language.upper()})", TEXT[language]["vi_hint"])


def all_registers_input() -> str:
    start, count = RUNTIME_BLOCK
    registers = [(r, r.name, r.hint or None) for r in REGISTERS if start <= r.address < start + count]
    return udp_input(registers, "GoodWe DT (UDP replies)", TEXT["en"]["vi_hint"])


# ---- docs ----------------------------------------------------------------------------------

def markdown() -> str:
    rows = ["| Address | Name | Type | Scale | Unit | Group | Write | Notes |", "|---|---|---|---|---|---|---|---|"]
    for reg in REGISTERS + BRIDGE_REGISTERS:
        scale = f"÷{reg.scale}" if reg.scale != 1 else ""
        write = f"{reg.write[0]}–{reg.write[1]}" if reg.write else ""
        rows.append(f"| {reg.address} | {reg.name} | {reg.kind.name} | {scale} | {reg.unit} | {reg.group} | {write} | "
                    f"{reg.hint.replace(chr(10), '; ')} |")
    names = ["| Address | English | Dutch |", "|---|---|---|"]
    names += [f"| {a} | {n['en']} | {n['nl']} |" for a, n in GW10KT_DT.items()]
    bits = lambda table: "\n".join(f"| {bit} | {2 ** bit} | {name} |" for bit, name in sorted(table.items()))
    return ("# GoodWe DT register map\n\n"
            "Generated by `tools/build_templates.py` from `src/goodwe_modbus/registers.py`. Sources: the "
            "[goodwe](https://github.com/marcelblijleven/goodwe) library used by Home Assistant, checked against a "
            "GW10KT-DT (firmware 15.15.35).\n\n"
            "- Addresses are Modbus PDU addresses as GoodWe numbers them; in Loxone *IO address* = address.\n"
            "- All registers are holding registers (function 3). 32-bit values are high word first.\n"
            "- Raw `65535` means the model doesn't have that sensor (e.g. PV3/PV4 on a 2-MPPT inverter).\n"
            "- Groups: `main` = what Home Assistant shows, `extra` = diagnostics, `pv34` = MPPT 3/4, "
            "`meter` = GoodWe smart meter, `setting` = writable, `bridge` = added by the goodwe-modbus bridge.\n\n"
            + "\n".join(rows)
            + "\n\n## GW10KT-DT templates\n\nObjects in `VI_GoodWe_GW10KT-DT_EN.xml` / `_NL.xml`, in this order:\n\n"
            + "\n".join(names)
            + "\n\n## Error codes (30130)\n\n| Bit | Value | Meaning |\n|---|---|---|\n" + bits(ERROR_BITS)
            + "\n\n## Derating mode (30165)\n\n| Bit | Value | Meaning |\n|---|---|---|\n" + bits(DERATING_BITS) + "\n")


TEMPLATES = ROOT / "loxone/templates"
OUTPUTS = {
    TEMPLATES / "VI_GoodWe_GW10KT-DT_EN.xml": lambda: gw10kt_dt_input("en"),
    TEMPLATES / "VO_GoodWe_GW10KT-DT_EN.xml": lambda: udp_output(TEXT["en"], "GoodWe GW10KT-DT (EN)"),
    TEMPLATES / "VI_GoodWe_GW10KT-DT_NL.xml": lambda: gw10kt_dt_input("nl"),
    TEMPLATES / "VO_GoodWe_GW10KT-DT_NL.xml": lambda: udp_output(TEXT["nl"], "GoodWe GW10KT-DT (NL)"),
    TEMPLATES / "VI_GoodWe_DT_UDP.xml": all_registers_input,
    TEMPLATES / "VO_GoodWe_DT_UDP.xml": lambda: udp_output(TEXT["en"], "GoodWe DT (UDP requests)"),
    TEMPLATES / "MB_GoodWe_DT.xml": lambda: modbus_template(
        native({MAIN, SETTING}), "GoodWe DT inverter (Modbus)", NATIVE_HINT),
    TEMPLATES / "MB_GoodWe_DT_full.xml": lambda: modbus_template(
        native({MAIN, EXTRA, PV34, METER, SETTING}), "GoodWe DT inverter (Modbus, all registers)", NATIVE_HINT),
    TEMPLATES / "MB_GoodWe_DT_Bridge.xml": lambda: modbus_template(
        native({MAIN, SETTING}) + [r for r in BRIDGE_REGISTERS if PV34 not in r.tags],
        "GoodWe DT via goodwe-modbus bridge", BRIDGE_HINT),
    ROOT / "docs/registers.md": markdown,
}


def validate(path: Path, content: str) -> None:
    root = ElementTree.fromstring(content.encode())  # must be well-formed
    assert not re.search(r'="[^"]*\'', content), f"{path.name}: raw apostrophe in an attribute"
    titles = [e.get("Title") for e in root if e.get("Title")]
    assert len(titles) == len(set(titles)), f"{path.name}: duplicate titles"
    assert all(not e.get("Comment") for e in root.iter()), f"{path.name}: display name (Comment) set"
    if root.tag == "Modbus":
        sensors = [int(c.get("ModbusAddress")) for c in root.iter("ModbusCmd") if c.get("Sensor") == "true"]
        assert len(sensors) == len(set(sensors)), f"{path.name}: sensors share an address"


def main() -> int:
    stale = []
    expected = set(OUTPUTS)
    for path in TEMPLATES.glob("*.xml"):
        if path not in expected:
            if "--check" in sys.argv:
                stale.append(f"{path.name} (no longer generated)")
            else:
                path.unlink()
                print(f"Removed {path.relative_to(ROOT)}")
    for path, build in OUTPUTS.items():
        content = build()
        if path.suffix == ".xml":
            validate(path, content)
        if "--check" in sys.argv:
            if not path.exists() or path.read_text(encoding="utf-8-sig") != content:
                stale.append(path.name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8-sig" if path.suffix == ".xml" else "utf-8")
            print(f"Wrote {path.relative_to(ROOT)}")
    if stale:
        print("Out of date, run tools/build_templates.py: " + ", ".join(stale))
        return 1
    if "--check" in sys.argv:
        print("OK: templates and docs are up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
