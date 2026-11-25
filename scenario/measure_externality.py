"""Testbed calibration: measure the best-effort externality D_i(L) on the real user plane.

For each load level (multiple of the peak-hour session mix) and each guaranteed share g,
a share g of the sessions of every class gets a real AsSessionWithQoS grant (price opened,
budget unlimited), all sessions run real traffic, and
    D_i(L) = mean MOS of the guaranteed sessions of class i - mean MOS of its best-effort ones
at the measured load L. Output: results/testbed/externality/points.jsonl, then
    python scenario/fit_externality.py     -> results/calibration_testbed.json
which can replace the analytical calibration (NEF_LAB_CALIBRATION=results/calibration_testbed.json).

Usage (UE container):  python scenario/measure_externality.py [--levels 0.5 0.7 0.85 1.0 1.15] [--shares 0.25 0.5]
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time

import httpx
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from labcommon import load_cfg, model_bundle, peak_slot, profile_table, rpath, scaling  # noqa: E402
from scenario.run_scenario import API, healthy_ues  # noqa: E402
from traffic import client, qoe  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--nef", default=None)
    ap.add_argument("--levels", nargs="+", type=float, default=[0.5, 0.7, 0.85, 1.0, 1.15])
    ap.add_argument("--shares", nargs="+", type=float, default=[0.25, 0.5])
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--repeats", type=int, default=2)
    args = ap.parse_args()
    cfg = load_cfg(args.config)
    P, cal, models = model_bundle()
    prof = profile_table(cfg)
    sc = scaling(cfg)
    tp = peak_slot()
    names = [c["name"] for c in P["classes"]]
    n_af = len(P.get("afs_per_class", [1.0]))
    X_cls = np.array([models[tp].X[j * n_af:(j + 1) * n_af].sum() for j in range(len(names))])
    ues = healthy_ues()
    base = args.nef or f"http://{cfg['core']['core_ip']}:{cfg['nef']['listen_port']}"
    c = httpx.Client(base_url=base, timeout=30)
    odir = rpath("results", "testbed", "externality")
    os.makedirs(odir, exist_ok=True)
    fout = open(os.path.join(odir, "points.jsonl"), "a", buffering=1)
    ref_of = {v["class"]: r for r, v in prof.items()}
    dn = cfg["core"]["dn_ip"]
    tcfg = cfg["traffic"]
    for rep in range(args.repeats):
        for lev in args.levels:
            for g in args.shares:
                n_cls = np.maximum(np.round(lev * X_cls / sc.k).astype(int), 2)
                if n_cls.sum() > len(ues):
                    print(f"skip level {lev}: {n_cls.sum()} sessions > {len(ues)} UEs")
                    continue
                c.post("/lab/v1/config", json={"policy": "quantile", "rationing": "random", "run": "externality"})
                c.post("/lab/v1/slot", json={"slot": tp, "open_price": 0.0, "budget_mbps": 1e6})
                specs, k = {cl: [] for cl in names}, 0
                locs = []
                L = 0.0
                for j, cl in enumerate(names):
                    ref = ref_of[cl]
                    n_g = int(round(g * n_cls[j]))
                    for q_ in range(n_cls[j]):
                        ue = ues[k]
                        k += 1
                        gr = q_ < n_g
                        if gr:
                            body = {"notificationDestination": "http://cal.example/notify", "ueIpv4Addr": ue,
                                    "qosReference": ref,
                                    "flowInfo": [{"flowId": 1, "flowDescriptions": [f"permit out ip from {dn} to {ue}",
                                                                                    f"permit in ip from {ue} to {dn}"]}]}
                            r = c.post(f"{API}/cal/subscriptions", json=body)
                            gr = r.status_code == 201
                            if gr:
                                locs.append(r.headers["location"])
                        L += prof[ref]["b_tb"] if gr else prof[ref]["a_tb"]
                        specs[cl].append({"sid": f"cal-{lev}-{g}-{k}", "class": cl, "ue": ue, "granted": gr,
                                          "rate_mbps": prof[ref]["b_tb"] if gr else prof[ref]["a_tb"],
                                          "a_tb": prof[ref]["a_tb"], "af": j, "u": 0.5})
                c.post("/lab/v1/enforcer/flush")
                qq = mp.Queue()
                procs = [mp.Process(target=client.run_sessions, args=(v, dn, tcfg, args.seconds, tcfg["warmup_s"], sc.s, qq))
                         for v in specs.values() if v]
                for p_ in procs:
                    p_.start()
                res = []
                for _ in procs:
                    res += qq.get()
                for p_ in procs:
                    p_.join()
                for loc in locs:
                    c.delete(loc)
                for cl in names:
                    rows = [r for r in res if r["class"] == cl]
                    for r in rows:
                        r["qoe"] = qoe.session_qoe(r, tcfg)
                    m_g = [r["qoe"]["mos"] for r in rows if r["granted"] and r["qoe"].get("mos") is not None]
                    m_b = [r["qoe"]["mos"] for r in rows if not r["granted"] and r["qoe"].get("mos") is not None]
                    rec = {"rep": rep, "level": lev, "share": g, "class": cl, "L_tb": L, "L_over_R": L / sc.R_tb,
                           "L_model": L / sc.R_tb * sc.R_model, "mos_gbr": float(np.mean(m_g)) if m_g else None,
                           "mos_be": float(np.mean(m_b)) if m_b else None, "n_gbr": len(m_g), "n_be": len(m_b)}
                    rec["D"] = (rec["mos_gbr"] - rec["mos_be"]) if (m_g and m_b) else None
                    fout.write(json.dumps(rec) + "\n")
                    print(f"  lev={lev} g={g} L/R={L / sc.R_tb:.2f} {cl}: GBR {rec['mos_gbr']} BE {rec['mos_be']} D={rec['D']}")
                time.sleep(cfg["scenario"]["cooldown_s"])
    c.post("/lab/v1/reset")


if __name__ == "__main__":
    main()
