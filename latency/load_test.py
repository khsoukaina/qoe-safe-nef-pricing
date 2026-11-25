"""Decision latency of the NEF under load (open-loop Poisson arrivals).

Each request is a real AsSessionWithQoS creation for an attached UE, held for --hold seconds
and then deleted. The NEF reports its internal breakdown in X-Nef-Timing:
    price_ms      posted-price lookup (the per-request pricing decision)
    admission_ms  capacity check (+ batch window for priority rationing)
    bsf_ms        Nbsf_Management discovery of the UE's PCF (real Open5GS BSF)
    pcf_ms        Npcf_PolicyAuthorization_Create (real Open5GS PCF; triggers SMF/UPF)
    enf_ms        installation of the guaranteed class on the user plane
    total_ms      NEF processing time; the AF also measures end-to-end latency
Run it twice to separate the core's cost: NEF started with --southbound core, then none.

Usage (UE container):  python latency/load_test.py --rates 5 10 20 50 100 --duration 30 --label core
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import random
import sys
import time

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from labcommon import load_cfg, peak_slot, rpath  # noqa: E402
from scenario.run_scenario import API, ue_addresses  # noqa: E402


async def run_rate(base, ues, dn, rate, duration, hold, ref, out, label):
    """Open-loop Poisson arrivals. Requests are spread round-robin over the attached UEs; each one
    carries its own flow (unique UE port), so a UE can hold several application sessions at once:
    the offered load is never limited by the number of UEs (v1 artefact: 'no_free_ue')."""
    results = []
    counter = {"k": 0}
    async with httpx.AsyncClient(base_url=base, timeout=30,
                                 limits=httpx.Limits(max_connections=400, max_keepalive_connections=400)) as c:
        async def one(ue, port):
            body = {"notificationDestination": "http://lat.example/notify", "ueIpv4Addr": ue, "qosReference": ref,
                    "flowInfo": [{"flowId": 1, "flowDescriptions": [f"permit out 17 from {dn} to {ue} {port}",
                                                                    f"permit in 17 from {ue} {port} to {dn}"]}]}
            t0 = time.perf_counter()
            try:
                r = await c.post(f"{API}/lat/subscriptions", json=body)
            except httpx.HTTPError as e:
                rec = {"label": label, "rate": rate, "status": f"client_error:{type(e).__name__}",
                       "e2e_ms": 1e3 * (time.perf_counter() - t0)}
                results.append(rec)
                out.write(json.dumps(rec) + "\n")
                return
            e2e = 1e3 * (time.perf_counter() - t0)
            timing = dict(kv.split("=") for kv in r.headers.get("x-nef-timing", "").split(";") if "=" in kv)
            rec = {"label": label, "rate": rate, "status": r.status_code, "e2e_ms": e2e,
                   **{k: float(v) for k, v in timing.items()}}
            if r.status_code != 201:
                try:
                    rec["cause"] = r.json().get("cause")
                except Exception:
                    pass
            if r.status_code == 201:
                await asyncio.sleep(hold)
                t1 = time.perf_counter()
                d = await c.delete(r.headers["location"])
                rec["delete_ms"] = 1e3 * (time.perf_counter() - t1)
                rec["delete_status"] = d.status_code
            results.append(rec)
            out.write(json.dumps(rec) + "\n")

        tasks = []
        t_end = time.perf_counter() + duration
        while time.perf_counter() < t_end:
            await asyncio.sleep(random.expovariate(rate))
            k = counter["k"]
            counter["k"] += 1
            tasks.append(asyncio.create_task(one(ues[k % len(ues)], 10000 + k % 10000)))
        await asyncio.gather(*tasks)
    ok = [r for r in results if r.get("status") == 201]
    other = collections.Counter(str(r.get("status")) + (":" + str(r.get("cause")) if r.get("cause") else "")
                                for r in results if r.get("status") != 201)
    if ok:
        e = sorted(r["e2e_ms"] for r in ok)
        print(f"  rate {rate:>5}/s: {len(ok)} ok, other {dict(other)}; e2e p50 {e[len(e) // 2]:.1f} ms "
              f"p95 {e[int(0.95 * (len(e) - 1))]:.1f} ms p99 {e[int(0.99 * (len(e) - 1))]:.1f} ms")
    else:
        print(f"  rate {rate:>5}/s: no success, {dict(other)}")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--nef", default=None)
    ap.add_argument("--rates", nargs="+", type=float, default=[5, 10, 20, 50, 100])
    ap.add_argument("--duration", type=float, default=30)
    ap.add_argument("--hold", type=float, default=1.0)
    ap.add_argument("--ref", default="QOD_VOICE")
    ap.add_argument("--label", default="core", help="core (NEF + real core) or none (NEF alone)")
    args = ap.parse_args()
    cfg = load_cfg(args.config)
    base = args.nef or f"http://{cfg['core']['core_ip']}:{cfg['nef']['listen_port']}"
    ues = ue_addresses()
    if not ues:
        sys.exit("no attached UE")
    random.seed(7)
    c = httpx.Client(base_url=base, timeout=30)
    c.post("/lab/v1/config", json={"policy": "quantile", "rationing": "random", "run": f"latency-{args.label}"})
    c.post("/lab/v1/slot", json={"slot": peak_slot(), "open_price": 0.0, "budget_mbps": 1e9})
    odir = rpath("results", "latency")
    os.makedirs(odir, exist_ok=True)
    with open(os.path.join(odir, f"latency_{args.label}.jsonl"), "a", buffering=1) as out:
        for rate in args.rates:
            asyncio.run(run_rate(base, ues, cfg["core"]["dn_ip"], rate, args.duration, args.hold, args.ref, out,
                                 args.label))
            time.sleep(args.hold + 2)          # let every session of this rate be deleted
    act = c.get("/lab/v1/tc").json().get("activation_ms")
    print(f"  user-plane activation delay (enqueue -> class active): {act}")
    with open(os.path.join(odir, f"activation_{args.label}.json"), "w") as f:
        json.dump(act, f)
    c.post("/lab/v1/reset")


if __name__ == "__main__":
    main()
