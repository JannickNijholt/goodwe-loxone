import asyncio
from datetime import date, timedelta

import pytest

from goodwe_modbus.bridge import Bridge
from goodwe_modbus.config import Config
from goodwe_modbus.modbus import GATEWAY_TARGET_FAILED, ILLEGAL_ADDRESS, ILLEGAL_FUNCTION, ILLEGAL_VALUE, ModbusError
from goodwe_modbus.registers import (
    BRIDGE_DATA_AGE, BRIDGE_LAST_WRITE, BRIDGE_STATUS, PV1_POWER, PV2_POWER, PV_POWER, STATUS_OFFLINE, STATUS_ONLINE,
    STATUS_STARTING, SYNC_CLOCK, WRITE_BLOCKED, WRITE_OK, WRITE_PENDING, WRITE_REJECTED,
)


class Clock:
    def __init__(self):
        self.now = 1_000_000.0
        self.day = date(2026, 10, 23)

    def __call__(self):
        return self.now


def make(inverter, **config):
    clock = Clock()
    settings = {"host": "test", "allow_writes": True, "write_debounce": 0, "offline_after": 3}
    settings.update(config)
    return Bridge(Config(**settings), inverter, clock=clock, today=lambda: clock.day), clock


def run(coro):
    return asyncio.run(coro)


def read1(bridge, address, count=1):
    return bridge.read(247, "holding", address, count)


def test_first_poll_reads_runtime_device_meter_and_settings(inverter):
    async def scenario():
        bridge, _ = make(inverter)
        await bridge.poll()
        return bridge
    bridge = run(scenario())
    assert bridge.status == STATUS_ONLINE
    assert bridge.device == {"serial": "5010KDTS000X0000", "model": "GW10KT-DT", "firmware": "15.15.35"}
    assert bridge.meter is False  # no reply for the meter block
    assert read1(bridge, 30145, 2) == [3, 24906]  # 22151.4 kWh
    assert read1(bridge, 40336) == [2]
    with pytest.raises(ModbusError) as err:
        read1(bridge, 30195, 2)
    assert err.value.code == ILLEGAL_ADDRESS


def test_meter_is_not_polled_every_time(inverter):
    async def scenario():
        bridge, clock = make(inverter)
        await bridge.poll()
        inverter.reads.clear()
        clock.now += 30
        await bridge.poll()
    run(scenario())
    assert inverter.reads == [(30100, 73)]


def test_calculated_power(inverter):
    async def scenario():
        bridge, _ = make(inverter)
        await bridge.poll()
        return bridge
    bridge = run(scenario())
    pv1, pv2 = read1(bridge, PV1_POWER, 2)
    assert pv1 == round(398.4 * 2.1) and pv2 == round(396.7 * 2.2)
    assert read1(bridge, PV_POWER) == [pv1 + pv2]  # PV3/PV4 read 65535 = absent, count as 0


def test_no_data_yet_is_a_gateway_error(inverter):
    inverter.online = False
    async def scenario():
        bridge, _ = make(inverter)
        await bridge.poll()
        return bridge
    bridge = run(scenario())
    assert bridge.status == STATUS_STARTING
    with pytest.raises(ModbusError) as err:
        read1(bridge, 30127, 2)
    assert err.value.code == GATEWAY_TARGET_FAILED
    assert read1(bridge, BRIDGE_STATUS) == [STATUS_STARTING]


def test_night_zeroes_live_values_and_resets_energy_today_at_midnight(inverter):
    async def scenario():
        bridge, clock = make(inverter)
        await bridge.poll()
        inverter.online = False
        for _ in range(2):
            clock.now += 60
            await bridge.poll()
        assert bridge.status == STATUS_ONLINE  # isolated UDP losses don't count
        assert read1(bridge, 30127, 2) == [0, 1725]
        clock.now += 60
        await bridge.poll()
        assert bridge.status == STATUS_OFFLINE
        assert read1(bridge, 30127, 2) == [0, 0]  # AC power
        assert read1(bridge, PV_POWER) == [0]
        assert read1(bridge, 30145, 2) == [3, 24906]  # total kept
        assert read1(bridge, 30144) == [23]  # today kept until midnight
        assert read1(bridge, BRIDGE_DATA_AGE) == [180]
        clock.day += timedelta(days=1)
        assert read1(bridge, 30144) == [0]
        inverter.online = True
        await bridge.poll()
        assert bridge.status == STATUS_ONLINE
        assert read1(bridge, 30127, 2) == [0, 1725]
    run(scenario())


def test_writes_disabled_by_default(inverter):
    async def scenario():
        bridge, _ = make(inverter, allow_writes=False)
        await bridge.poll()
        with pytest.raises(ModbusError) as err:
            bridge.write(247, 40336, [50])
        return err.value.code
    assert run(scenario()) == ILLEGAL_FUNCTION
    assert inverter.writes == []


@pytest.mark.parametrize("address,value,code", [(40336, 101, ILLEGAL_VALUE), (40327, 2, ILLEGAL_VALUE),
                                                (30127, 0, ILLEGAL_ADDRESS), (40313, 1, ILLEGAL_ADDRESS)])
def test_write_validation(inverter, address, value, code):
    bridge, _ = make(inverter)
    with pytest.raises(ModbusError) as err:
        bridge.write(247, address, [value])
    assert err.value.code == code


def test_write_is_debounced_and_sent_once(inverter):
    async def scenario():
        bridge, _ = make(inverter, write_debounce=0.05)
        await bridge.poll()
        for value in (30, 40, 50):  # slider moving
            bridge.write(247, 40336, [value])
        assert read1(bridge, 40336) == [50]  # read-back shows the requested value at once
        await asyncio.sleep(0.1)
        return bridge
    bridge = run(scenario())
    assert inverter.writes == [(40336, [50])]
    assert read1(bridge, BRIDGE_LAST_WRITE) == [WRITE_OK]


def test_unchanged_value_is_not_written(inverter):
    async def scenario():
        bridge, _ = make(inverter)
        await bridge.poll()
        bridge.write(247, 40336, [2])  # already 2 on the inverter
        await asyncio.sleep(0.01)
    run(scenario())
    assert inverter.writes == []


def test_write_waits_for_the_inverter_at_night(inverter):
    async def scenario():
        bridge, _ = make(inverter, offline_after=1)
        await bridge.poll()
        inverter.online = False
        await bridge.poll()
        bridge.write(247, 40327, [1])
        await asyncio.sleep(0.01)
        assert inverter.writes == []
        assert read1(bridge, BRIDGE_LAST_WRITE) == [WRITE_PENDING]
        inverter.online = True
        await bridge.poll()
        return bridge
    bridge = run(scenario())
    assert inverter.writes == [(40327, [1])]
    assert bridge.desired == {}


def test_rejected_write_is_dropped(inverter):
    inverter.reject_writes = True
    async def scenario():
        bridge, _ = make(inverter)
        await bridge.poll()
        bridge.write(247, 40336, [50])
        await asyncio.sleep(0.01)
        return bridge
    bridge = run(scenario())
    assert read1(bridge, BRIDGE_LAST_WRITE) == [WRITE_REJECTED]
    assert read1(bridge, 40336) == [2]


def test_hourly_write_limit(inverter):
    async def scenario():
        bridge, clock = make(inverter, max_writes_per_hour=2)
        await bridge.poll()
        for value in (10, 20, 30):
            bridge.write(247, 40336, [value])
            await asyncio.sleep(0.01)
            clock.now += 60
        return bridge
    bridge = run(scenario())
    assert inverter.writes == [(40336, [10]), (40336, [20])]
    assert read1(bridge, BRIDGE_LAST_WRITE) == [WRITE_BLOCKED]


def test_clock_sync(inverter):
    async def scenario():
        bridge, _ = make(inverter)
        await bridge.poll()
        bridge.write(247, SYNC_CLOCK, [1])
        await asyncio.sleep(0.01)
    run(scenario())
    (address, words), = inverter.writes
    assert address == 40313 and len(words) == 3 and words[0] >> 8 >= 26
