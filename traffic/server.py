"""Application servers in the data network (behind the UPF), one process per service.

  UDP echo   :5000  voice packets and gaming RTT probes are echoed with a server timestamp
  UDP stream :5001  "START <kbps> <seconds> <pkt_bytes> <tag>" -> paced downlink stream
                    (cloud-gaming video), each packet carries seq + send timestamp
  TCP        :8000  minimal HTTP/1.1 "GET /bytes/<n>" -> n bytes (video segments, web objects)

The UEs and the servers run on the same host (different containers), so their clocks are the
same and one-way delays are measured directly.

Usage:  python traffic/server.py --bind 10.60.0.1
"""
from __future__ import annotations

import argparse
import asyncio
import multiprocessing as mp
import struct
import time

HDR = struct.Struct("!Id")          # seq, client send time
SRV = struct.Struct("!d")           # server timestamp appended by the echo
STREAM = struct.Struct("!Iid")      # seq, total expected, server send time
BUF = b"\x00" * (1 << 20)


# ------------------------------------------------------------------ UDP echo
class Echo(asyncio.DatagramProtocol):
    def connection_made(self, tr):
        self.tr = tr

    def datagram_received(self, data, addr):
        self.tr.sendto(data + SRV.pack(time.time()), addr)


# ------------------------------------------------------------------ UDP stream
class Streamer(asyncio.DatagramProtocol):
    def connection_made(self, tr):
        self.tr = tr
        self.active = {}

    def datagram_received(self, data, addr):
        try:
            parts = data.decode().split()
        except UnicodeDecodeError:
            return
        if parts and parts[0] == "START":
            kbps, dur, size = float(parts[1]), float(parts[2]), int(parts[3])
            if addr in self.active:
                self.active[addr].cancel()
            self.active[addr] = asyncio.ensure_future(self.stream(addr, kbps, dur, size))
        elif parts and parts[0] == "STOP" and addr in self.active:
            self.active.pop(addr).cancel()

    async def stream(self, addr, kbps, dur, size):
        pps = kbps * 1000.0 / 8.0 / size
        total = int(pps * dur)
        pad = BUF[: max(size - STREAM.size, 0)]
        t0 = time.perf_counter()
        seq = 0
        tick = 0.002
        while seq < total:
            due = int((time.perf_counter() - t0) * pps) + 1
            while seq < min(due, total):
                self.tr.sendto(STREAM.pack(seq, total, time.time()) + pad, addr)
                seq += 1
            await asyncio.sleep(tick)


# ------------------------------------------------------------------ TCP bytes
async def http_bytes(reader, writer):
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            parts = line.decode(errors="ignore").split()
            while True:                       # skip headers
                h = await reader.readline()
                if h in (b"\r\n", b"\n", b""):
                    break
            n = 0
            if len(parts) >= 2 and parts[1].startswith("/bytes/"):
                n = int(parts[1].split("/")[2])
            writer.write(f"HTTP/1.1 200 OK\r\nContent-Length: {n}\r\nConnection: keep-alive\r\n\r\n".encode())
            while n > 0:
                k = min(n, len(BUF))
                writer.write(BUF[:k])
                n -= k
                await writer.drain()
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()


def run_udp(proto, host, port):
    async def main():
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(proto, local_addr=(host, port))
        await asyncio.Event().wait()
    asyncio.run(main())


def run_tcp(host, port):
    async def main():
        srv = await asyncio.start_server(http_bytes, host, port, backlog=512)
        async with srv:
            await srv.serve_forever()
    asyncio.run(main())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--echo", type=int, default=5000)
    ap.add_argument("--stream", type=int, default=5001)
    ap.add_argument("--tcp", type=int, default=8000)
    a = ap.parse_args()
    procs = [mp.Process(target=run_udp, args=(Echo, a.bind, a.echo), daemon=True),
             mp.Process(target=run_udp, args=(Streamer, a.bind, a.stream), daemon=True),
             mp.Process(target=run_tcp, args=(a.bind, a.tcp), daemon=True)]
    for p in procs:
        p.start()
    print(f"[dn] echo udp/{a.echo}  stream udp/{a.stream}  http tcp/{a.tcp} on {a.bind}", flush=True)
    for p in procs:
        p.join()


if __name__ == "__main__":
    main()
