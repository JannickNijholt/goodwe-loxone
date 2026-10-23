"""Entry point: python -m goodwe_modbus (or the goodwe-modbus console script)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys

from .bridge import Bridge
from .config import Config
from .inverter import GoodweClient
from .modbus import start_server
from .web import start_web

_LOGGER = logging.getLogger("goodwe_modbus")


async def run(config: Config) -> None:
    client = GoodweClient(config.host, config.port, config.comm_address, config.timeout, config.retries)
    bridge = Bridge(config, client)
    modbus = await start_server(bridge, config.modbus_host, config.modbus_port)
    web = await start_web(bridge, config.web_host, config.web_port) if config.web_port else None
    _LOGGER.info("Inverter %s:%d, Modbus TCP on %s:%d, status page on port %s, writes %s", config.host, config.port,
                 config.modbus_host, config.modbus_port, config.web_port or "off",
                 "enabled" if config.allow_writes else "disabled")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    worker = asyncio.create_task(bridge.run())
    await stop.wait()
    _LOGGER.info("Stopping")
    worker.cancel()
    for server in (modbus, web):
        if server:
            server.close()
            await server.wait_closed()
    await client.close()


def main() -> None:
    config = Config()
    logging.basicConfig(level=config.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("goodwe").setLevel(logging.WARNING)
    if not config.host:
        sys.exit("Set GOODWE_HOST to the inverter's IP address (see deploy/goodwe-modbus.env)")
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
