"""Experiment Q1 (simulation, vendored model): making the QoE result visible and larger.

Why the mean MOS gain of the strict QoE-safe policy looks tiny (+0.001): by construction it
may not lower the mean, and the guaranteed sessions are a few percent of the population, so
any gain is diluted in a mean over everybody. Q1 answers three questions with the SAME model:

  Q1a  WHO gains, WHO loses (peak hours)? For every policy: MOS uplift of the sessions that got
       a guarantee ("buyers"), MOS change of everybody else ("non-buyers", same sessions as
       without NEF), share of dissatisfied sessions (MOS < 3), 5th-percentile MOS.
  Q1b  GATING (new variant, testbed policy "safe_gated"): post the QoE-safe price only to the
       classes whose marginal-gain test is >= 0 at the forecast load, close the others.
  Q1c  TIME SHIFTING (scenario hypothesis, to be validated with operator data): a share phi
       of the deferrable sessions (video, web) leaves the peak for the shoulder hours when the
       posted peak price is above the shoulder price. phi = 0 is exactly the previous model.

Prices of Q1a/Q1b are read from results/prices/prices.json (same pre-computation as the
testbed); Q1c recomputes the QoE-safe prices on the shifted demand.

Usage:  python sim/exp_q1_quality.py [--quick]
Outputs: results/sim/figQ1a_who_gains.(png|pdf), figQ1b_gating.(png|pdf), figQ1c_shift.(png|pdf),
         results/sim/q1_*.csv, results/sim/q1_key_numbers.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import warnings

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from scipy import stats  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from labcommon import af_class_index, class_names, from_fnum, load_cfg, model_bundle, peak_slot, rpath  # noqa: E402
from nef_pricing import admission, baselines, metrics, qoe_safe  # noqa: E402
from nef_pricing.calibration import slot_models  # noqa: E402
from nef_pricing.demand import class_profiles  # noqa: E402
from nef_pricing.followers import nash  # noqa: E402

warnings.filterwarnings("ignore")
OUT = rpath("results", "sim")
os.makedirs(OUT, exist_ok=True)
plt.rcParams.update({"figure.dpi": 110, "savefig.dpi": 180, "font.size": 10, "axes.grid": True, "grid.alpha": 0.3,
                     "axes.spines.top": False, "axes.spines.right": False, "legend.fontsize": 8})
LAB = {"no_nef": "No NEF", "safe_mean": "QoE-safe (mean, η=0)", "safe_gated_mean": "QoE-safe mean + gating", "safe_strict": "QoE-safe (strict)", "safe_gated": "QoE-safe + class gating",
       "safe_mean_eta": "QoE-safe (mean, η = 0.01)", "quantile": "Conformal quantile",
       "congestion_noext": "Congestion pricing w/o externality", "flat_rev": "Flat commercial price",
       "revenue_max": "Revenue max."}
COL = {"no_nef": "#1f5fa8", "no_nef#2": "#9ecae1", "safe_mean": "#f28e1c", "safe_strict": "#f2c418",
       "safe_gated_mean": "#fdb863", "safe_gated": "#d4a106", "safe_mean_eta": "#fee08b",
       "quantile": "#4a90d9", "congestion_noext": "#0b2f63", "flat_rev": "#c4580f", "revenue_max": "#8a3b08"}
BLUE, LBLUE, ORANGE, YELLOW, DORANGE = "#1f5fa8", "#6fa8dc", "#f28e1c", "#f2c418", "#c4580f"
DISSAT = 3.0


def ci95(v):
    v = np.asarray(v, float)
    if len(v) < 2:
        return float(v.mean()), 0.0
    return float(v.mean()), float(stats.t.ppf(0.975, len(v) - 1) * v.std(ddof=1) / np.sqrt(len(v)))


def save(fig, name):
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(OUT, f"{name}.{ext}"), bbox_inches="tight")
    plt.close(fig)
    print(f"  -> {name}")


# ------------------------------------------------------------------------ group metrics
def group_metrics(m, out, grid=200):
    """Session-level comparison with the no-NEF situation of the SAME draw (same sessions)."""
    u = (np.arange(grid) + 0.5) / grid
    D, D0 = m.degr.D(out.load), m.degr.D(m.base_load())
    vals, base, w, buyer = [], [], [], []
    for i, s in enumerate(m.sens):
        q = s.Q(u)
        m1, m0 = m.q2[i] - q * D[i], m.q2[i] - q * D0[i]
        wi = m.X[i] / grid
        if out.rationing == "random":
            v_d = 1.0 - out.y_demand[i] / m.X[i]
            dem = u >= v_d
            # requesters are served with probability phi
            vals += [m1[~dem], np.full(dem.sum(), m.q2[i]), m1[dem]]
            base += [m0[~dem], m0[dem], m0[dem]]
            w += [np.full((~dem).sum(), wi), np.full(dem.sum(), wi * out.phi), np.full(dem.sum(), wi * (1 - out.phi))]
            buyer += [np.zeros((~dem).sum(), bool), np.ones(dem.sum(), bool), np.zeros(dem.sum(), bool)]
        else:
            thr = out.u_min[i] if out.u_min is not None else 1.0 - out.y_served[i] / m.X[i]
            sv = u >= thr
            vals.append(np.where(sv, m.q2[i], m1)); base.append(m0)
            w.append(np.full(grid, wi)); buyer.append(sv)
    v, b, w, by = map(np.concatenate, (vals, base, w, buyer))
    keep = w > 0
    v, b, w, by = v[keep], b[keep], w[keep], by[keep]
    nb = ~by
    return {"dmos": float(np.average(v - b, weights=w)),
            "buyers_uplift": float(np.average(v[by] - b[by], weights=w[by])) if by.any() else 0.0,
            "buyers_share": float(w[by].sum() / w.sum()),
            "nonbuyers_dmos": float(np.average(v[nb] - b[nb], weights=w[nb])) if nb.any() else 0.0,
            "dissat": 100 * float(w[v < DISSAT].sum() / w.sum()), "dissat_base": 100 * float(w[b < DISSAT].sum() / w.sum()),
            "p5": metrics.weighted_quantile(v, w, 0.05), "p5_base": metrics.weighted_quantile(b, w, 0.05)}


def class_price_vector(pr_slot, prof_cls, names, cidx):
    by_cls = {prof_cls[r]: from_fnum(p) for r, p in pr_slot.items()}
    return np.array([by_cls[names[j]] for j in cidx])


def evaluate(models, slots, pvecs, rationing, n_seeds, n_draws, cv):
    """pvecs: {slot: price vector per AF}. Returns per-seed averages of the group metrics."""
    per_seed = []
    for r in range(n_seeds):
        rng = np.random.default_rng(1000 + r)
        acc = []
        for t in slots:
            m0 = models[t]
            pv = pvecs[t]
            y0 = None
            for x in np.maximum(1 + cv * rng.standard_normal(n_draws), 0.05):
                m = m0.with_X(m0.X * x)
                if np.all(np.isinf(pv)):
                    out = admission.Outcome(np.inf, np.zeros(m.N), np.zeros(m.N), m.base_load())
                    gm = group_metrics(m, out)
                    sm = metrics.summarize(m, out, 0.0)
                else:
                    y = nash(m, pv, y0=y0)
                    y0 = y
                    out = admission.admit(m, y, pv, rationing)
                    gm = group_metrics(m, out)
                    sm = metrics.summarize(m, out)
                gm.update({"profit": sm["profit"], "welfare": sm["welfare"], "reject": 100 * sm["rejection_rate"]})
                acc.append(gm)
        per_seed.append({k: float(np.mean([a[k] for a in acc])) for k in acc[0]})
    return per_seed


# ------------------------------------------------------------------------ Q1a / Q1b
def part_ab(cfg, tab, n_seeds, n_draws):
    P, cal, models = model_bundle()
    names, cidx = class_names(P), af_class_index(P)
    tp = peak_slot()
    peak = [tp - 1, tp, tp + 1]
    cv = float(cfg["scenario"]["forecast_cv"])
    prof_cls = tab["meta"]["profiles"]
    policies = [p for p in tab["meta"]["policies"]]
    res = {}
    for rationing in ("random", "priority"):
        res[rationing] = {}
        for pol in policies:
            pv = {t: class_price_vector(tab["prices"][rationing][pol][str(t)], prof_cls, names, cidx) for t in peak}
            res[rationing][pol] = evaluate(models, peak, pv, rationing, n_seeds, n_draws, cv)
            d = res[rationing][pol]
            print(f"[{rationing}] {pol:18s} dMOS={np.mean([z['dmos'] for z in d]):+.4f} "
                  f"buyers={np.mean([z['buyers_uplift'] for z in d]):+.3f} ({100 * np.mean([z['buyers_share'] for z in d]):.1f}%) "
                  f"non-buyers={np.mean([z['nonbuyers_dmos'] for z in d]):+.4f} "
                  f"dissat={np.mean([z['dissat'] for z in d]):.2f}% (base {np.mean([z['dissat_base'] for z in d]):.2f}%) "
                  f"profit={np.mean([z['profit'] for z in d]):.2f}", flush=True)
    rows = []
    for rationing, d in res.items():
        for pol, ps in d.items():
            line = [rationing, pol]
            for k in ("dmos", "buyers_uplift", "buyers_share", "nonbuyers_dmos", "dissat", "dissat_base", "p5", "p5_base",
                      "profit", "welfare", "reject"):
                mu, h = ci95([z[k] for z in ps])
                line += [mu, h]
            rows.append(line)
    hdr = ["rationing", "policy"] + [f"{k}{s}" for k in ("dmos", "buyers_uplift", "buyers_share", "nonbuyers_dmos", "dissat",
                                                         "dissat_base", "p5", "p5_base", "profit", "welfare", "reject")
                                      for s in ("", "_ci")]
    np.savetxt(os.path.join(OUT, "q1_who_gains.csv"), np.array(rows, dtype=object), fmt="%s", delimiter=",",
               header=",".join(hdr), comments="")

    # ---- figure Q1a: buyers vs non-buyers, dissatisfied share (random admission)
    show = [p for p in ("safe_mean", "safe_strict", "safe_gated_mean", "quantile", "congestion_noext", "flat_rev", "revenue_max") if p in policies]
    fig, ax = plt.subplots(1, 3, figsize=(13, 3.8))
    for k_, rationing in enumerate(["random"]):
        d = res[rationing]
        xs = np.arange(len(show))
        bu = [ci95([z["buyers_uplift"] for z in d[p]]) for p in show]
        nb = [ci95([z["nonbuyers_dmos"] for z in d[p]]) for p in show]
        ax[0].bar(xs, [b[0] for b in bu], yerr=[b[1] for b in bu], color=[COL[p] for p in show], capsize=3)
        ax[0].set_title("MOS gain of the sessions that got a guarantee")
        ax[1].bar(xs, [b[0] for b in nb], yerr=[b[1] for b in nb], color=[COL[p] for p in show], capsize=3)
        ax[1].axhline(0, color="k", lw=0.8)
        ax[1].set_title("MOS change of everybody else (best effort)")
        ds = [ci95([z["dissat"] - z["dissat_base"] for z in d[p]]) for p in show]
        ax[2].bar(xs, [b[0] for b in ds], yerr=[b[1] for b in ds], color=[COL[p] for p in show], capsize=3)
        ax[2].axhline(0, color="k", lw=0.8)
        ax[2].set_title("Change in dissatisfied sessions (MOS < 3), pts of %")
        for a in ax:
            a.set_xticks(xs)
            a.set_xticklabels([LAB[p] for p in show], rotation=30, ha="right")
    fig.suptitle("Peak hours, random admission, 95% CI over seeds: who gains, who pays", y=1.02)
    save(fig, "figQ1a_who_gains")

    # ---- figure Q1b: gating vs strict
    if "safe_gated_mean" in policies:
        fig, ax = plt.subplots(1, 3, figsize=(11, 3.4))
        for j, (k, title) in enumerate([("dmos", "Δ mean MOS vs no NEF"), ("profit", "Operator profit (peak)"),
                                        ("welfare", "Welfare gain (peak)")]):
            for r_, rationing in enumerate(("random", "priority")):
                for q_, pol in enumerate(("safe_mean", "safe_gated_mean")):
                    mu, h = ci95([z[k] for z in res[rationing][pol]])
                    ax[j].bar(r_ * 3 + q_, mu, yerr=h, color=COL[pol], capsize=3, label=LAB[pol] if r_ == 0 else None)
            ax[j].set_xticks([0.5, 3.5])
            ax[j].set_xticklabels(["random", "priority"])
            ax[j].set_title(title)
        ax[0].legend()
        save(fig, "figQ1b_gating")
    return res


# ------------------------------------------------------------------------ Q1c shifting
def part_c(cfg, phis, n_seeds, n_draws, quick):
    P, cal, models = model_bundle()
    names, cidx = class_names(P), af_class_index(P)
    tp = peak_slot()
    peak = [tp - 1, tp, tp + 1]
    shoulder = [tp - 3, tp - 2, tp + 2, tp + 3]
    window = sorted(peak + shoulder)
    cv = float(cfg["scenario"]["forecast_cv"])
    flexible = {"video", "web"}
    Xm0 = class_profiles(P["classes"], P["slots"])
    zd = np.random.default_rng(21).standard_normal(15 if quick else 40)
    rows = []
    for phi in phis:
        Xm = Xm0.copy()
        for j, c in enumerate(names):
            if c in flexible and phi > 0:
                moved = phi * Xm[peak, j].sum()
                Xm[peak, j] *= (1 - phi)
                Xm[shoulder, j] += moved / len(shoulder)
        mods = slot_models(P, cal, X_matrix=Xm)
        S = baselines.surfaces([mods[t] for t in window], tag=f"q1shift_{phi}", n_p=14 if quick else 26)
        pv = {}
        for s, t in zip(S, window):
            xs_d = np.clip(1 + cv * zd, s.scales[0], s.scales[-1])
            p_q = baselines.clearing_from_surface(s, 1 + 1.645 * cv)
            tb = qoe_safe.score_table(s, xs_d, "random", p_min=p_q, n_fine=15 if quick else 40)
            pv[t] = np.full(mods[t].N, qoe_safe.select_price(tb, "both"))
        # shift is only plausible if the peak is priced above the shoulders
        pk = np.mean([pv[t][0] for t in peak if np.isfinite(pv[t][0])] or [np.inf])
        sh = np.mean([pv[t][0] if np.isfinite(pv[t][0]) else 0.0 for t in shoulder])
        res = evaluate(mods, window, pv, "random", n_seeds, n_draws, cv)
        mos_pol = _evening_mos(mods, window, pv, n_seeds, n_draws, cv)
        mos_ref = _evening_mos(models, window, {t: np.full(models[t].N, np.inf) for t in window}, n_seeds, n_draws, cv)
        d = np.array(mos_pol) - np.array(mos_ref)
        mu, h = ci95(d)
        pr, prh = ci95([z["profit"] for z in res])
        rows.append([phi, mu, h, pr, prh, pk, sh])
        print(f"[shift] phi={phi:.2f} evening dMOS={mu:+.4f}±{h:.4f}  profit={pr:.2f}  peak p={pk:.4g} shoulder p={sh:.4g}",
              flush=True)
    rows = np.array(rows)
    np.savetxt(os.path.join(OUT, "q1_shift.csv"), rows, delimiter=",",
               header="phi,dmos_evening,ci,profit,profit_ci,peak_price,shoulder_price", comments="")
    fig, ax = plt.subplots(1, 2, figsize=(9, 3.4))
    ax[0].errorbar(rows[:, 0], rows[:, 1], yerr=rows[:, 2], marker="o", color=COL["safe_strict"], capsize=3)
    ax[0].axhline(0, color="k", lw=0.8)
    ax[0].set_xlabel("φ = share of deferrable sessions (video, web) leaving the peak")
    ax[0].set_ylabel("Δ evening mean MOS vs no NEF")
    ax[0].set_title("QoE-safe pricing with time shifting")
    ax[1].errorbar(rows[:, 0], rows[:, 3], yerr=rows[:, 4], marker="s", color=BLUE, capsize=3)
    ax[1].set_xlabel("φ")
    ax[1].set_ylabel("Operator profit (evening)")
    ax[1].set_title("Revenue side")
    fig.text(0.5, -0.06, "φ is a behavioural hypothesis (to be calibrated with operator data); φ = 0 is the original model.",
             ha="center", fontsize=8, style="italic")
    save(fig, "figQ1c_shift")
    return rows


def _evening_mos(mods, window, pv, n_seeds, n_draws, cv):
    """Session-weighted mean MOS over the window, one value per seed (same draws as evaluate)."""
    out_ = []
    for r in range(n_seeds):
        rng = np.random.default_rng(1000 + r)
        num, den = 0.0, 0.0
        for t in window:
            m0 = mods[t]
            y0 = None
            for x in np.maximum(1 + cv * rng.standard_normal(n_draws), 0.05):
                m = m0.with_X(m0.X * x)
                if np.all(np.isinf(pv[t])):
                    out = admission.Outcome(np.inf, np.zeros(m.N), np.zeros(m.N), m.base_load())
                    s = metrics.summarize(m, out, 0.0)
                else:
                    y = nash(m, pv[t], y0=y0)
                    y0 = y
                    s = metrics.summarize(m, admission.admit(m, y, pv[t], "random"))
                num += s["mos_mean"] * m.X.sum()
                den += m.X.sum()
        out_.append(num / den)
    return out_


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", choices=["ab", "c"], default=None)
    args = ap.parse_args()
    cfg = load_cfg()
    n_seeds = 3 if args.quick else 10
    n_draws = 10 if args.quick else 40
    key = {}
    if args.only in (None, "ab"):
        path = rpath(cfg["pricing"]["prices_file"])
        if not os.path.exists(path):
            sys.exit("run pricing/precompute_prices.py first")
        tab = json.load(open(path))
        res = part_ab(cfg, tab, n_seeds, n_draws)
        key["who_gains"] = {r: {p: {k: float(np.mean([z[k] for z in v])) for k in v[0]} for p, v in d.items()}
                            for r, d in res.items()}
    if args.only in (None, "c"):
        rows = part_c(cfg, [0.0, 0.05, 0.1, 0.2] if not args.quick else [0.0, 0.1], n_seeds, n_draws, args.quick)
        key["shift"] = rows.tolist()
    with open(os.path.join(OUT, "q1_key_numbers.json"), "w") as f:
        json.dump(key, f, indent=2)


if __name__ == "__main__":
    main()
