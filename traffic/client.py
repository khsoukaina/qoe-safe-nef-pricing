"""Traffic generators run inside the UE container, one socket per session bound to the UE's
PDU-session address (uesimtunX), so that every packet really crosses gNB -> N3 -> UPF -> N6.

Session types (raw measurements; MOS is computed in traffic/qoe.py):
  voice  : G.711-like 50 pps UDP to the echo server; one-way delays, jitter, loss
  gaming : paced UDP downlink stream (cloud-gaming video) + 30 pps RTT probes
  video  : DASH-like adaptive streaming over HTTP/TCP (segments, ABR, buffer, stalls)
  web    : page = objects fetched over `parallel` TCP connections; page load time (PLT)

run_sessions(list_of_specs, duration, cfg) is executed in one worker process per class.
"""
from __future__ import annotations

import asyncio
import random
import socket
import struct
import time

HDR = struct.Struct("!Id")
SRV = struct.Struct("!d")
STREAM = struct.Struct("!Iid")


def _udp_sock(ue_ip):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 21)
    s.bind((ue_ip, 0))
    s.setblocking(False)
    return s


class _Collector(asyncio.DatagramProtocol):
    def __init__(self, on_packet):
        self.on_packet = on_packet

    def datagram_received(self, data, addr):
        self.on_packet(data, time.time())


# ------------------------------------------------------------------ voice
async def voice(spec, dn, cfg, dur, warm):
    v = cfg["voice"]
    loop = asyncio.get_running_loop()
    sent, got = {}, {}

    def on_pkt(data, t):
        if len(data) >= HDR.size + SRV.size:
            seq, ts = HDR.unpack_from(data)
            (tsrv,) = SRV.unpack_from(data, len(data) - SRV.size)
            got[seq] = (ts, tsrv, t)

    tr, _ = await loop.create_datagram_endpoint(lambda: _Collector(on_pkt), sock=_udp_sock(spec["ue"]))
    pad = b"\x00" * max(v["payload"] - HDR.size, 0)
    period = 1.0 / v["pps"]
    t0 = time.perf_counter()
    seq = 0
    await asyncio.sleep(random.random() * period)
    while time.perf_counter() - t0 < dur:
        ts = time.time()
        sent[seq] = ts
        tr.sendto(HDR.pack(seq, ts) + pad, (dn, cfg["udp_echo_port"]))
        seq += 1
        nxt = t0 + seq * period
        await asyncio.sleep(max(0.0, nxt - time.perf_counter()))
    await asyncio.sleep(1.0)
    tr.close()
    t_start = min(sent.values()) + warm if sent else 0
    keep = [s for s, ts in sent.items() if ts >= t_start]
    ul = [got[s][1] - got[s][0] for s in keep if s in got]
    dl = [got[s][2] - got[s][1] for s in keep if s in got]
    return {"n_sent": len(keep), "n_recv": sum(s in got for s in keep),
            "owd_ul_ms": [1e3 * x for x in ul], "owd_dl_ms": [1e3 * x for x in dl]}


# ------------------------------------------------------------------ gaming
async def gaming(spec, dn, cfg, dur, warm):
    g = cfg["gaming"]
    loop = asyncio.get_running_loop()
    rtts, stream = [], {"n": 0, "max_seq": -1, "total": 0, "t_first": None, "owd": []}
    t_meas = time.time() + warm

    def on_probe(data, t):
        if len(data) >= HDR.size + SRV.size and t >= t_meas:
            seq, ts = HDR.unpack_from(data)
            rtts.append(1e3 * (t - ts))

    def on_stream(data, t):
        if len(data) >= STREAM.size:
            seq, total, tsrv = STREAM.unpack_from(data)
            stream["total"] = total
            if t >= t_meas:
                if stream["t_first"] is None:
                    stream["t_first"] = seq
                stream["n"] += 1
                stream["max_seq"] = max(stream["max_seq"], seq)
                if stream["n"] % 10 == 0:
                    stream["owd"].append(1e3 * (t - tsrv))

    tp, _ = await loop.create_datagram_endpoint(lambda: _Collector(on_probe), sock=_udp_sock(spec["ue"]))
    ts_, _ = await loop.create_datagram_endpoint(lambda: _Collector(on_stream), sock=_udp_sock(spec["ue"]))
    kbps = spec["rate_mbps"] * 1000.0
    ts_.sendto(f"START {kbps:.1f} {dur:.1f} {g['pkt_bytes']} {spec['sid']}".encode(), (dn, cfg["udp_stream_port"]))
    period = 1.0 / g["probe_pps"]
    t0 = time.perf_counter()
    seq = 0
    while time.perf_counter() - t0 < dur:
        tp.sendto(HDR.pack(seq, time.time()) + b"\x00" * 40, (dn, cfg["udp_echo_port"]))
        seq += 1
        await asyncio.sleep(max(0.0, t0 + seq * period - time.perf_counter()))
    await asyncio.sleep(1.0)
    ts_.sendto(b"STOP", (dn, cfg["udp_stream_port"]))
    tp.close()
    ts_.close()
    expected = (stream["max_seq"] - stream["t_first"] + 1) if stream["t_first"] is not None else 0
    loss = 1.0 - stream["n"] / expected if expected > 0 else 1.0
    n_probe_exp = int((dur - warm) * g["probe_pps"])
    return {"rtt_ms": rtts, "stream_loss": max(loss, 0.0), "stream_rx": stream["n"],
            "probe_loss": max(0.0, 1 - len(rtts) / max(n_probe_exp, 1)), "stream_owd_ms": stream["owd"],
            "rate_mbps": spec["rate_mbps"]}


# ------------------------------------------------------------------ HTTP helper
async def _open(ue, dn, port):
    return await asyncio.open_connection(dn, port, local_addr=(ue, 0))


async def _get(reader, writer, n):
    writer.write(f"GET /bytes/{int(n)} HTTP/1.1\r\nHost: dn\r\n\r\n".encode())
    await writer.drain()
    length = 0
    while True:
        line = await reader.readline()
        if not line:
            raise ConnectionError("closed")
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":")[1])
        if line in (b"\r\n", b"\n"):
            break
    await reader.readexactly(length)


# ------------------------------------------------------------------ video
async def video(spec, dn, cfg, dur, warm, scale):
    v = cfg["video"]
    ladder = [k * scale for k in v["ladder_kbps_real"]]     # testbed kbit/s
    seg = v["segment_s"]
    r, w = await _open(spec["ue"], dn, cfg["tcp_port"])
    t_start = time.perf_counter()
    buf, playing, stall, startup = 0.0, False, 0.0, None
    thr = []
    segs = []
    last = t_start
    i_rung = 0
    try:
        while time.perf_counter() - t_start < dur:
            if thr:
                est = len(thr[-3:]) / sum(1.0 / x for x in thr[-3:])       # harmonic mean (kbit/s)
                i_rung = max([k for k, br in enumerate(ladder) if br <= v["safety"] * est] or [0])
            nbytes = ladder[i_rung] * 1000.0 / 8.0 * seg
            t0 = time.perf_counter()
            await asyncio.wait_for(_get(r, w, nbytes), timeout=max(dur, 30))
            t1 = time.perf_counter()
            dt = t1 - t0
            thr.append(nbytes * 8.0 / 1000.0 / max(dt, 1e-3))
            # buffer dynamics
            elapsed = t1 - last
            last = t1
            if playing:
                buf -= elapsed
                if buf < 0:
                    if t1 - t_start > warm:
                        stall += -buf
                    buf = 0.0
            buf += seg
            if not playing:
                playing = True
                startup = t1 - t_start
            if t1 - t_start > warm:
                segs.append({"rung": i_rung, "kbps_real": v["ladder_kbps_real"][i_rung], "dl_s": dt})
            if buf > 12.0:                          # player buffer cap: wait
                await asyncio.sleep(buf - 12.0)
                buf = 12.0
                last = time.perf_counter()
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pass
    finally:
        w.close()
    measured = max(dur - warm, 1e-3)
    return {"segments": segs, "stall_s": stall, "startup_s": startup, "measured_s": measured,
            "throughput_kbps_tb": thr[-20:]}


# ------------------------------------------------------------------ web
async def web(spec, dn, cfg, dur, warm, scale, a_tb):
    wcfg = cfg["web"]
    page = wcfg["page_kb_real"] * 1000.0 * scale
    n_obj = wcfg["objects"]
    obj = page * 0.9 / n_obj
    t_start = time.perf_counter()
    plts = []
    conns = []
    try:
        conns = [await _open(spec["ue"], dn, cfg["tcp_port"]) for _ in range(wcfg["parallel"])]
        await asyncio.sleep(random.random() * 2.0)
        while time.perf_counter() - t_start < dur:
            t0 = time.perf_counter()
            await asyncio.wait_for(_get(*conns[0], page * 0.1), timeout=30)       # HTML
            queue = list(range(n_obj))

            async def worker(c):
                while queue:
                    queue.pop()
                    await _get(*c, obj)
            await asyncio.wait_for(asyncio.gather(*[worker(c) for c in conns]), timeout=60)
            plt = time.perf_counter() - t0
            if t0 - t_start > warm:
                plts.append(plt)
            think = max(0.5, page * 8.0 / 1e6 / max(a_tb, 1e-3) - plt)          # mean rate ~ a_tb
            await asyncio.sleep(think * random.uniform(0.7, 1.3))
    except (asyncio.TimeoutError, ConnectionError, OSError):
        plts.append(60.0)
    finally:
        for _, w in conns:
            w.close()
    return {"plt_s": plts}


# ------------------------------------------------------------------ worker
async def _run_all(specs, dn, cfg, dur, warm, scale):
    tasks = []
    for s in specs:
        if s["class"] == "voice":
            tasks.append(voice(s, dn, cfg, dur, warm))
        elif s["class"] == "gaming":
            tasks.append(gaming(s, dn, cfg, dur, warm))
        elif s["class"] == "video":
            tasks.append(video(s, dn, cfg, dur, warm, scale))
        elif s["class"] == "web":
            tasks.append(web(s, dn, cfg, dur, warm, scale, s["a_tb"]))
    res = await asyncio.gather(*tasks, return_exceptions=True)
    out = []
    for s, r in zip(specs, res):
        out.append({**s, "raw": r if not isinstance(r, Exception) else {"error": repr(r)}})
    return out


def run_sessions(specs, dn, cfg, dur, warm, scale, queue=None):
    out = asyncio.run(_run_all(specs, dn, cfg, dur, warm, scale))
    if queue is not None:
        queue.put(out)
    return out
