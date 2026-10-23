from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

import pytest
from goodwe.dt import DT
from goodwe.modbus import create_modbus_rtu_request
from goodwe.protocol import ModbusRtuReadCommand, ProtocolResponse

from conftest import fixture_registers
from goodwe_modbus.registers import BY_ADDRESS, REGISTERS, RUNTIME_BLOCK, Kind

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import build_templates  # noqa: E402

# goodwe library sensor id -> our register
LIBRARY_IDS = {
    "vpv1": 30103, "ipv1": 30104, "vpv2": 30105, "ipv2": 30106, "vline1": 30115, "vline2": 30116, "vline3": 30117,
    "vgrid1": 30118, "vgrid2": 30119, "vgrid3": 30120, "igrid1": 30121, "igrid2": 30122, "igrid3": 30123,
    "fgrid1": 30124, "fgrid2": 30125, "fgrid3": 30126, "total_inverter_power": 30127, "work_mode": 30129,
    "error_codes": 30130, "warning_code": 30132, "apparent_power": 30133, "reactive_power": 30135,
    "power_factor": 30139, "temperature": 30141, "e_day": 30144, "e_total": 30145, "h_total": 30147,
    "safety_country": 30149, "funbit": 30162, "vbus": 30163, "vnbus": 30164, "derating_mode": 30165, "rssi": 30172,
}


def udp_reply() -> bytes:
    """The WiFi module's answer to the runtime block request: AA 55, Modbus RTU response, CRC."""
    start, count = RUNTIME_BLOCK
    registers = fixture_registers()
    data = b"".join(registers[a].to_bytes(2, "big") for a in range(start, start + count))
    body = bytes([0x7F, 3, len(data)]) + data
    crc = build_templates.crc16(body)
    return b"\xaa\x55" + body + bytes([crc & 0xFF, crc >> 8])


def words_of(register):
    registers = fixture_registers()
    return [registers[register.address + i] for i in range(register.words)]


def test_decoding_matches_goodwe_library():
    start, count = RUNTIME_BLOCK
    inverter = DT("test", 8899)
    response = ProtocolResponse(udp_reply(), ModbusRtuReadCommand(0x7F, start, count))
    library = inverter._map_response(response, inverter.sensors())
    for sensor_id, address in LIBRARY_IDS.items():
        assert BY_ADDRESS[address].decode(words_of(BY_ADDRESS[address])) == pytest.approx(library[sensor_id]), sensor_id
    assert library["e_total"] == pytest.approx(22151.4)


def test_register_layout():
    addresses = {}
    for reg in REGISTERS:
        for i in range(reg.words):
            assert reg.address + i not in addresses, f"{reg.name} overlaps {addresses.get(reg.address + i)}"
            addresses[reg.address + i] = reg.name


@pytest.mark.parametrize("function,address,value", [(3, 30100, 73), (6, 40327, 1), (6, 40336, 70)])
def test_udp_frames_match_goodwe_library(function, address, value):
    assert build_templates.rtu_frame(function, address, value) == create_modbus_rtu_request(0x7F, function, address, value)


def loxone_match(check: str, data: bytes, signed: bool) -> int | None:
    """Command recognition as the Miniserver applies it: \\sN skips N bytes, \\1..\\4 are value bytes (1 = LSB)."""
    position, value, size = 0, 0, 0
    for token in re.findall(r"\\s\d+|\\[1-4]", check):
        if token.startswith("\\s"):
            position += int(token[2:])
            continue
        if position >= len(data):
            return None
        value |= data[position] << 8 * (int(token[1]) - 1)
        position, size = position + 1, size + 1
    if signed and value >= 1 << (8 * size - 1):
        value -= 1 << 8 * size
    return value


UDP_INPUTS = {
    "all": build_templates.all_registers_input,
    "en": lambda: build_templates.gw10kt_dt_input("en"),
    "nl": lambda: build_templates.gw10kt_dt_input("nl"),
}


def udp_commands(variant: str) -> list:
    return list(ElementTree.fromstring(UDP_INPUTS[variant]().encode()).iter("VirtualInUdpCmd"))


@pytest.mark.parametrize("variant", UDP_INPUTS)
def test_udp_input_parses_the_real_reply(variant):
    reply = udp_reply()
    for cmd in udp_commands(variant):
        register = BY_ADDRESS[build_templates.check_address(cmd.get("Check"))]
        raw = loxone_match(cmd.get("Check"), reply, cmd.get("Signed") == "true")
        value = raw * float(cmd.get("DestValHigh")) / float(cmd.get("SourceValHigh"))
        assert value == pytest.approx(register.decode(words_of(register))), cmd.get("Title")


def test_udp_inputs_cover_the_right_registers():
    everything = [r.address for r in REGISTERS if 30100 <= r.address <= 30172]
    assert [build_templates.check_address(c.get("Check")) for c in udp_commands("all")] == everything
    english = [(c.get("Check"), c.get("Unit")) for c in udp_commands("en")]
    assert english == [(c.get("Check"), c.get("Unit")) for c in udp_commands("nl")]
    assert len(english) == len(build_templates.GW10KT_DT)


@pytest.mark.parametrize("variant", UDP_INPUTS)
def test_udp_input_ignores_write_echoes(variant):
    # A write reply (AA 55 7F 06 addr value CRC, 10 bytes) must not match any value pattern.
    echo = b"\xaa\x55" + build_templates.rtu_frame(6, 40336, 70)
    for cmd in udp_commands(variant):
        assert loxone_match(cmd.get("Check"), echo, False) is None, cmd.get("Title")


def test_modbus_templates_use_32_bit_types():
    root = ElementTree.fromstring(build_templates.OUTPUTS[ROOT / "loxone/templates/MB_GoodWe_DT.xml"]().encode())
    by_address = {int(c.get("ModbusAddress")): c for c in root.iter("ModbusCmd") if c.get("Sensor") == "true"}
    assert by_address[30145].get("ModbusDataType") == str(build_templates.DATA_TYPE[Kind.U32])
    assert by_address[30141].get("ModbusDataType") == str(build_templates.DATA_TYPE[Kind.S16])


def test_templates_are_up_to_date():
    result = subprocess.run([sys.executable, str(ROOT / "tools/build_templates.py"), "--check"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
