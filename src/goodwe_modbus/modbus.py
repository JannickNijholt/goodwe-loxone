"""Minimal asyncio Modbus TCP server: FC3/FC4 reads, FC6/FC16 writes (from the Onecta bridge)."""

from __future__ import annotations

import asyncio
import logging
import struct
from typing import Protocol

_LOGGER = logging.getLogger(__name__)

READ_HOLDING = 3
READ_INPUT = 4
WRITE_SINGLE = 6
WRITE_MULTIPLE = 16

ILLEGAL_FUNCTION = 0x01
ILLEGAL_ADDRESS = 0x02
ILLEGAL_VALUE = 0x03
DEVICE_FAILURE = 0x04
GATEWAY_TARGET_FAILED = 0x0B

MAX_READ = 125
MAX_WRITE = 123


class ModbusError(Exception):
    def __init__(self, code: int):
        super().__init__(f"Modbus exception {code:#04x}")
        self.code = code


class Handler(Protocol):
    def read(self, unit: int, table: str, address: int, count: int) -> list[int]: ...
    def write(self, unit: int, address: int, words: list[int]) -> None: ...


def handle_pdu(handler: Handler, unit: int, pdu: bytes) -> bytes:
    """Process one request PDU and return the response PDU."""
    function = pdu[0]
    try:
        if function in (READ_HOLDING, READ_INPUT):
            if len(pdu) != 5:
                raise ModbusError(ILLEGAL_VALUE)
            address, count = struct.unpack(">HH", pdu[1:5])
            if not 1 <= count <= MAX_READ or address + count > 0x10000:
                raise ModbusError(ILLEGAL_VALUE)
            table = "holding" if function == READ_HOLDING else "input"
            words = handler.read(unit, table, address, count)
            return struct.pack(f">BB{count}H", function, count * 2, *words)
        if function == WRITE_SINGLE:
            if len(pdu) != 5:
                raise ModbusError(ILLEGAL_VALUE)
            address, value = struct.unpack(">HH", pdu[1:5])
            handler.write(unit, address, [value])
            return pdu
        if function == WRITE_MULTIPLE:
            if len(pdu) < 6:
                raise ModbusError(ILLEGAL_VALUE)
            address, count, byte_count = struct.unpack(">HHB", pdu[1:6])
            if not 1 <= count <= MAX_WRITE or byte_count != count * 2 or len(pdu) != 6 + byte_count:
                raise ModbusError(ILLEGAL_VALUE)
            words = list(struct.unpack(f">{count}H", pdu[6:]))
            handler.write(unit, address, words)
            return struct.pack(">BHH", function, address, count)
        raise ModbusError(ILLEGAL_FUNCTION)
    except ModbusError as err:
        return bytes([function | 0x80, err.code])
    except Exception:  # never let a handler bug kill the connection
        _LOGGER.exception("Modbus handler failed")
        return bytes([function | 0x80, DEVICE_FAILURE])


async def serve_connection(handler: Handler, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    _LOGGER.debug("Modbus client connected: %s", peer)
    try:
        while True:
            header = await reader.readexactly(7)
            transaction, protocol, length, unit = struct.unpack(">HHHB", header)
            if length < 2 or length > 256:
                break
            pdu = await reader.readexactly(length - 1)
            if protocol != 0:
                continue
            response = handle_pdu(handler, unit, pdu)
            writer.write(struct.pack(">HHHB", transaction, 0, len(response) + 1, unit) + response)
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    finally:
        _LOGGER.debug("Modbus client disconnected: %s", peer)
        writer.close()


async def start_server(handler: Handler, host: str, port: int) -> asyncio.base_events.Server:
    return await asyncio.start_server(lambda r, w: serve_connection(handler, r, w), host, port)
