"""Read-only status page (GET /) and JSON (GET /api/status) on a tiny asyncio HTTP server."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from html import escape

from . import __version__
from .bridge import Bridge
from .registers import BRIDGE, DERATING_BITS, ERROR_BITS, NOT_AVAILABLE, STATUS_OFFLINE, STATUS_ONLINE, bits_text

_LOGGER = logging.getLogger(__name__)

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--text:#1d2330;--muted:#667085;--line:#e4e7ec;--ok:#12805c;--warn:#b54708}
@media (prefers-color-scheme:dark){:root{--bg:#0f1218;--card:#171b23;--text:#e6e9ef;--muted:#98a2b3;--line:#2a303c;--ok:#3ccb8f;--warn:#f79009}}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--text)}
main{max-width:1000px;margin:0 auto;padding:24px 16px 48px}h1{font-size:22px;margin:0}h2{font-size:16px;margin:0 0 12px}
small,.muted{color:var(--muted)}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin-top:16px;overflow-x:auto}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}.stat b{display:block;font-size:20px}
.ok{color:var(--ok)}.warn{color:var(--warn)}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}ul{padding-left:18px;margin:0;font-size:13px}
"""
STATUS = {STATUS_ONLINE: ("online", "ok"), STATUS_OFFLINE: ("not answering (normal at night)", "warn")}


def fmt(value: float | None, unit: str) -> str:
    if value is None:
        return "–"
    return f"{value:g} {unit}".strip()


def status_json(bridge: Bridge) -> dict:
    values = {}
    for register, words, value in bridge.snapshot():
        values[str(register.address)] = {"name": register.name, "raw": words, "value": value, "unit": register.unit}
    return {"version": __version__, "status": bridge.status, "failures": bridge.failures, "last_ok": bridge.last_ok,
            "device": bridge.device, "meter": bridge.meter, "writes_enabled": bridge.config.allow_writes,
            "registers": values}


def status_html(bridge: Bridge) -> str:
    label, css = STATUS.get(bridge.status, ("starting", "warn"))
    age = "–" if bridge.last_ok is None else datetime.fromtimestamp(bridge.last_ok).strftime("%d-%m %H:%M:%S")
    device = bridge.device
    stats = "".join(f"<div class=stat><small>{escape(k)}</small><b>{v}</b></div>" for k, v in (
        ("Inverter", f"<span class={css}>{escape(label)}</span>"),
        ("Model", escape(device.get("model", "–"))),
        ("Firmware", escape(device.get("firmware", "–"))),
        ("Last good read", age),
        ("Smart meter", {None: "–", True: "yes", False: "no"}[bridge.meter]),
        ("Writes", "enabled" if bridge.config.allow_writes else "disabled"),
    ))
    rows = []
    for register, words, value in bridge.snapshot():
        raw = "–" if words is None else " ".join(str(w) for w in words)
        shown = fmt(value, register.unit)
        if words is not None and words == [NOT_AVAILABLE] * len(words) and register.group != BRIDGE:
            shown = "not available"
        elif register.address == 30130 and value:
            shown = escape(bits_text(int(value), ERROR_BITS))
        elif register.address == 30165 and value:
            shown = escape(bits_text(int(value), DERATING_BITS))
        rows.append(f"<tr><td>{register.address}</td><td>{escape(register.name)}</td><td>{shown}</td>"
                    f"<td class=muted>{raw}</td></tr>")
    events = "".join(f"<li>{datetime.fromtimestamp(t).strftime('%d-%m %H:%M:%S')} {escape(m)}</li>"
                     for t, m in bridge.events) or "<li class=muted>Nothing yet</li>"
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'><meta http-equiv=refresh content=30>"
            f"<title>GoodWe bridge</title><style>{CSS}</style></head><body><main>"
            f"<h1>GoodWe → Modbus bridge</h1><small>{__version__} · Modbus TCP port {bridge.config.modbus_port} · "
            f"inverter {escape(bridge.config.host)}:{bridge.config.port}</small>"
            f"<div class=card><div class=grid>{stats}</div></div>"
            f"<div class=card><h2>Registers</h2><table><tr><th>Address</th><th>Name</th><th>Value</th><th>Raw</th></tr>"
            f"{''.join(rows)}</table></div><div class=card><h2>Activity</h2><ul>{events}</ul></div></main></body></html>")


async def handle(bridge: Bridge, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        method, path = request.split(b" ", 2)[:2]
        path = path.split(b"?")[0]
        if method != b"GET":
            status, body, kind = "405 Method Not Allowed", b"GET only", "text/plain"
        elif path == b"/":
            status, body, kind = "200 OK", status_html(bridge).encode(), "text/html; charset=utf-8"
        elif path == b"/api/status":
            status, body, kind = "200 OK", json.dumps(status_json(bridge)).encode(), "application/json"
        else:
            status, body, kind = "404 Not Found", b"not found", "text/plain"
        writer.write(f"HTTP/1.1 {status}\r\nContent-Type: {kind}\r\nContent-Length: {len(body)}\r\n"
                     f"Connection: close\r\n\r\n".encode() + body)
        await writer.drain()
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError, ConnectionError):
        pass
    except Exception:  # never let the status page take the bridge down
        _LOGGER.exception("Web request failed")
    finally:
        writer.close()


async def start_web(bridge: Bridge, host: str, port: int) -> asyncio.base_events.Server:
    return await asyncio.start_server(lambda r, w: handle(bridge, r, w), host, port)
