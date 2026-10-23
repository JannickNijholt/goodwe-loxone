import json
from pathlib import Path

import pytest

from goodwe_modbus.inverter import NoResponse, Rejected

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "gw10kt_dt.json").read_text())


def fixture_registers() -> dict[int, int]:
    """Address -> raw word, as read from the GW10KT-DT."""
    registers = {}
    for start, words in FIXTURE["blocks"].items():
        registers.update(zip(range(int(start), int(start) + len(words)), words))
    return registers


class FakeInverter:
    """Answers reads from the fixture like the real inverter: no reply for blocks it doesn't have."""

    def __init__(self):
        self.registers = fixture_registers()
        self.online = True
        self.reject_writes = False
        self.reads: list[tuple[int, int]] = []
        self.writes: list[tuple[int, list[int]]] = []

    async def read(self, address, count):
        self.reads.append((address, count))
        if not self.online or any(a not in self.registers for a in range(address, address + count)):
            raise NoResponse("MaxRetriesException")
        return [self.registers[a] for a in range(address, address + count)]

    async def write(self, address, words):
        if not self.online:
            raise NoResponse("MaxRetriesException")
        if self.reject_writes:
            raise Rejected("ILLEGAL DATA VALUE")
        self.writes.append((address, list(words)))
        self.registers.update(zip(range(address, address + len(words)), words))

    async def close(self):
        pass


@pytest.fixture
def inverter() -> FakeInverter:
    return FakeInverter()
