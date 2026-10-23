"""Runtime configuration from environment variables (see deploy/goodwe-modbus.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(f"GOODWE_{name}", default)


def _flag(name: str, default: str) -> bool:
    return _env(name, default).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    host: str = field(default_factory=lambda: _env("HOST", ""))
    port: int = field(default_factory=lambda: int(_env("PORT", "8899")))
    comm_address: int = field(default_factory=lambda: int(_env("COMM_ADDRESS", "0x7F"), 0))
    timeout: int = field(default_factory=lambda: int(_env("TIMEOUT", "2")))
    retries: int = field(default_factory=lambda: int(_env("RETRIES", "2")))
    poll_seconds: float = field(default_factory=lambda: float(_env("POLL_SECONDS", "30")))
    # The inverter switches off at night; ask less often while it doesn't answer.
    offline_poll_seconds: float = field(default_factory=lambda: float(_env("OFFLINE_POLL_SECONDS", "60")))
    offline_after: int = field(default_factory=lambda: int(_env("OFFLINE_AFTER", "3")))
    settings_seconds: float = field(default_factory=lambda: float(_env("SETTINGS_SECONDS", "600")))
    modbus_host: str = field(default_factory=lambda: _env("MODBUS_HOST", "0.0.0.0"))
    modbus_port: int = field(default_factory=lambda: int(_env("MODBUS_PORT", "502")))
    web_host: str = field(default_factory=lambda: _env("WEB_HOST", "0.0.0.0"))
    web_port: int = field(default_factory=lambda: int(_env("WEB_PORT", "8080")))
    allow_writes: bool = field(default_factory=lambda: _flag("ALLOW_WRITES", "0"))
    # Settings live in the inverter's EEPROM; protect it from Loxone logic that keeps writing.
    max_writes_per_hour: int = field(default_factory=lambda: int(_env("MAX_WRITES_PER_HOUR", "20")))
    write_debounce: float = field(default_factory=lambda: float(_env("WRITE_DEBOUNCE", "2")))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))
