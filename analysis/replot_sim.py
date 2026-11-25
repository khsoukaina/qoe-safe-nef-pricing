"""Redraw the simulation figures Q1a/Q1b/Q1c from the saved CSV files (seconds, no re-run).

Same style as analysis/make_figures.py (IEEE: serif fonts, blue/orange/yellow/green/red).
Usage:  python3 analysis/replot_sim.py
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np  # noqa: E402

from analysis.make_figures import COL, LAB, plt  # noqa: E402  (style + palette)
from labcommon import rpath  # noqa: E402

SIM = rpath("results", "sim")
KEYS = ("dmos", "buyers_uplift", "buyers_share", "nonbuyers_dmos", "dissat", "dissat_base", "p5", "p5_base",
        "profit", "welfare", "reject")


def save(fig, name):
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(SIM, f"{name}.{ext}"), bbox_inches="tight")
    plt.close(fig)
    print(f"  -> results/sim/{name}")


def who_gains():
    rows = {}
    with open(os.path.join(SIM, "q1_who_gains.csv")) as f:
        rd = csv.reader(f)
        next(rd)
        for r in rd:
            vals = list(map(float, r[2:]))
            rows[(r[0], r[1])] = {k: (vals[2 * i], vals[2 * i + 1]) for i, k in enumerate(KEYS)}
    return rows


def q1a(rows):
    pols = [p for p in ("safe_mean", "safe_strict", "safe_gated_mean", "quantile", "congestion_noext", "flat_rev",
                        "revenue_max") if ("random", p) in rows]
    fig, ax = plt.subplots(1, 4, figsize=(15, 3.6))
    xs = np.arange(len(pols))
    spec = [("buyers_uplift", "Granted sessions: Δ MOS", 1.0), ("nonbuyers_dmos", "All other sessions: Δ MOS", 1.0),
            ("dissat", "Dissatisfied sessions (MOS<3), %", 1.0), ("profit", "Operator profit (peak)", 1.0)]
    for j, (k, title, _) in enumerate(spec):
        m = [rows[("random", p)][k][0] for p in pols]
        h = [rows[("random", p)][k][1] for p in pols]
        ax[j].bar(xs, m, yerr=h, capsize=3, color=[COL.get(p) for p in pols])
        if k == "dissat":
            base = rows[("random", pols[0])]["dissat_base"][0]
            ax[j].axhline(base, color=COL["no_nef"], ls="--", lw=1.4, label=f"no NEF ({base:.1f}%)")
            ax[j].legend(fontsize=7)
        ax[j].axhline(0, color="k", lw=0.8)
        ax[j].set_xticks(xs)
        ax[j].set_xticklabels([LAB.get(p, p) for p in pols], rotation=35, ha="right", fontsize=8)
        ax[j].set_title(title)
    fig.suptitle("Simulation (testbed-calibrated model), peak hours, random admission, 95% CI over 10 seeds", y=1.03)
    save(fig, "figQ1a_who_gains")


def q1b(rows):
    fig, ax = plt.subplots(1, 3, figsize=(11, 3.3))
    for j, (k, title) in enumerate([("dmos", "Δ mean MOS vs no NEF"), ("profit", "Operator profit (peak)"),
                                    ("welfare", "Welfare gain (peak)")]):
        for r_, rat in enumerate(("random", "priority")):
            for q_, pol in enumerate(("safe_mean", "safe_gated_mean")):
                if (rat, pol) not in rows:
                    continue
                mu, h = rows[(rat, pol)][k]
                ax[j].bar(r_ * 3 + q_, mu, yerr=h, capsize=3, color=COL[pol], label=LAB[pol] if r_ == 0 else None)
        ax[j].set_xticks([0.5, 3.5])
        ax[j].set_xticklabels(["random admission", "priority admission"])
        ax[j].set_title(title)
    ax[0].legend(fontsize=8)
    save(fig, "figQ1b_gating")


def q1c():
    d = np.loadtxt(os.path.join(SIM, "q1_shift.csv"), delimiter=",", skiprows=1, ndmin=2)
    fig, ax = plt.subplots(1, 2, figsize=(9, 3.3))
    ax[0].errorbar(d[:, 0], d[:, 1], yerr=d[:, 2], marker="o", color=COL["safe_mean"], capsize=3)
    ax[0].set_xlabel("φ = share of deferrable sessions (video, web) leaving the peak")
    ax[0].set_ylabel("Δ mean MOS vs no NEF (evening)")
    ax[0].set_title("QoE side")
    ax[1].errorbar(d[:, 0], d[:, 3], yerr=d[:, 4], marker="s", color=COL["no_nef"], capsize=3)
    ax[1].set_xlabel("φ")
    ax[1].set_ylabel("Operator profit (evening)")
    ax[1].set_title("Revenue side")
    fig.text(0.5, -0.07, "φ is a behavioural hypothesis (to be calibrated with operator data); φ = 0 is the original model.",
             ha="center", fontsize=8, style="italic")
    save(fig, "figQ1c_shift")


if __name__ == "__main__":
    r = who_gains()
    q1a(r)
    q1b(r)
    q1c()
