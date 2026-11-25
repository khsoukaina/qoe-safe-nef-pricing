"""Health check of every UE data path (UE -> gNB -> UPF -> DN and back), before any run.

For each attached UE (uesimtunX): 20 UDP echo packets (loss, RTT) and one TCP connection with a
200 kB download from the DN server. A UE is "healthy" if UDP loss <= 10 %, TCP works and RTT is
sane. The list of broken UEs is written to results/broken_ues.json; run_scenario.py and
measure_externality.py then skip them (only healthy UEs carry sessions).

Usage (UE container):  python scenario/check_ues.py [--workers 16]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import socket
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from labcommon import load_cfg, rpath  # noqa: E402
from scenario.run_scenario import ue_addresses  # noqa: E402

HDR = struct.Struct("!Id")


def check(ue, dn, cfg):
    res = {"ue": ue}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((ue, 0))
        s.settimeout(0.5)
        rtts = []
        for k in range(20):
            t0 = time.time()
            s.sendto(HDR.pack(k, t0) + b"\0" * 100, (dn, cfg["traffic"]["udp_echo_port"]))
            try:
                d, _ = s.recvfrom(2048)
                if HDR.unpack_from(d)[0] == k:
                    rtts.append(1e3 * (time.time() - t0))
            except socket.timeout:
                pass
            time.sleep(0.02)
        s.close()
        res["udp_loss"] = 1 - len(rtts) / 20
        res["rtt_ms"] = round(sorted(rtts)[len(rtts) // 2], 1) if rtts else None
    except OSError as e:
        res["udp_error"] = str(e)
        res["udp_loss"] = 1.0
    try:
        t0 = time.time()
        c = socket.create_connection((dn, cfg["traffic"]["tcp_port"]), timeout=3, source_address=(ue, 0))
        c.sendall(b"GET /bytes/200000 HTTP/1.1\r\nHost: dn\r\n\r\n")
        c.settimeout(5)
        got = 0
        while got < 200000:
            b = c.recv(65536)
            if not b:
                break
            got += len(b)
        c.close()
        res["tcp_ok"] = got >= 200000
        res["tcp_s"] = round(time.time() - t0, 3)
    except OSError as e:
        res["tcp_ok"] = False
        res["tcp_error"] = str(e)[:80]
    res["healthy"] = bool(res["udp_loss"] <= 0.10 and res["tcp_ok"] and res.get("rtt_ms") is not None)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    cfg = load_cfg()
    ues = ue_addresses()
    if not ues:
        sys.exit("no uesimtun interface")
    with cf.ThreadPoolExecutor(a.workers) as ex:
        res = list(ex.map(lambda u: check(u, cfg["core"]["dn_ip"], cfg), ues))
    bad = [r for r in res if not r["healthy"]]
    for i, r in enumerate(res):
        flag = "OK " if r["healthy"] else "BAD"
        print(f"{flag} uesimtun{i:<2} {r['ue']:<12} udp_loss={r['udp_loss']:.2f} rtt={r.get('rtt_ms')} ms "
              f"tcp={'ok' if r['tcp_ok'] else 'FAIL'} {r.get('tcp_s', '')} {r.get('tcp_error', '')}{r.get('udp_error', '')}")
    print(f"\n{len(res) - len(bad)} / {len(res)} UEs healthy")
    with open(rpath("results", "broken_ues.json"), "w") as f:
        json.dump({"t": time.time(), "broken": [r["ue"] for r in bad], "details": res}, f, indent=1)


if __name__ == "__main__":
    main()
