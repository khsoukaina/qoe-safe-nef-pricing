"""Pre-compute the posted prices of every policy for the slots of pricing.window_slots (any hours).

All prices come from the vendored simulation package (the mathematics agreed before):
  * Stackelberg leader / Nash followers: demand surfaces y^NE(p, x)          (baselines)
  * conformal quantile of the demand scale -> clearing price p_clr(q_conf)   (conformal)
  * QoE-safe selection (strict = mean AND 5th percentile >= no-NEF, eta = 0) (qoe_safe)
and one NEW, clearly separated variant:
  * safe_gated: the QoE-safe price is posted only to the classes whose marginal-gain test
    g_i (qoe_safe.marginal_gain) is >= 0 at the forecast load; the other classes are closed
    (price = inf) on that slot. With no class closed it is identical to safe_strict.

The NEF reads results/prices/prices.json at run time: the per-request decision is then a
table lookup + admission + southbound signalling (this is what the decision latency
measures). The pricing computation time per slot is measured here and stored as well.

Usage:
    python pricing/precompute_prices.py [--quick]
"""
from __future__ import annotations

import argparse
import copy
import os
import sys
import time
import warnings

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from labcommon import (af_class_index, class_names, fnum, jdump, load_cfg, model_bundle,  # noqa: E402
                       peak_slot, profile_table, rpath)
from nef_pricing import admission, baselines, conformal, metrics, qoe_safe  # noqa: E402
from nef_pricing.demand import Forecast  # noqa: E402
from nef_pricing.followers import nash  # noqa: E402
from nef_pricing.model import LinearDegradation  # noqa: E402

warnings.filterwarnings("ignore")


def no_externality(m):
    """Operator that ignores the best-effort externality (D frozen at its no-NEF value)."""
    m2 = copy.copy(m)
    m2.degr = LinearDegradation(m.degr.D(m.base_load()), np.zeros(m.N), m.R)
    return m2


def price_grid(s, p_min, n_fine):
    lo = s.model.c if p_min is None else max(p_min, s.model.c)
    hi = float(s.prices[-1])
    return np.unique(np.r_[[p for p in s.prices if p >= lo], np.geomspace(max(lo, 1e-9), hi, n_fine)])


def gated_table(s, xs, allowed, rationing, p_min, n_fine):
    """Same indicators as qoe_safe.score_table, for a price posted only to `allowed` AFs."""
    grid = price_grid(s, p_min, n_fine)
    rows = []
    y_prev = {}
    for p in grid:
        pv = np.where(allowed, float(p), np.inf)
        acc = []
        for j, x in enumerate(xs):
            m = s.model.with_X(s.model.X * x)
            y = nash(m, pv, y0=y_prev.get(j))
            y_prev[j] = y
            out = admission.admit(m, y, pv, rationing)
            r = metrics.summarize(m, out)
            acc.append([r["mos_mean"], r["mos_p5"], r["welfare"], r["profit"], r["rejection_rate"]])
        rows.append(np.mean(np.asarray(acc, float), axis=0))
    base = []
    for x in xs:
        m = s.model.with_X(s.model.X * x)
        out = admission.Outcome(np.inf, np.zeros(m.N), np.zeros(m.N), m.base_load())
        r = metrics.summarize(m, out, 0.0)
        base.append([r["mos_mean"], r["mos_p5"]])
    base = np.mean(np.asarray(base, float), axis=0)
    R = np.asarray(rows)
    return {"price": grid, "mos": R[:, 0], "p5": R[:, 1], "welfare": R[:, 2], "profit": R[:, 3],
            "reject": R[:, 4], "base_mos": float(base[0]), "base_p5": float(base[1])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="coarser grids (smoke test only)")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()
    cfg = load_cfg(args.config)
    pc = cfg["pricing"]
    P, cal, models = model_bundle()
    prof = profile_table(cfg)
    names = class_names(P)
    cidx = af_class_index(P)
    eps = float(pc["eps"])
    cv = float(cfg["scenario"]["forecast_cv"])
    n_design = 15 if args.quick else int(pc["n_design"])
    n_fine = 15 if args.quick else int(pc["n_fine"])
    window = [int(t) for t in pc["window_slots"]]
    n_p = 14 if args.quick else 26

    t0 = time.perf_counter()
    S = baselines.surfaces(models, tag="core_lab", n_p=n_p)
    print(f"demand surfaces ready ({time.perf_counter() - t0:.1f} s)")

    # conformal quantile of the demand scale from synthetic past days (exchangeable draws)
    hist = Forecast(np.ones(1), cv, seed=777).sample_scale(int(pc["history_days"]))
    q_conf = conformal.conformal_quantile(hist, eps)
    if not np.isfinite(q_conf):
        raise SystemExit(f"history too short for eps={eps}: need >= {conformal.min_history(eps)} days")
    print(f"conformal quantile q = {q_conf:.4f}  (n = {len(hist)}, eps = {eps})")

    # flat commercial price: one price for the whole day maximising expected profit
    grid = np.geomspace(S[0].model.c, np.median([s.prices[-1] for s in S]), 12 if args.quick else 20)
    tot = [sum(baselines.expected_score(s, p, (1.0,), "random", "profit") for s in S) for p in grid]
    flat_rev = float(grid[int(np.argmax(tot))])
    S_noext = baselines.surfaces([no_externality(models[t]) for t in window], tag="core_lab_noext", n_p=n_p)

    zd = np.random.default_rng(21).standard_normal(n_design)
    out = {"meta": {"eps": eps, "q_conformal": q_conf, "history_n": len(hist), "forecast_cv": cv,
                    "window_slots": window, "peak_slot": peak_slot(), "R_model": float(P["capacity_R"]),
                    "flat_rev": flat_rev, "classes": names,
                    "profiles": {r: v["class"] for r, v in prof.items()},
                    "policies": pc["policies"], "quick": bool(args.quick)},
           "slots": {}, "prices": {}, "compute_s": {}}
    ref_by_class = {v["class"]: r for r, v in prof.items()}

    def per_profile(pv_af):
        """AF price vector -> price per qosReference (all AFs of a class share a price)."""
        d = {}
        for j, c in enumerate(names):
            vals = pv_af[cidx == j]
            d[ref_by_class[c]] = fnum(vals[0])
        return d

    for t in window:
        m = models[t]
        g = qoe_safe.marginal_gain(m)
        g_cls = {c: float(np.mean(g[cidx == j])) for j, c in enumerate(names)}
        dl = {c: float(np.mean(m.Delta[cidx == j])) for j, c in enumerate(names)}
        out["slots"][str(t)] = {"base_load_model": m.base_load(), "X_model": m.X.tolist(),
                                "g_class": g_cls, "g_per_delta": {c: g_cls[c] / dl[c] for c in names},
                                "load_ratio_x1": m.base_load() / m.R}

    for rationing in ("random", "priority"):
        out["prices"][rationing] = {k: {} for k in pc["policies"]}
        for wi, t in enumerate(window):
            s = S[t]
            m = s.model
            xs_d = np.clip(1 + cv * zd, s.scales[0], s.scales[-1])
            p_q = baselines.clearing_from_surface(s, min(q_conf, s.scales[-1]))
            t1 = time.perf_counter()
            tab = qoe_safe.score_table(s, xs_d, rationing, p_min=p_q, n_fine=n_fine)
            t_tab = time.perf_counter() - t1
            allowed = qoe_safe.marginal_gain(m) >= 0
            t2 = time.perf_counter()
            p_gated = {}
            for cons in ("both", "mean"):
                if allowed.all():
                    p_gated[cons] = np.full(m.N, qoe_safe.select_price(tab, cons))
                elif not allowed.any():
                    p_gated[cons] = np.full(m.N, np.inf)
                else:
                    if cons == "both":
                        gt = gated_table(s, xs_d, allowed, rationing, p_q, n_fine)
                    p_gated[cons] = np.where(allowed, qoe_safe.select_price(gt, cons), np.inf)
            t_gate = time.perf_counter() - t2
            uni = {
                "no_nef": np.inf,
                "safe_strict": qoe_safe.select_price(tab, "both"),
                "safe_mean": qoe_safe.select_price(tab, "mean"),          # eta = 0 on the mean only (former T1)
                "safe_mean_eta": qoe_safe.select_price(tab, "mean", eta_mean=float(pc["eta_mean"])),
                "quantile": p_q,
                "congestion_noext": baselines.clearing_from_surface(S_noext[wi], min(q_conf, S_noext[wi].scales[-1])),
                "flat_rev": flat_rev,
                "revenue_max": baselines.best_price(s, "profit"),
            }
            for k in pc["policies"]:
                if k == "safe_gated":
                    pv = p_gated["both"]
                elif k == "safe_gated_mean":
                    pv = p_gated["mean"]
                else:
                    pv = np.full(m.N, uni[k])
                out["prices"][rationing][k][str(t)] = per_profile(pv)
            out["compute_s"].setdefault(rationing, {})[str(t)] = {"score_table": t_tab, "gating": t_gate}
            shown = {k: out["prices"][rationing][k][str(t)][ref_by_class["video"]] for k in pc["policies"]}
            shown["voice"] = out["prices"][rationing].get("safe_gated_mean", {}).get(str(t), {}).get(ref_by_class["voice"])
            print(f"[{rationing}] slot {t}h  allowed={allowed.astype(int).tolist()}  " +
                  "  ".join(f"{k}={v if v == 'inf' else round(v, 5)}" for k, v in shown.items()))

    path = rpath(pc["prices_file"])
    jdump(out, path)
    print(f"written {path}  (total {time.perf_counter() - t0:.0f} s)")


if __name__ == "__main__":
    main()
