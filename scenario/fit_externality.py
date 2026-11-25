"""Fit the externality model on the testbed measurements (same procedure as the simulation's
calibration: D_i(L) ~ delta_i + kappa_i L / R on L in [fit_L_min R, R], floor = low-load gap).

Writes results/calibration_testbed.json with the structure of vendor calibration.json, so the
whole pricing pipeline can be re-run on a model calibrated on the REAL user plane:
    NEF_LAB_CALIBRATION=results/calibration_testbed.json python pricing/precompute_prices.py
Classes with too few points keep their analytical calibration (reported).

Usage:  python scenario/fit_externality.py
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from labcommon import model_bundle, rpath  # noqa: E402


def main():
    P, cal, _ = model_bundle()
    pts = [json.loads(line) for line in open(rpath("results", "testbed", "externality", "points.jsonl"))]
    R = float(P["capacity_R"])
    lo = float(P.get("fit_L_min", 0.5))
    new = json.loads(json.dumps(cal))
    report = {}
    for j, c in enumerate(P["classes"]):
        rows = [p for p in pts if p["class"] == c["name"] and p["D"] is not None]
        L = np.array([p["L_model"] for p in rows])
        neg = [p["D"] for p in rows if p["D"] < -0.1]
        if len(neg) > len(rows) / 3:
            report[c["name"]] = (f"{len(neg)}/{len(rows)} points with guaranteed MOS < best-effort MOS: enforcement "
                                 f"artefact, analytical calibration kept (re-measure)")
            continue
        D = np.array([max(p["D"], 0.0) for p in rows])
        q2 = np.array([p["mos_gbr"] for p in rows])
        m = (L >= lo * R) & (L <= 1.05 * R)
        if m.sum() < 3:
            report[c["name"]] = f"only {int(m.sum())} usable points: analytical calibration kept"
            continue
        A = np.c_[np.ones(m.sum()), L[m] / R]
        coef, *_ = np.linalg.lstsq(A, D[m], rcond=None)
        delta, kappa = float(coef[0]), float(max(coef[1], 0.0))
        pred = np.maximum(delta + kappa * L[m] / R, 0)
        r2 = 1 - np.sum((D[m] - pred) ** 2) / max(np.sum((D[m] - D[m].mean()) ** 2), 1e-12)
        low = D[L < lo * R]
        cc = new["classes"][j]
        cc.update({"delta": delta, "kappa": kappa, "r2": float(r2), "q2": float(np.median(q2)),
                   "floor": float(max(np.mean(low) if len(low) else cc["floor"], 0.02)),
                   "D_at_R": float(max(delta + kappa, cc["floor"])), "source": "testbed"})
        o = np.argsort(L)
        cc["curve_L"], cc["curve_D"] = L[o].tolist(), D[o].tolist()
        report[c["name"]] = {"delta": delta, "kappa": kappa, "r2": float(r2), "n": int(m.sum()),
                             "analytical": {"delta": cal["classes"][j]["delta"], "kappa": cal["classes"][j]["kappa"]}}
    new["source"] = "testbed"
    with open(rpath("results", "calibration_testbed.json"), "w") as f:
        json.dump(new, f, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
