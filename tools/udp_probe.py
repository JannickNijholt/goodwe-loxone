#!/usr/bin/env python3
"""Find out whether the native UDP route can work on your Miniserver.

Point the Virtual Output of loxone/templates/VO_GoodWe_DT_UDP.xml at the computer running this script
instead of the inverter. It prints the source port of every request the Miniserver sends. The GoodWe WiFi
module answers to exactly that port, so a Virtual UDP Input only sees the replies if it is stable.

With --relay INVERTER_IP the request is passed to the inverter and its reply sent back to the Miniserver
from port 8899, just like the WiFi module does. If the Virtual UDP Input then shows values, the native
route works.

Usage: python3 tools/udp_probe.py [--port 8899] [--relay 192.168.1.50]
No dependencies; read requests only are relayed, writes are printed and dropped.
"""

from __future__ import annotations

import argparse
import functools
import socket
import time

print = functools.partial(print, flush=True)  # noqa: A001  (live output when piped or logged)

READ_HOLDING = 3


def describe(data: bytes) -> str:
    if len(data) == 8:
        address, value = int.from_bytes(data[2:4], "big"), int.from_bytes(data[4:6], "big")
        if data[1] == READ_HOLDING:
            return f"read {value} registers from {address}"
        return f"function {data[1]} register {address} value {value}"
    return f"{len(data)} bytes, not a GoodWe request"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--relay", metavar="INVERTER_IP", help="forward read requests to the inverter")
    args = parser.parse_args()

    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("0.0.0.0", args.port))
    print(f"Listening on UDP {args.port}. Ctrl+C to stop.")
    ports: list[int] = []
    while True:
        data, (host, port) = listener.recvfrom(1024)
        ports.append(port)
        stamp = time.strftime("%H:%M:%S")
        print(f"{stamp} from {host}:{port}  {describe(data)}  ({data.hex()})")
        if len(set(ports)) == 1 and len(ports) >= 3:
            verdict = "replies go to port 8899" if port == 8899 else f"replies go to fixed port {port}"
            print(f"         source port stable: {verdict}")
        elif len(set(ports)) > 1:
            print(f"         source port changes ({sorted(set(ports))}): a Virtual UDP Input can't receive the replies")
        if args.relay and len(data) == 8 and data[1] == READ_HOLDING:
            upstream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            upstream.settimeout(2)
            upstream.sendto(data, (args.relay, 8899))
            try:
                reply = upstream.recv(1024)
            except socket.timeout:
                print("         no reply from the inverter (asleep?)")
                continue
            finally:
                upstream.close()
            listener.sendto(reply, (host, port))
            print(f"         relayed {len(reply)}-byte reply to {host}:{port}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
