"""Generic hour-by-hour scenario on the real core: AF agents + real traffic + measured QoE, policy by policy.

Any slot of the 24-h profile can be played (--slots); results are also analysed by congestion
regime L0/R (analysis/make_figures.py, figT6), so nothing is tied to a particular place or event.

For every (policy, rationing, seed) and every slot t of the evening window:
  1. demand scale x_t and session sensitivities are drawn with COMMON random numbers
     (same seed -> same x_t, same sessions, same UEs for every policy: paired comparison);
  2. the NEF posts the policy's price (POST /lab/v1/slot, GET /nef-pricing/v1/prices);
  3. each AF computes its equilibrium upgrade share with the agreed follower model
     (Nash equilibrium of the vendored package, y = nash(model_t(x_t), price per AF)) and
     sends one AsSessionWithQoS request per upgraded session (its most sensitive ones),
     in a shuffled order, through the real NEF -> BSF -> PCF -> SMF -> UPF chain;
  4. every session (upgraded or not) runs real traffic through gNB/UPF for the slot;
  5. QoE is computed from the measurements; subscriptions are deleted at slot end.

Outputs (results/testbed/<tag>/): sessions.jsonl, requests.jsonl, slots.jsonl

Usage (inside the UE container):
  python scenario/run_scenario.py --policies no_nef safe_strict quantile revenue_max \
         --rationing random --seeds 1 2 3 [--slots 19 20 21] [--slot-seconds 90] [--quick]
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import subprocess
import sys
import time

import httpx
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from labcommon import (af_class_index, class_names, load_cfg, model_bundle, profile_table,  # noqa: E402
                       rpath, scaling)
from nef_pricing.followers import nash  # noqa: E402
from traffic import client, qoe  # noqa: E402

API = "/3gpp-as-session-with-qos/v1"


def ue_addresses():
    out = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True).stdout
    ips = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) > 3 and parts[1].startswith("uesimtun"):
            ips.append((int(parts[1].replace("uesimtun", "")), parts[3].split("/")[0]))
    return [ip for _, ip in sorted(ips)]


def healthy_ues():
    """Attached UEs minus those flagged by scenario/check_ues.py (results/broken_ues.json)."""
    ues = ue_addresses()
    path = rpath("results", "broken_ues.json")
    if os.path.exists(path):
        bad = set(json.load(open(path)).get("broken", []))
        if bad:
            print(f"  skipping {len(bad & set(ues))} unhealthy UEs (scenario/check_ues.py)")
        ues = [u for u in ues if u not in bad]
    return ues


def draw_slot(seed, t, models, cfg, P, mult=1.0):
    """Common random numbers: demand scale, sessions per AF, sensitivities, AF rounding draws.
    mult scales the realised demand (e.g. 1.3 = unforecast surge): any congestion regime can be played."""
    rng = np.random.default_rng([int(seed), int(t), 11])
    cv = float(cfg["scenario"]["forecast_cv"])
    x = float(max(1.0 + cv * rng.standard_normal(), 0.05)) * float(mult)
    k = float(cfg["scaling"]["sample_k"])
    m = models[t]
    n = []
    for i in range(m.N):
        lam = m.X[i] * x / k
        f = int(np.floor(lam))
        n.append(f + int(rng.random() < lam - f))
    sess = []
    for i in range(m.N):
        for j in range(n[i]):
            sess.append({"af": i, "u": float(rng.random())})
    u_round = rng.random(m.N)            # rounding of the AF upgrade counts
    order_seed = int(rng.integers(1 << 30))
    return x, sess, u_round, order_seed


def af_decisions(m_x, prices_by_class, cidx, names):
    """Equilibrium upgrade share of every AF at the posted prices (inf = closed)."""
    pv = np.array([np.inf if prices_by_class[names[j]] is None else prices_by_class[names[j]] for j in cidx])
    if np.all(np.isinf(pv)):
        return np.zeros(m_x.N), pv
    y = nash(m_x, pv)
    return np.clip(y / np.maximum(m_x.X, 1e-12), 0, 1), pv


def run_slot(c, cfg, prof, ref_of, sc, models, P, ues, policy, rationing, seed, t, args, out):
    names = class_names(P)
    cidx = af_class_index(P)
    tcfg = cfg["traffic"]
    dn = cfg["core"]["dn_ip"]
    x, sess, u_round, order_seed = draw_slot(seed, t, models, cfg, P, args.demand_mult)
    if len(sess) > len(ues):
        print(f"  ! {len(sess)} sessions > {len(ues)} UEs: truncated (increase core.ue_count)")
        sess = sess[: len(ues)]
    for s_, ip in zip(sess, ues):
        s_["ue"] = ip
    st = c.post("/lab/v1/slot", json={"slot": t, "x_forecast": 1.0}).json()
    pub = c.get("/nef-pricing/v1/prices").json()
    pr_cls = {prof[r]["class"]: pub["prices"].get(r) for r in prof}
    m = models[t]
    m_x = m.with_X(m.X * x)
    frac, pv = af_decisions(m_x, pr_cls, cidx, names)
    D_R = m.degr.D(m.R)
    # each AF upgrades its most sensitive sessions
    wanted = []
    for i in range(m.N):
        mine = sorted([s_ for s_ in sess if s_["af"] == i], key=lambda z: -z["u"])
        want = frac[i] * len(mine)
        nw = int(np.floor(want)) + int(u_round[i] < want - np.floor(want))
        for s_ in mine[:nw]:
            s_["requested"] = True
            s_["priority"] = float(m.sens[i].Q(s_["u"]) * D_R[i] / m.Delta[i])
            wanted.append(s_)
    rng = np.random.default_rng(order_seed)
    order = rng.permutation(len(wanted))
    granted = {}
    req_rows = []
    for k_ in order:
        s_ = wanted[k_]
        cls = names[cidx[s_["af"]]]
        ref = ref_of[cls]
        body = {"notificationDestination": f"http://af{s_['af']}.example/notify",
                "ueIpv4Addr": s_["ue"], "qosReference": ref, "supportedFeatures": "0",
                "flowInfo": [{"flowId": 1, "flowDescriptions": [f"permit out ip from {dn} to {s_['ue']}",
                                                                f"permit in ip from {s_['ue']} to {dn}"]}],
                "x-pricing": {"priority": s_["priority"]}}
        t0 = time.perf_counter()
        if rationing == "random":
            r = c.post(f"{API}/af{s_['af']}/subscriptions", json=body)
            req_rows.append(_req_row(r, t0, s_, cls, ref, policy, rationing, seed, t))
            if r.status_code == 201:
                granted[id(s_)] = r.headers["location"]
            time.sleep(cfg["scenario"]["request_spacing_ms"] / 1000.0)
        else:
            s_["_body"], s_["_t0"] = body, t0
    if rationing != "random" and wanted:   # batch admission: send concurrently inside the window
        import asyncio

        async def burst():
            async with httpx.AsyncClient(base_url=str(c.base_url), timeout=30) as ac:
                async def one(s_):
                    t0 = time.perf_counter()
                    r = await ac.post(f"{API}/af{s_['af']}/subscriptions", json=s_["_body"])
                    return s_, r, t0
                return await asyncio.gather(*[one(wanted[k_]) for k_ in order])
        for s_, r, t0 in asyncio.run(burst()):
            cls = names[cidx[s_["af"]]]
            req_rows.append(_req_row(r, t0, s_, cls, ref_of[cls], policy, rationing, seed, t))
            if r.status_code == 201:
                granted[id(s_)] = r.headers["location"]
    # ---------------------------------------------------------------- traffic
    specs = {cl: [] for cl in names}
    for n_, s_ in enumerate(sess):
        cls = names[cidx[s_["af"]]]
        ref = ref_of[cls]
        g = id(s_) in granted
        specs[cls].append({"sid": f"{seed}-{t}-{n_}", "class": cls, "af": s_["af"], "ue": s_["ue"], "u": s_["u"],
                           "requested": bool(s_.get("requested")), "granted": g,
                           "rate_mbps": prof[ref]["b_tb"] if g else prof[ref]["a_tb"], "a_tb": prof[ref]["a_tb"]})
    fl = c.post("/lab/v1/enforcer/flush").json()          # every granted class is active before traffic
    dur = float(args.slot_seconds)
    warm = float(tcfg["warmup_s"])
    q = mp.Queue()
    procs = [mp.Process(target=client.run_sessions, args=(v, dn, tcfg, dur, warm, sc.s, q)) for v in specs.values() if v]
    t_slot = time.time()
    for p_ in procs:
        p_.start()
    results = []
    for _ in procs:
        results += q.get()
    for p_ in procs:
        p_.join()
    tc = c.get("/lab/v1/tc").json().get("stats", {})
    # ---------------------------------------------------------------- end of slot
    for loc in granted.values():
        c.delete(loc)
    for rec in results:
        rec["qoe"] = qoe.session_qoe(rec, tcfg)
        row = {k: v for k, v in rec.items() if k != "raw"}
        row.update({"policy": policy, "rationing": rationing, "seed": seed, "slot": t, "x": x})
        out["sessions"].write(json.dumps(row) + "\n")
    for r in req_rows:
        out["requests"].write(json.dumps(r) + "\n")
    mos = [r["qoe"]["mos"] for r in results if r["qoe"].get("mos") is not None]
    out["slots"].write(json.dumps({"policy": policy, "rationing": rationing, "seed": seed, "slot": t, "x": x,
                                   "n_sessions": len(sess), "n_requested": len(wanted), "n_granted": len(granted),
                                   "prices": pub["prices"], "nef_state": st, "tc": tc, "t_start": t_slot,
                                   "mos_mean": float(np.mean(mos)) if mos else None,
                                   "enforcer_flush_ms": fl.get("flushed_in_ms"),
                                   "af_frac": frac.tolist()}) + "\n")
    print(f"  slot {t}h x={x:.2f} sessions={len(sess)} requested={len(wanted)} granted={len(granted)} "
          f"MOS={np.mean(mos) if mos else float('nan'):.3f}", flush=True)
    time.sleep(cfg["scenario"]["cooldown_s"])


def _req_row(r, t0, s_, cls, ref, policy, rationing, seed, t):
    timing = dict(kv.split("=") for kv in r.headers.get("x-nef-timing", "").split(";") if "=" in kv)
    body = {}
    try:
        body = r.json()
    except Exception:
        pass
    return {"policy": policy, "rationing": rationing, "seed": seed, "slot": t, "class": cls, "ref": ref,
            "ue": s_["ue"], "status": r.status_code, "cause": body.get("cause") if isinstance(body, dict) else None,
            "af_latency_ms": 1e3 * (time.perf_counter() - t0), "nef_timing": {k: float(v) for k, v in timing.items()},
            "priority": s_.get("priority")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--nef", default=None, help="NEF base URL (default http://<core_ip>:<port>)")
    ap.add_argument("--policies", nargs="+", default=None)
    ap.add_argument("--rationing", default="random", choices=["random", "priority", "qoe_gain"])
    ap.add_argument("--seeds", nargs="+", type=int, default=None)
    ap.add_argument("--slots", nargs="+", type=int, default=None)
    ap.add_argument("--slot-seconds", type=float, default=None)
    ap.add_argument("--quick", action="store_true", help="3 peak slots, 40 s per slot, 1 seed")
    ap.add_argument("--tag", default=None)
    ap.add_argument("--demand-mult", type=float, default=1.0,
                    help="multiply the realised demand (the NEF still prices on its forecast x=1): 1.3 = surge")
    ap.add_argument("--fixed-order", action="store_true",
                    help="run the policies in the given order (default: shuffled per seed). A policy may be "
                         "repeated with a suffix, e.g. no_nef#2, to measure the run-to-run noise (A/A test)")
    args = ap.parse_args()
    cfg = load_cfg(args.config)
    P, cal, models = model_bundle()
    prof = profile_table(cfg)
    ref_of = {v["class"]: r for r, v in prof.items()}
    sc = scaling(cfg)
    if args.quick:
        args.slots = args.slots or [19, 20, 21]
        args.slot_seconds = args.slot_seconds or 40
        args.seeds = args.seeds or [1]
    args.slots = args.slots or cfg["pricing"]["window_slots"]
    args.slot_seconds = args.slot_seconds or cfg["scenario"]["slot_seconds"]
    args.seeds = args.seeds or cfg["scenario"]["seeds"]
    policies = args.policies or cfg["pricing"]["policies"]
    base = args.nef or f"http://{cfg['core']['core_ip']}:{cfg['nef']['listen_port']}"
    ues = healthy_ues()
    print(f"{len(ues)} healthy UEs; NEF {base}; R_tb={sc.R_tb:.2f} Mbit/s; slots {args.slots}")
    if not ues:
        sys.exit("no uesimtun interface: are the UEs registered? (see GUIDE.md step 3)")
    tag = args.tag or time.strftime("run_%Y%m%d_%H%M%S")
    odir = rpath("results", "testbed", tag)
    os.makedirs(odir, exist_ok=True)
    with open(os.path.join(odir, "meta.json"), "w") as f:
        json.dump({"policies": policies, "rationing": args.rationing, "seeds": args.seeds, "slots": args.slots,
                   "slot_seconds": args.slot_seconds, "R_tb": sc.R_tb, "k": sc.k, "s": sc.s, "n_ues": len(ues),
                   "config": cfg}, f, indent=2, default=str)
    out = {k: open(os.path.join(odir, f"{k}.jsonl"), "a", buffering=1) for k in ("sessions", "requests", "slots")}
    c = httpx.Client(base_url=base, timeout=30)
    for seed in args.seeds:
        order = list(policies)
        if not args.fixed_order:          # random order per seed: removes any drift / order effect
            np.random.default_rng(1000 + seed).shuffle(order)
        for policy in order:
            print(f"== policy={policy} rationing={args.rationing} seed={seed}", flush=True)
            r = c.post("/lab/v1/config", json={"policy": policy.split("#")[0], "rationing": args.rationing,
                                               "run": f"{tag}:{policy}:{args.rationing}:{seed}"})
            if r.status_code != 200:
                sys.exit(f"NEF refused policy {policy}: {r.text}")
            for t in args.slots:
                run_slot(c, cfg, prof, ref_of, sc, models, P, ues, policy, args.rationing, seed, t, args, out)
    c.post("/lab/v1/reset")
    print(f"done -> {odir}")


if __name__ == "__main__":
    main()
