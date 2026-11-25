"""Does the conformal price really keep the overload probability below ε?  (simulation, a few minutes)

Every day d the NEF posts the QoD price of the peak hour BEFORE the demand of that day is known.
Each pricing rule is a demand scale q_d it prepares for; the posted price is the clearing price
p_d = p_clr(q_d) (equilibrium load = capacity R at scale q_d). The demand is monotone in the scale,
so the day is OVERLOADED (paid QoD requests must be refused) exactly when the realised scale x_d > q_d.

Rules compared (all use only the past days, window of n days):
  conformal       q = conformal quantile of the window, k = ceil((n+1)(1-ε))   <- ours (distribution-free)
  adaptive        adaptive conformal (Gibbs & Candès 2021), for demand that drifts  <- ours, variant
  gaussian        q = mean + z_{1-ε} std   (assumes a normal demand)
  mean            q = mean of the window   (price set on the forecast mean)
  tiers           rule-based tiered price from the load observed the day before (3 tiers):
                  a representative of industry practice (telemetry + pre-defined rules)
Demand scenarios (scale x = demand / forecast, cv = scenario.forecast_cv of lab.yaml):
  normal          x = 1 + cv Z                                    (exchangeable)
  heavy           Student-t with 3 d.o.f., same mean and std      (exchangeable, heavy tail)
  skewed          log-normal, same mean and std                   (exchangeable, skewed)
  surge           normal, then an UNFORECAST +30 % level shift    (not exchangeable: the honest limit)

Outputs (results/sim/):
  figC1_coverage.(png|pdf)       measured overload frequency vs target ε, one panel per scenario
  figC2_cost_of_safety.(png|pdf) at ε = 5 %: overload, rejected QoD requests, profit, best-effort harm
  figC3_surge_timeline.(png|pdf) rolling overload rate around the unforecast surge
  tableC_coverage.(md|csv), c_coverage.json

Usage (core container, same calibration as the prices):
  NEF_LAB_CALIBRATION=results/calibration_testbed.json python3 sim/exp_conformal_coverage.py
  python3 sim/exp_conformal_coverage.py --quick          # smoke test (< 1 min)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import warnings

import numpy as np
from scipy import stats

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import labcommon  # noqa: E402,F401  (puts vendor/nef_pricing on the path)
warnings.filterwarnings("ignore")

EPS = [0.01, 0.05, 0.10, 0.20]
RULES = ["conformal", "adaptive", "gaussian", "mean", "tiers"]
SCEN = ["normal", "heavy", "skewed", "surge"]
LAB = {"conformal": "Conformal quantile (ours)", "adaptive": "Adaptive conformal (ours)",
       "gaussian": "Gaussian quantile", "mean": "Mean-demand price", "tiers": "Rule-based tiers (reactive)"}
SLAB = {"normal": "Normal demand", "heavy": "Heavy tail (Student-t, 3 dof)", "skewed": "Skewed (log-normal)",
        "surge": "Unforecast surge (+30 %)"}
BLUE, ORANGE, YELLOW, GREEN, RED, PURPLE = "#0060C0", "#FF7F00", "#F2C200", "#1FA02C", "#E31A1C", "#8E3FBF"
COL = {"conformal": GREEN, "adaptive": YELLOW, "gaussian": ORANGE, "mean": RED, "tiers": BLUE}
MARK = {"conformal": "o", "adaptive": "D", "gaussian": "^", "mean": "s", "tiers": "v"}


# ============================================================================ demand and rules
def draw_scales(scen, n, cv, rng, shift_at=None, surge=1.3):
    """Sequence of n daily demand scales (mean 1, std cv before any surge)."""
    if scen in ("normal", "surge"):
        x = 1 + cv * rng.standard_normal(n)
    elif scen == "heavy":
        dof = 3
        x = 1 + cv * rng.standard_t(dof, n) / np.sqrt(dof / (dof - 2))
    elif scen == "skewed":
        s = np.sqrt(np.log(1 + cv ** 2))
        x = np.exp(s * rng.standard_normal(n) - s ** 2 / 2)
    else:
        raise ValueError(scen)
    if scen == "surge" and shift_at is not None:
        x[shift_at:] *= surge
    return np.maximum(x, 0.05)


def tiers_scale(x_prev, cv):
    """Reactive 3-tier rule: yesterday's observed load picks the tier (low / normal / high)."""
    lo, hi = 1 - 0.5 * cv, 1 + 0.5 * cv
    return 1 - 0.5 * cv if x_prev < lo else (1 + cv if x_prev > hi else 1.0)


def run_rules(x, n, eps, cv, gamma=0.005):
    """Prepared scale q_d of every rule for days n..len(x)-1 (only past days are used)."""
    from nef_pricing.conformal import AdaptiveConformal, conformal_quantile
    z = stats.norm.ppf(1 - eps)
    D = len(x)
    Q = {r: np.full(D, np.nan) for r in RULES}
    ac = AdaptiveConformal(eps, gamma=gamma, window=n)
    for d in range(n):
        ac.hist.append(float(x[d]))
    for d in range(n, D):
        w = x[d - n:d]
        Q["conformal"][d] = conformal_quantile(w, eps)
        q_ac = ac.quantile()
        Q["adaptive"][d] = q_ac if np.isfinite(q_ac) else np.max(w)
        Q["gaussian"][d] = w.mean() + z * w.std(ddof=1)
        Q["mean"][d] = w.mean()
        Q["tiers"][d] = tiers_scale(x[d - 1], cv)
        ac.update(float(x[d]), Q["adaptive"][d])
    return Q


def coverage(scen, eps, n_hist, days, cv, seed):
    """Overload frequency of every rule (+ 95 % CI) and the raw sequences."""
    n = max(n_hist, int(np.ceil(1 / eps)))  # conformal quantile finite needs n >= 1/eps - 1
    rng = np.random.default_rng([seed, int(1e4 * eps), SCEN.index(scen)])
    shift = n + days // 2 if scen == "surge" else None
    x = draw_scales(scen, n + days, cv, rng, shift_at=shift)
    Q = run_rules(x, n, eps, cv)
    sl = slice(shift, shift + 2 * n) if scen == "surge" else slice(n, n + days)  # surge: first 2n days after the shift
    out = {}
    for r in RULES:
        ov = (x[sl] > Q[r][sl]).astype(float)
        p = ov.mean()
        out[r] = {"overload": p, "ci": 1.96 * np.sqrt(max(p * (1 - p), 1e-12) / len(ov)), "days": len(ov)}
    return out, x, Q, n, shift


# ============================================================================ cost of safety (model)
def econ(s, x, Q, idx, rationing="random"):
    """At the posted price p_clr(q_d): profit, rejected QoD requests, best-effort harm, measured on day x_d."""
    from nef_pricing import baselines, metrics
    from sim.exp_q1_quality import group_metrics
    lo, hi = s.scales[0], s.scales[-1]
    res = {}
    for r in RULES:
        acc = []
        for d in idx:
            p = baselines.clearing_from_surface(s, float(np.clip(Q[r][d], lo, hi)))
            m, out = baselines.slot_outcome(s, p, float(x[d]), rationing)
            sm = metrics.summarize(m, out)
            g = group_metrics(m, out)
            acc.append([sm["profit"], 100 * sm["rejection_rate"], 1e3 * (-g["nonbuyers_dmos"]) * (1 - g["buyers_share"]),
                        g["dmos"]])
        a = np.asarray(acc)
        res[r] = {k: float(v) for k, v in zip(("profit", "rejected_pct", "harm_1e3", "dmos"), a.mean(axis=0))}
        res[r].update({k + "_ci": float(1.96 * v / np.sqrt(len(a))) for k, v in
                       zip(("profit", "rejected_pct", "harm_1e3", "dmos"), a.std(axis=0, ddof=1))})
    return res


# ============================================================================ figures
def style():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "serif", "font.serif": ["STIXGeneral", "DejaVu Serif"], "mathtext.fontset": "stix",
                         "savefig.dpi": 300, "font.size": 8, "axes.titlesize": 8.5, "axes.labelsize": 8,
                         "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 7, "axes.grid": True,
                         "grid.alpha": 0.3, "grid.linestyle": ":", "axes.spines.top": False, "axes.spines.right": False,
                         "patch.force_edgecolor": True, "patch.edgecolor": "black", "patch.linewidth": 0.5})
    return plt


def save(fig, out, name):
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(out, f"{name}.{ext}"), bbox_inches="tight")
    print(f"  -> {os.path.relpath(out)}/{name}.(png|pdf)")


CLIP = 60.0


def fig_coverage(C, out, note=None):
    plt = style()
    from matplotlib.lines import Line2D
    fig, axs = plt.subplots(1, 4, figsize=(7.16, 2.15), sharey=True)
    e = np.array(EPS) * 100
    ymax = 0
    for k, sc in enumerate(SCEN):
        ax = axs[k]
        ax.fill_between([0, 22], [0, 22], [0, 0], color="#E9F5E9", lw=0, zorder=0)
        ax.plot([0, 22], [0, 22], color="black", ls="--", lw=1.0, zorder=1)
        for r in RULES:
            y = np.array([100 * C[sc][str(ep)][r]["overload"] for ep in EPS])
            h = np.array([100 * C[sc][str(ep)][r]["ci"] for ep in EPS])
            ymax = max(ymax, np.max(np.minimum(y + h, CLIP)))
            yc = np.minimum(y, CLIP)
            ax.errorbar(e, yc, yerr=np.where(y > CLIP, 0, h), color=COL[r], marker=MARK[r], ms=4.2, mec="black", mew=0.6,
                        lw=1.4, capsize=1.5, zorder=3 if r in ("conformal", "adaptive") else 2)
            if np.any(y > CLIP):  # off-scale: say the real values instead of drawing them at the clip
                ax.text(e[-1], CLIP - 1.5 - 4.5 * (r == "tiers"), f"{LAB[r].split(' (')[0].split()[0].lower()}: "
                        f"{y[y > CLIP].min():.0f}–{y[y > CLIP].max():.0f} %", ha="right", va="top", fontsize=6,
                        color=COL[r], fontweight="bold")
        ax.set_title(SLAB[sc])
        ax.set_xticks(e)
        ax.set_xticklabels([f"{v:g}" for v in e])
        ax.set_xlim(0, 22)
        ax.set_xlabel("target ε (%)")
    axs[0].set_ylabel("measured overload frequency (%)")
    axs[0].set_ylim(0, CLIP + 3)
    axs[0].text(14, 2.0, "guarantee\nmet", fontsize=6.5, color=GREEN, ha="center")
    h = [Line2D([], [], color=COL[r], marker=MARK[r], mec="black", mew=0.6, ms=4.5, lw=1.4, label=LAB[r]) for r in RULES]
    h.append(Line2D([], [], color="black", ls="--", lw=1.0, label="target: overload = ε"))
    fig.legend(handles=h, loc="upper center", bbox_to_anchor=(0.5, -0.09), ncol=6, frameon=False, columnspacing=1.0,
               handletextpad=0.4)
    if note:
        fig.text(0.5, 1.02, note, ha="center", color=RED, fontweight="bold")
    save(fig, out, "figC1_coverage")
    plt.close(fig)


def fig_cost(E, C, out, note=None):
    plt = style()
    keys = [("overload", "Overload days (%)"), ("rejected_pct", "Rejected QoD requests (%)"),
            ("profit", "Operator profit (peak hour)"), ("harm_1e3", "Best-effort MOS loss ($10^{-3}$)")]
    fig, axs = plt.subplots(1, 4, figsize=(7.16, 2.0))
    w = 0.16
    xs = np.arange(len(SCEN))
    for k, (key, lab) in enumerate(keys):
        ax = axs[k]
        for j, r in enumerate(RULES):
            if key == "overload":
                v = [100 * C[sc]["0.05"][r]["overload"] for sc in SCEN]
                h = [100 * C[sc]["0.05"][r]["ci"] for sc in SCEN]
            else:
                v = [E[sc][r][key] for sc in SCEN]
                h = [E[sc][r][key + "_ci"] for sc in SCEN]
            ax.bar(xs + (j - 2) * w, v, w, yerr=h, color=COL[r], capsize=1, error_kw={"elinewidth": 0.6}, label=LAB[r])
        if key == "overload":
            ax.axhline(5, color="black", ls="--", lw=1.0)
        ax.set_xticks(xs)
        ax.set_xticklabels(["normal", "heavy", "skewed", "surge"], fontsize=6.5)
        ax.set_title(lab, fontsize=8)
    fig.suptitle("ε = 5 %: what each rule costs and protects", y=1.02, fontsize=8.5)
    if note:
        fig.text(0.5, 1.08, note, ha="center", color=RED, fontweight="bold")
    fig.tight_layout()
    h, l = axs[0].get_legend_handles_labels()
    fig.legend(h, l, loc="upper center", bbox_to_anchor=(0.5, 0.0), ncol=5, frameon=False, fontsize=7)
    save(fig, out, "figC2_cost_of_safety")
    plt.close(fig)


def fig_surge(x, Q, n, shift, out, note=None, win=30):
    plt = style()
    fig, ax = plt.subplots(figsize=(3.5, 2.2))
    d0, d1 = max(n, shift - 3 * n), min(len(x), shift + 4 * n)
    t = np.arange(d0, d1) - shift
    for r in RULES:
        ov = (x[d0:d1] > Q[r][d0:d1]).astype(float)
        roll = np.convolve(ov, np.ones(win) / win, mode="same")
        ax.plot(t, 100 * roll, color=COL[r], lw=1.5 if r in ("conformal", "adaptive") else 1.1, label=LAB[r])
    ax.axhline(5, color="black", ls="--", lw=1.0)
    ax.axvline(0, color="0.3", lw=0.8)
    ax.axvspan(0, n, color="#FDE7EF", lw=0, zorder=0)
    ax.text(n / 2, 104, "window refills", ha="center", va="bottom", fontsize=6.5, color="0.3")
    ax.set_ylim(0, 100)
    ax.set_xlabel("days since the unforecast +30 % surge")
    ax.set_ylabel(f"overload, rolling {win} days (%)")
    ax.set_title("ε = 5 %: recovery after a demand shift", pad=12)
    ax.legend(fontsize=6, loc="upper center", bbox_to_anchor=(0.5, -0.24), ncol=2, frameon=False)
    if note:
        fig.text(0.5, 1.02, note, ha="center", color=RED, fontweight="bold", fontsize=7)
    save(fig, out, "figC3_surge_timeline")
    plt.close(fig)


# ============================================================================ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--history", type=int, default=60, help="days in the window (as pricing.history_days)")
    ap.add_argument("--days", type=int, default=4000, help="evaluated days per scenario and ε")
    ap.add_argument("--econ-days", type=int, default=300, help="days evaluated with the model (cost of safety)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--note", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--no-model", action="store_true", help=argparse.SUPPRESS)  # coverage only (tests)
    ap.add_argument("--replot", action="store_true", help="redraw the figures from c_coverage.json (seconds)")
    ap.add_argument("--out", default=os.path.join(ROOT, "results", "sim"))
    a = ap.parse_args()
    if a.quick:
        a.days, a.econ_days = 800, 40
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()
    from labcommon import load_cfg
    cv = float(load_cfg()["scenario"]["forecast_cv"])
    print(f"[coverage] cv = {cv}, window n = {a.history} days (raised to 1/ε for ε = 1 %), {a.days} days per case")

    if a.replot:
        d = json.load(open(os.path.join(a.out, "c_coverage.json")))
        C, E = d["coverage"], d["cost"]
        x, Q, n, shift = coverage("surge", 0.05, d["history"], d["days"], d["cv"], a.seed)[1:]
        fig_coverage(C, a.out, a.note)
        if E:
            fig_cost(E, C, a.out, a.note)
        fig_surge(x, Q, n, shift, a.out, a.note)
        return

    C, keep = {}, {}
    for sc in SCEN:
        C[sc] = {}
        for ep in EPS:
            res, x, Q, n, shift = coverage(sc, ep, a.history, a.days, cv, a.seed)
            C[sc][str(ep)] = res
            if ep == 0.05:
                keep[sc] = (x, Q, n, shift)
            print(f"  {sc:7s} ε={ep:4.0%}  " + "  ".join(f"{r}={100 * res[r]['overload']:5.2f}%" for r in RULES))

    E = None
    if not a.no_model:
        from nef_pricing import baselines
        from labcommon import model_bundle, peak_slot
        models = model_bundle()[2]
        tp = peak_slot()
        s = baselines.surfaces([models[tp]], tag="coverage", n_p=26)[0]
        print(f"[cost] peak slot {tp}h, calibration: {os.environ.get('NEF_LAB_CALIBRATION') or 'analytical'}")
        E = {}
        for sc in SCEN:
            x, Q, n, shift = keep[sc]
            start = shift if sc == "surge" else n
            idx = np.linspace(start, min(len(x) - 1, start + (2 * n if sc == "surge" else a.days) - 1),
                              a.econ_days).astype(int)
            E[sc] = econ(s, x, Q, idx)
            print(f"  {sc:7s} " + "  ".join(f"{r}: profit {E[sc][r]['profit']:.2f} rej {E[sc][r]['rejected_pct']:.2f}% "
                                            f"harm {E[sc][r]['harm_1e3']:.2f}" for r in ("conformal", "gaussian", "mean", "tiers")))

    # ---- table
    hdr = ["scenario", "ε"] + [LAB[r] for r in RULES]
    rows = []
    for sc in SCEN:
        for ep in EPS:
            rows.append([SLAB[sc], f"{ep:.0%}"] + [f"{100 * C[sc][str(ep)][r]['overload']:.2f} ± {100 * C[sc][str(ep)][r]['ci']:.2f}"
                                                   for r in RULES])
    md = ["| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)] + ["| " + " | ".join(r) + " |" for r in rows]
    if E:
        md += ["", "Cost of safety at ε = 5 % (peak hour, random admission)", "",
               "| scenario | rule | profit | rejected QoD (%) | best-effort MOS loss (1e-3) | Δ mean MOS |", "|---|---|---|---|---|---|"]
        for sc in SCEN:
            for r in RULES:
                e = E[sc][r]
                md.append(f"| {sc} | {LAB[r]} | {e['profit']:.3f} | {e['rejected_pct']:.2f} | {e['harm_1e3']:.2f} | {e['dmos']:.4f} |")
    with open(os.path.join(a.out, "tableC_coverage.md"), "w") as f:
        f.write("Measured overload frequency (%) vs target ε — mean ± 95 % CI. Surge: first 2n days after the shift.\n\n"
                + "\n".join(md) + "\n")
    with open(os.path.join(a.out, "tableC_coverage.csv"), "w", newline="") as f:
        csv.writer(f).writerows([hdr] + rows)
    with open(os.path.join(a.out, "c_coverage.json"), "w") as f:
        json.dump({"coverage": C, "cost": E, "cv": cv, "history": a.history, "days": a.days}, f, indent=1)
    print("\n".join(md))

    fig_coverage(C, a.out, a.note)
    if E:
        fig_cost(E, C, a.out, a.note)
    x, Q, n, shift = keep["surge"]
    fig_surge(x, Q, n, shift, a.out, a.note)
    print(f"[coverage] done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
