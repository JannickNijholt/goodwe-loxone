"""Polls the inverter, serves Modbus reads from the cache and passes allowed writes on."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import date, datetime
from typing import Callable

from .config import Config
from .inverter import Client, NoResponse, Rejected
from .modbus import GATEWAY_TARGET_FAILED, ILLEGAL_ADDRESS, ILLEGAL_FUNCTION, ILLEGAL_VALUE, ModbusError
from .registers import (
    BRIDGE_DATA_AGE, BRIDGE_FAILURES, BRIDGE_LAST_WRITE, BRIDGE_REGISTERS, BRIDGE_STATUS, BRIDGE_WRITES_ENABLED,
    BY_ADDRESS, CALCULATED_POWER, CLOCK_ADDRESS, DAILY, DEVICE_BLOCK, METER, METER_BLOCK, NOT_AVAILABLE,
    PV1_POWER, PV2_POWER, PV3_POWER, PV4_POWER, PV_POWER, REGISTERS, RUNTIME_BLOCK, SETTING_ADDRESSES,
    STATUS_OFFLINE, STATUS_ONLINE, STATUS_STARTING, SYNC_CLOCK, WRITABLE, WRITE_BLOCKED, WRITE_FAILED, WRITE_NONE,
    WRITE_OK, WRITE_PENDING, WRITE_REJECTED, ZERO, Register,
)

_LOGGER = logging.getLogger(__name__)
DEVICE_REFRESH = 6 * 3600
METER_PROBE = 6 * 3600

# Register that covers each address (32-bit values cover two)
COVERING: dict[int, Register] = {r.address + i: r for r in REGISTERS for i in range(r.words)}
BRIDGE_ADDRESSES = {r.address for r in BRIDGE_REGISTERS}
METER_ADDRESSES = range(METER_BLOCK[0], METER_BLOCK[0] + METER_BLOCK[1])


def text(words: list[int]) -> str:
    raw = b"".join(w.to_bytes(2, "big") for w in words if w != NOT_AVAILABLE)
    return raw.decode("ascii", "replace").strip("\x00 ")


def clock_words(now: datetime) -> list[int]:
    return [(now.year - 2000) << 8 | now.month, now.day << 8 | now.hour, now.minute << 8 | now.second]


class Bridge:
    def __init__(self, config: Config, client: Client, clock: Callable[[], float] = time.time,
                 today: Callable[[], date] = date.today):
        self.config = config
        self.client = client
        self.clock = clock
        self.today = today
        self.cache: dict[int, int] = {}
        self.status = STATUS_STARTING
        self.failures = 0
        self.last_ok: float | None = None
        self.last_ok_date: date | None = None
        self.last_error: str | None = None
        self.device: dict[str, str] = {}
        self.meter: bool | None = None  # None = not probed yet
        self.meter_failures = 0
        self.next_device = 0.0
        self.next_meter = 0.0
        self.next_settings = 0.0
        self.desired: dict[int, int] = {}
        self.sync_clock = False
        self.write_times: deque[float] = deque()
        self.last_write = WRITE_NONE
        self.events: deque[tuple[float, str]] = deque(maxlen=60)
        self._flush_task: asyncio.Task | None = None

    def log(self, message: str, level: int = logging.INFO) -> None:
        _LOGGER.log(level, message)
        self.events.appendleft((self.clock(), message))

    # ---- polling ------------------------------------------------------------------------

    async def run(self) -> None:
        while True:
            await self.poll()
            interval = self.config.poll_seconds if self.status == STATUS_ONLINE else self.config.offline_poll_seconds
            await asyncio.sleep(interval)

    async def _read_into_cache(self, address: int, count: int) -> list[int]:
        words = await self.client.read(address, count)
        self.cache.update(zip(range(address, address + count), words))
        return words

    async def poll(self) -> None:
        try:
            await self._read_into_cache(*RUNTIME_BLOCK)
        except (NoResponse, Rejected) as err:
            self.failures += 1
            self.last_error = str(err)
            if self.status == STATUS_ONLINE and self.failures >= self.config.offline_after:
                self.status = STATUS_OFFLINE
                self.log(f"Inverter not answering ({self.failures} reads in a row), serving offline values")
            elif self.status == STATUS_STARTING and self.failures == 1:
                self.log(f"No answer from the inverter yet: {err}", logging.WARNING)
            return
        now = self.clock()
        if self.status != STATUS_ONLINE:
            self.log("Inverter online")
        self.status = STATUS_ONLINE
        self.failures = 0
        self.last_ok = now
        self.last_ok_date = self.today()
        self.last_error = None
        if now >= self.next_device:
            await self._read_device(now)
        if now >= self.next_meter:
            await self._read_meter(now)
        if now >= self.next_settings:
            await self._read_settings(now)
        if self.desired or self.sync_clock:
            await self.flush_writes()

    async def _read_device(self, now: float) -> None:
        try:
            words = await self.client.read(*DEVICE_BLOCK)
        except (NoResponse, Rejected) as err:
            _LOGGER.debug("Device info read failed: %s", err)
            return
        self.cache.update(zip(range(DEVICE_BLOCK[0], DEVICE_BLOCK[0] + DEVICE_BLOCK[1]), words))
        firmware = f"{words[33]}.{words[34]}.{words[35]:02x}"  # 30034-30036, formatted like the goodwe library
        device = {"serial": text(words[3:11]), "model": text(words[11:16]), "firmware": firmware}
        if device != self.device:
            self.log(f"Inverter {device['model']}, firmware {device['firmware']}")
        self.device = device
        self.next_device = now + DEVICE_REFRESH

    async def _read_meter(self, now: float) -> None:
        if self.meter is False:
            self.next_meter = now + METER_PROBE
        try:
            await self._read_into_cache(*METER_BLOCK)
        except (NoResponse, Rejected) as err:
            # Without a meter this block gets no answer at all, so one miss on a first probe is enough.
            self.meter_failures += 1
            if self.meter is not False and (self.meter is None or self.meter_failures >= self.config.offline_after):
                self.log(f"No smart meter data ({err}); trying again in {METER_PROBE // 3600} h")
                self.meter = False
                for address in METER_ADDRESSES:
                    self.cache.pop(address, None)
            self.next_meter = now + METER_PROBE
            return
        if self.meter is not True:
            self.log("Smart meter found")
        self.meter = True
        self.meter_failures = 0
        self.next_meter = 0.0

    async def _read_settings(self, now: float) -> None:
        for address in SETTING_ADDRESSES:
            try:
                await self._read_into_cache(address, 1)
            except (NoResponse, Rejected) as err:
                _LOGGER.debug("Setting %d read failed: %s", address, err)
        self.next_settings = now + self.config.settings_seconds

    # ---- Modbus reads -------------------------------------------------------------------

    def value(self, address: int) -> int | None:
        if address in BRIDGE_ADDRESSES:
            return self._bridge_value(address)
        if address in self.desired:
            return self.desired[address]
        raw = self.cache.get(address)
        if raw is None or self.status == STATUS_ONLINE:
            return raw
        register = COVERING.get(address)
        if register is None:
            return raw
        if register.offline == ZERO:
            return 0
        if register.offline == DAILY and self.last_ok_date != self.today():
            return 0
        return raw

    def _scaled(self, address: int, scale: int) -> float:
        raw = self.value(address)
        return 0.0 if raw in (None, NOT_AVAILABLE) else raw / scale

    def _bridge_value(self, address: int) -> int:
        if address == BRIDGE_STATUS:
            return self.status
        if address == BRIDGE_DATA_AGE:
            return 0xFFFF if self.last_ok is None else min(0xFFFF, int(self.clock() - self.last_ok))
        if address == BRIDGE_FAILURES:
            return min(0xFFFF, self.failures)
        if address == BRIDGE_WRITES_ENABLED:
            return int(self.config.allow_writes)
        if address == BRIDGE_LAST_WRITE:
            return self.last_write
        if address == PV_POWER:
            return min(0xFFFF, sum(self._bridge_value(a) for a in (PV1_POWER, PV2_POWER, PV3_POWER, PV4_POWER)))
        for power, voltage, current in CALCULATED_POWER:
            if address == power:
                return min(0xFFFF, round(self._scaled(voltage, 10) * self._scaled(current, 10)))
        return 0  # SYNC_CLOCK and anything else write-only

    def read(self, unit: int, table: str, address: int, count: int) -> list[int]:
        words = [self.value(a) for a in range(address, address + count)]
        if all(w is not None for w in words):
            return words  # type: ignore[return-value]
        if not self.cache and all(a not in BRIDGE_ADDRESSES for a in range(address, address + count)):
            raise ModbusError(GATEWAY_TARGET_FAILED)  # nothing read from the inverter yet
        raise ModbusError(ILLEGAL_ADDRESS)

    # ---- Modbus writes ------------------------------------------------------------------

    def write(self, unit: int, address: int, words: list[int]) -> None:
        for offset, value in enumerate(words):
            register = WRITABLE.get(address + offset)
            if register is None:
                raise ModbusError(ILLEGAL_ADDRESS)
            low, high = register.write
            if not low <= value <= high:
                raise ModbusError(ILLEGAL_VALUE)
        if not self.config.allow_writes:
            self.log(f"Write to {address} ignored: writes are disabled (GOODWE_ALLOW_WRITES)", logging.WARNING)
            raise ModbusError(ILLEGAL_FUNCTION)
        for offset, value in enumerate(words):
            if address + offset == SYNC_CLOCK:
                self.sync_clock = self.sync_clock or value == 1
            elif self.desired.get(address + offset) != value:
                self.desired[address + offset] = value
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.get_running_loop().create_task(self._flush_later())

    async def _flush_later(self) -> None:
        await asyncio.sleep(self.config.write_debounce)  # collect slider moves
        await self.flush_writes()

    def _write_allowed(self) -> bool:
        now = self.clock()
        while self.write_times and self.write_times[0] < now - 3600:
            self.write_times.popleft()
        return len(self.write_times) < self.config.max_writes_per_hour

    async def flush_writes(self) -> None:
        if self.status != STATUS_ONLINE:
            if self.desired or self.sync_clock:
                self.last_write = WRITE_PENDING
            return
        for address, value in list(self.desired.items()):
            if self.cache.get(address) == value:
                del self.desired[address]
                continue
            await self._send(address, [value], f"{BY_ADDRESS[address].name} = {value}")
        if self.sync_clock:
            await self._send(CLOCK_ADDRESS, clock_words(datetime.now()), "inverter clock sync")

    async def _send(self, address: int, words: list[int], label: str) -> None:
        if not self._write_allowed():
            self.last_write = WRITE_BLOCKED
            self.log(f"Not sent: {label} (more than {self.config.max_writes_per_hour} writes in the last hour)",
                     logging.WARNING)
            self._drop(address)
            return
        try:
            await self.client.write(address, words)
        except Rejected as err:
            self.last_write = WRITE_REJECTED
            self.log(f"Inverter rejected {label}: {err}", logging.WARNING)
            self._drop(address)
            return
        except NoResponse as err:
            self.last_write = WRITE_FAILED
            self.log(f"No answer to {label}, retrying after the next good read: {err}", logging.WARNING)
            return
        self.write_times.append(self.clock())
        self.last_write = WRITE_OK
        self.log(f"Written: {label}")
        if address == CLOCK_ADDRESS:
            self.sync_clock = False
        else:
            self.cache[address] = words[0]
            self._drop(address)

    def _drop(self, address: int) -> None:
        if address == CLOCK_ADDRESS:
            self.sync_clock = False
        else:
            self.desired.pop(address, None)

    # ---- for the web UI -----------------------------------------------------------------

    def snapshot(self) -> list[tuple[Register, list[int] | None, float | None]]:
        rows = []
        for register in REGISTERS + BRIDGE_REGISTERS:
            if register.group == METER and not self.meter:
                continue
            words = [self.value(register.address + i) for i in range(register.words)]
            if any(w is None for w in words):
                rows.append((register, None, None))
            else:
                rows.append((register, words, register.decode(words)))  # type: ignore[arg-type]
        return rows
