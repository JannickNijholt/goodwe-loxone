"""Loxone (Modbus TCP client) -> bridge -> goodwe library over UDP -> simulated WiFi module."""

import asyncio
import struct

from conftest import fixture_registers
from goodwe_modbus.bridge import Bridge
from goodwe_modbus.config import Config
from goodwe_modbus.inverter import GoodweClient, NoResponse
from goodwe_modbus.modbus import start_server


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


class WifiModule(asyncio.DatagramProtocol):
    """Answers Modbus RTU over UDP like a GoodWe WiFi module: AA 55 + RTU response. Silent for unknown blocks."""

    def __init__(self):
        self.registers = fixture_registers()
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if len(data) != 8 or crc16(data[:6]) != data[6] | data[7] << 8:
            return
        unit, function, address, value = struct.unpack(">BBHH", data[:6])
        if function == 3:
            if any(a not in self.registers for a in range(address, address + value)):
                return
            payload = b"".join(self.registers[a].to_bytes(2, "big") for a in range(address, address + value))
            body = bytes([unit, 3, len(payload)]) + payload
        elif function == 6:
            self.registers[address] = value
            body = data[:6]
        else:
            return
        crc = crc16(body)
        self.transport.sendto(b"\xaa\x55" + body + bytes([crc & 0xFF, crc >> 8]), addr)


async def modbus_read(port, address, count):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(struct.pack(">HHHBBHH", 1, 0, 6, 247, 3, address, count))
    header = await reader.readexactly(7)
    body = await reader.readexactly(struct.unpack(">H", header[4:6])[0] - 1)
    writer.close()
    return list(struct.unpack(f">{count}H", body[2:]))


async def modbus_write(port, address, value):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(struct.pack(">HHHBBHH", 2, 0, 6, 247, 6, address, value))
    await reader.readexactly(12)
    writer.close()


def test_loxone_reads_and_writes_through_the_bridge():
    async def scenario():
        loop = asyncio.get_running_loop()
        transport, module = await loop.create_datagram_endpoint(WifiModule, local_addr=("127.0.0.1", 0))
        udp_port = transport.get_extra_info("sockname")[1]
        # Any port other than 8899 selects Modbus TCP in GoodweClient, so patch the UDP choice in.
        client = GoodweClient("127.0.0.1", 8899, timeout=1, retries=1)
        client._protocol._port = udp_port
        bridge = Bridge(Config(host="127.0.0.1", allow_writes=True, write_debounce=0), client)
        server = await start_server(bridge, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            await bridge.poll()
            assert bridge.device["model"] == "GW10KT-DT"
            assert bridge.meter is False
            assert await modbus_read(port, 30145, 2) == [3, 24906]
            await modbus_write(port, 40336, 70)
            await asyncio.sleep(0.2)
            assert module.registers[40336] == 70
            try:
                await client.read(30195, 2)
            except NoResponse:
                pass
            else:
                raise AssertionError("meter block should not answer")
        finally:
            server.close()
            await client.close()
            transport.close()
    asyncio.run(scenario())
