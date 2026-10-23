"""Talks to the inverter through the goodwe library's transport (the one Home Assistant uses).

Port 8899 is Modbus RTU over UDP via the WiFi module, port 502 is Modbus TCP (LAN/WiFi kit 2.0).
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from goodwe.exceptions import InverterError, RequestRejectedException
from goodwe.protocol import InverterProtocol, TcpInverterProtocol, UdpInverterProtocol

UDP_PORT = 8899
DT_COMM_ADDRESS = 0x7F  # goodwe library default for DT/MS/D-NS/XS inverters


class NoResponse(Exception):
    """The inverter did not answer (switched off at night, network problem)."""


class Rejected(Exception):
    """The inverter answered with a Modbus exception (unsupported register or value)."""


class Client(Protocol):
    async def read(self, address: int, count: int) -> list[int]: ...
    async def write(self, address: int, words: list[int]) -> None: ...
    async def close(self) -> None: ...


class GoodweClient:
    def __init__(self, host: str, port: int = UDP_PORT, comm_address: int = DT_COMM_ADDRESS,
                 timeout: int = 2, retries: int = 2):
        if port == UDP_PORT:
            self._protocol: InverterProtocol = UdpInverterProtocol(host, port, comm_address, timeout, retries)
        else:
            self._protocol = TcpInverterProtocol(host, port, comm_address, timeout, retries)
        self._protocol.keep_alive = True
        self._lock = asyncio.Lock()

    async def _execute(self, command) -> bytes:
        async with self._lock:
            try:
                response = await command.execute(self._protocol)
            except RequestRejectedException as err:
                raise Rejected(err.message) from None
            except InverterError as err:
                raise NoResponse(str(err) or type(err).__name__) from None
            return response.response_data()

    async def read(self, address: int, count: int) -> list[int]:
        data = await self._execute(self._protocol.read_command(address, count))
        if len(data) != 2 * count:
            raise NoResponse(f"short response: {len(data)} bytes for {count} registers")
        return [int.from_bytes(data[i:i + 2], "big") for i in range(0, len(data), 2)]

    async def write(self, address: int, words: list[int]) -> None:
        if len(words) == 1:
            # The library sends the value as a signed 16-bit number.
            value = words[0] - 0x10000 if words[0] >= 0x8000 else words[0]
            await self._execute(self._protocol.write_command(address, value))
        else:
            payload = b"".join(w.to_bytes(2, "big") for w in words)
            await self._execute(self._protocol.write_multi_command(address, payload))

    async def close(self) -> None:
        self._protocol.keep_alive = False
        await self._protocol.close()
