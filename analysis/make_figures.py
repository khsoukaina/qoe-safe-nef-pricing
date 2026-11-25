"""Figures and tables of the real-core testbed (English labels).

  figT1_latency        decision latency: CDF + breakdown (price, admission, BSF, PCF, user plane),
                       NEF + real core vs NEF alone, per request rate
  figT2_qoe_by_class   measured Δ MOS per class vs no NEF (paired sessions, 95% CI)
  figT3_who_gains      buyers' uplift, non-buyers' change, dissatisfied share change
  figT4_timeline       hour by hour: posted price, granted sessions, measured mean MOS
  figT6_regimes        results indexed by the load regime L0/R (off-peak, shoulder, peak, surge):
                       the study is not tied to an hour or a place, only to the congestion level
  figT5_externality    measured D_i(L) on the user plane vs the model calibration curve
  tableT_scoreboard    one line per policy (csv + markdown)

Usage:  python analysis/make_figures.py --run results/testbed/<tag> [--run ...] [--latency] [--externality]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from scipy import stats  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from labcommon import load_cfg, model_bundle, peak_slot, profile_table, rpath  # noqa: E402

FIG = rpath("results", "figures")
os.makedirs(FIG, exist_ok=True)
# IEEE-paper style: serif (Times-like STIX) fonts, the usual categorical colours
plt.rcParams.update({"figure.dpi": 110, "savefig.dpi": 300, "font.size": 10, "font.family": "serif",
                     "font.serif": ["STIXGeneral", "DejaVu Serif"], "mathtext.fontset": "stix",
                     "axes.grid": True, "grid.alpha": 0.3, "grid.linestyle": ":", "axes.spines.top": False,
                     "axes.spines.right": False, "legend.fontsize": 8, "legend.frameon": True,
                     "axes.titlesize": 10, "axes.labelsize": 10, "lines.linewidth": 1.6, "lines.markersize": 5})
# vivid categorical colours (no pastel): blue, orange, yellow, green, red (+ purple, cyan, grey for extras)
BLUE, ORANGE, YELLOW, PURPLE, GREEN, CYAN, RED, GREY = ("#0060C0", "#FF7F00", "#F2C200", "#8E3FBF",
                                                        "#1FA02C", "#00A9D6", "#E31A1C", "#6E6E6E")
plt.rcParams.update({"patch.force_edgecolor": True, "patch.edgecolor": "black", "patch.linewidth": 0.5})
LAB = {"no_nef": "No NEF", "no_nef#2": "No NEF (A/A repeat)", "safe_mean": "QoE-safe (mean)",
       "safe_strict": "QoE-safe (strict)", "safe_gated_mean": "QoE-safe + gating", "safe_gated": "QoE-safe strict + gating",
       "safe_mean_eta": "QoE-safe (η=0.01)", "quantile": "Conformal quantile",
       "congestion_noext": "Congestion w/o externality", "flat_rev": "Flat commercial price", "revenue_max": "Revenue max."}
COL = {"no_nef": BLUE, "no_nef#2": GREY, "safe_mean": GREEN, "safe_strict": YELLOW, "safe_gated_mean": ORANGE,
       "safe_gated": "#C05A00", "safe_mean_eta": PURPLE, "quantile": CYAN, "congestion_noext": RED,
       "flat_rev": "#A0522D", "revenue_max": "#9E0000"}
CLASSES = ["video", "gaming", "voice", "web"]


def ci95(v):
    v = np.asarray([x for x in v if x is not None and np.isfinite(x)], float)
    if len(v) == 0:
        return np.nan, 0.0
    if len(v) < 2:
        return float(v.mean()), 0.0
    return float(v.mean()), float(stats.t.ppf(0.975, len(v) - 1) * v.std(ddof=1) / np.sqrt(len(v)))


def save(fig, name):
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(FIG, f"{name}.{ext}"), bbox_inches="tight")
    plt.close(fig)
    print(f"  -> {name}")


def jl(path):
    if not os.path.exists(path):
        return []
    return [json.loads(x) for x in open(path) if x.strip()]


# ------------------------------------------------------------------------ latency
def fig_latency():
    data = {lab: jl(rpath("results", "latency", f"latency_{lab}.jsonl")) for lab in ("core", "none")}
    if not data["core"]:
        print("  (no latency data)")
        return
    rates = sorted({r["rate"] for r in data["core"]})
    fig, ax = plt.subplots(1, 3, figsize=(14, 3.8))
    rate_col = [BLUE, ORANGE, YELLOW, GREEN, RED, PURPLE, CYAN]
    for k, rate in enumerate(rates):
        e = np.sort([r["e2e_ms"] for r in data["core"] if r["rate"] == rate and r.get("status") == 201])
        if len(e):
            ax[0].plot(e, np.arange(1, len(e) + 1) / len(e), color=rate_col[k % len(rate_col)], label=f"{rate:g} req/s")
    ax[0].set_xscale("log")
    ax[0].set_xlabel("AF-observed decision latency (ms)")
    ax[0].set_ylabel("CDF")
    ax[0].set_title("NEF + real 5G core (Open5GS)")
    ax[0].legend()
    parts = [("price_ms", "pricing lookup"), ("admission_ms", "admission"), ("bsf_ms", "BSF discovery"),
             ("pcf_ms", "PCF (N5) create"), ("enf_ms", "user-plane class")]
    bottom = np.zeros(len(rates))
    pcol = [BLUE, ORANGE, YELLOW, GREEN, RED]
    for j, (key, lab) in enumerate(parts):
        med = np.array([np.median([r.get(key, 0.0) for r in data["core"] if r["rate"] == rate and r.get("status") == 201] or [0])
                        for rate in rates])
        ax[1].bar(np.arange(len(rates)), med, bottom=bottom, label=lab, color=pcol[j])
        bottom += med
    for k, tot in enumerate(bottom):  # total median on top of each bar (small bars are unreadable otherwise)
        ax[1].text(k, tot, f"{tot:.0f} ms", ha="center", va="bottom", fontsize=8)
    pr = [np.median([r.get("price_ms", 0.0) + r.get("admission_ms", 0.0) for r in data["core"]
                     if r["rate"] == rate and r.get("status") == 201] or [0]) for rate in rates]
    ax[1].text(0.02, 0.62, f"pricing + admission: {max(pr):.2f} ms at most", transform=ax[1].transAxes, fontsize=8,
               style="italic")
    ax[1].set_xticks(np.arange(len(rates)))
    ax[1].set_xticklabels([f"{r:g}" for r in rates])
    ax[1].set_xlabel("request rate (req/s)")
    ax[1].set_ylabel("median time (ms)")
    ax[1].set_title("Where the time goes (median)")
    ax[1].legend()
    for lab, mk, cc in (("core", "o", BLUE), ("none", "s", RED)):
        if not data[lab]:
            continue
        p50 = [np.percentile([r["e2e_ms"] for r in data[lab] if r["rate"] == rate and r.get("status") == 201] or [np.nan], 50) for rate in rates]
        p95 = [np.percentile([r["e2e_ms"] for r in data[lab] if r["rate"] == rate and r.get("status") == 201] or [np.nan], 95) for rate in rates]
        ax[2].plot(rates, p50, marker=mk, color=cc, label=f"{'NEF + core' if lab == 'core' else 'NEF alone'} p50")
        ax[2].plot(rates, p95, marker=mk, ls="--", color=cc, label=f"{'NEF + core' if lab == 'core' else 'NEF alone'} p95")
    ax[2].set_xscale("log")
    ax[2].set_yscale("log")
    ax[2].set_xlabel("request rate (req/s)")
    ax[2].set_ylabel("latency (ms)")
    ax[2].set_title("Cost of the real core signalling")
    ax[2].legend()
    save(fig, "figT1_latency")
    rows = []
    for lab in ("core", "none"):
        for rate in rates:
            e = [r["e2e_ms"] for r in data[lab] if r["rate"] == rate and r.get("status") == 201]
            if e:
                rows.append(f"| {lab} | {rate:g} | {len(e)} | {np.percentile(e, 50):.2f} | {np.percentile(e, 95):.2f} | {np.percentile(e, 99):.2f} |")
    with open(os.path.join(FIG, "tableT_latency.md"), "w") as f:
        f.write("| southbound | rate (req/s) | n | p50 (ms) | p95 (ms) | p99 (ms) |\n|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n")


# ------------------------------------------------------------------------ QoE
def load_runs(run_dirs):
    """Every record is tagged with its run: two runs can reuse the same (seed, slot, session index)
    (e.g. 'general' and 'surge' both play slots 12 and 20), so pairing MUST include the run.
    v1 paired on (seed, slot, sid) only: a policy of one run was compared with the no-NEF baseline of
    the other run (A/A artefact of +0.36 MOS at slots 12/20)."""
    sess, slots, reqs = [], [], []
    for d in run_dirs:
        tag = os.path.basename(os.path.normpath(d))
        for lst, fn in ((sess, "sessions.jsonl"), (slots, "slots.jsonl"), (reqs, "requests.jsonl")):
            for r in jl(os.path.join(d, fn)):
                r["run"] = tag
                lst.append(r)
    return sess, slots, reqs


def paired(sess, rationing):
    """{policy: list of (record, baseline record)} paired on (run, seed, slot, session index)."""
    base = {(s["run"], s["seed"], s["slot"], s["sid"]): s for s in sess if s["policy"] == "no_nef"}
    out = collections.defaultdict(list)
    for s in sess:
        if s["policy"] == "no_nef" or s["rationing"] != rationing:
            continue
        b = base.get((s["run"], s["seed"], s["slot"], s["sid"]))
        if b and s["qoe"].get("mos") is not None and b["qoe"].get("mos") is not None:
            out[s["policy"]].append((s, b))
    return out


def by_group(pairs, key):
    """Average of key over the sessions of each (seed, slot) group, list of group means."""
    g = collections.defaultdict(list)
    for s, b in pairs:
        v = key(s, b)
        if v is not None:
            g[(s["run"], s["seed"], s["slot"])].append(v)
    return [np.mean(v) for v in g.values() if v]


def fig_qoe(sess, slots, reqs, rationing, peak_only, tag=""):
    tp = peak_slot()
    if peak_only:
        keep = {tp - 1, tp, tp + 1}
        sess = [s for s in sess if s["slot"] in keep]
    pr = paired(sess, rationing)
    pols = [p for p in LAB if p in pr] + sorted(p for p in pr if p not in LAB)
    if not pols:
        print("  (no paired QoE data: run no_nef and at least one policy with the same seeds)")
        return
    suffix = f"_{rationing}" + ("_peak" if peak_only else "_all") + (f"_{tag}" if tag else "")
    # T2: per class
    fig, ax = plt.subplots(figsize=(10, 3.8))
    w = 0.8 / len(pols)
    for k, pol in enumerate(pols):
        mus, hs = [], []
        for c in CLASSES:
            mu, h = ci95(by_group([pq for pq in pr[pol] if pq[0]["class"] == c],
                                  lambda s, b: s["qoe"]["mos"] - b["qoe"]["mos"]))
            mus.append(mu)
            hs.append(h)
        ax.bar(np.arange(len(CLASSES)) + k * w - 0.4 + w / 2, mus, w, yerr=hs, capsize=2, color=COL.get(pol, '#333333'), label=LAB.get(pol, pol))
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xticks(np.arange(len(CLASSES)))
    ax.set_xticklabels(CLASSES)
    ax.set_ylabel("measured Δ MOS vs no NEF")
    ax.set_title(f"Measured on the real core ({'peak hours' if peak_only else 'all slots'}, {rationing} admission, 95% CI)")
    ax.legend(ncol=4, fontsize=7)
    save(fig, "figT2_qoe_by_class" + suffix)
    # T3: who gains
    fig, ax = plt.subplots(1, 3, figsize=(13, 3.8))
    xs = np.arange(len(pols))
    rows = []
    for pol in pols:
        allm = ci95(by_group(pr[pol], lambda s, b: s["qoe"]["mos"] - b["qoe"]["mos"]))
        buy = ci95(by_group(pr[pol], lambda s, b: s["qoe"]["mos"] - b["qoe"]["mos"] if s["granted"] else None))
        nbuy = ci95(by_group(pr[pol], lambda s, b: s["qoe"]["mos"] - b["qoe"]["mos"] if not s["granted"] else None))
        dis = ci95(by_group(pr[pol], lambda s, b: 100.0 * ((s["qoe"]["mos"] < 3.0) - (b["qoe"]["mos"] < 3.0))))
        p5 = ci95([np.percentile([s["qoe"]["mos"] for s, _ in grp], 5) - np.percentile([b["qoe"]["mos"] for _, b in grp], 5)
                   for grp in _groups(pr[pol]).values()])
        rq = [r for r in reqs if r["policy"] == pol and r["rationing"] == rationing and (not peak_only or r["slot"] in {tp - 1, tp, tp + 1})]
        rej = 100.0 * np.mean([r["status"] != 201 for r in rq]) if rq else 0.0
        sl = [s for s in slots if s["policy"] == pol and s["rationing"] == rationing and (not peak_only or s["slot"] in {tp - 1, tp, tp + 1})]
        rows.append((pol, allm, buy, nbuy, dis, p5, rej, len(rq), _revenue(sl, sess, pol, rationing)))
    for j, (idx, title) in enumerate([(2, "Granted sessions: Δ MOS"), (3, "All other sessions: Δ MOS"),
                                      (4, "Δ dissatisfied (MOS<3), pts")]):
        ax[j].bar(xs, [r[idx][0] for r in rows], yerr=[r[idx][1] for r in rows], color=[COL.get(p, '#333333') for p in pols], capsize=3)
        ax[j].axhline(0, color="k", lw=0.8)
        ax[j].set_xticks(xs)
        ax[j].set_xticklabels([LAB.get(p, p) for p in pols], rotation=30, ha="right")
        ax[j].set_title(title)
    fig.suptitle(f"Measured on the real core ({rationing} admission, {'peak hours' if peak_only else 'all slots'}, 95% CI)", y=1.03)
    save(fig, "figT3_who_gains" + suffix)
    # scoreboard
    hdr = "| policy | Δ MOS all | granted: Δ MOS | others: Δ MOS | Δ dissatisfied (pts) | Δ P5 MOS | rejected requests | requests | revenue (cell eq.) |"
    lines = [hdr, "|" + "---|" * 9]
    for pol, allm, buy, nbuy, dis, p5, rej, nrq, rev in rows:
        f = lambda t: f"{t[0]:+.3f} ± {t[1]:.3f}" if np.isfinite(t[0]) else "–"
        lines.append(f"| {LAB.get(pol, pol)} | {f(allm)} | {f(buy)} | {f(nbuy)} | {dis[0]:+.2f} ± {dis[1]:.2f} | {f(p5)} | "
                     f"{rej:.1f}% | {nrq} | {rev:.2f} |")
    with open(os.path.join(FIG, f"tableT_scoreboard{suffix}.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))


def _groups(pairs):
    g = collections.defaultdict(list)
    for s, b in pairs:
        g[(s["run"], s["seed"], s["slot"])].append((s, b))
    return g


def _revenue(slot_rows, sess, pol, rationing):
    """Revenue of the cell equivalent: sum over granted sessions of price x b_model x k, averaged per seed."""
    cfg = load_cfg()
    prof = profile_table(cfg)
    k = cfg["scaling"]["sample_k"]
    ref_of = {v["class"]: r for r, v in prof.items()}
    tot = collections.defaultdict(float)
    price = {(s["run"], s["seed"], s["slot"]): s["prices"] for s in slot_rows}
    for s in sess:
        if s["policy"] != pol or s["rationing"] != rationing or not s["granted"]:
            continue
        p = price.get((s["run"], s["seed"], s["slot"]), {}).get(ref_of[s["class"]])
        if p is not None:
            tot[(s["run"], s["seed"])] += p * prof[ref_of[s["class"]]]["b_model"] * k
    return float(np.mean(list(tot.values()))) if tot else 0.0


def fig_timeline(sess, slots, rationing, tag=""):
    pols = [p for p in ("no_nef", "safe_mean", "safe_strict", "safe_gated_mean", "quantile", "revenue_max") if any(s["policy"] == p for s in slots)]
    if not pols:
        return
    fig, ax = plt.subplots(1, 3, figsize=(14, 3.6))
    for pol in pols:
        rows = [s for s in slots if s["policy"] == pol and s["rationing"] == rationing]
        ts = sorted({r["slot"] for r in rows})
        price = [np.mean([(r["prices"].get("QOD_VIDEO") if r["prices"].get("QOD_VIDEO") is not None else np.nan) for r in rows if r["slot"] == t]) for t in ts]
        gr = [np.mean([r["n_granted"] for r in rows if r["slot"] == t]) for t in ts]
        ms = [np.mean([r["mos_mean"] for r in rows if r["slot"] == t and r["mos_mean"] is not None]) for t in ts]
        ax[0].plot(ts, price, marker="o", color=COL.get(pol, '#333333'), label=LAB.get(pol, pol))
        ax[1].plot(ts, gr, marker="o", color=COL.get(pol, '#333333'), label=LAB.get(pol, pol))
        ax[2].plot(ts, ms, marker="o", color=COL.get(pol, '#333333'), label=LAB.get(pol, pol))
    ax[0].set_title("Posted price, video profile (gap = closed)")
    ax[1].set_title("Granted QoS sessions")
    ax[2].set_title("Measured mean MOS (all sessions)")
    for a in ax:
        a.set_xlabel("hour of day (slot)")
    ax[2].legend()
    save(fig, f"figT4_timeline_{rationing}" + (f"_{tag}" if tag else ""))


REGIMES = [(0.0, 0.4, "low load\n(L0/R<0.4)"), (0.4, 0.65, "medium\n(0.4-0.65)"),
           (0.65, 0.85, "high\n(0.65-0.85)"), (0.85, 9.9, "saturated\n(>0.85)")]


def load_ratio(slot, x):
    """Best-effort load of the slot relative to capacity, L0/R = base_load(t) * x / R (model units)."""
    m = model_bundle()[2][int(slot)]
    return m.base_load() * float(x) / m.R


def fig_regimes(sess, rationing):
    """Generalised view: every (seed, slot) group is placed by its congestion level, not by its hour."""
    pr = paired(sess, rationing)
    pols = [p for p in LAB if p in pr] + sorted(p for p in pr if p not in LAB)
    if not pols:
        return
    fig, ax = plt.subplots(1, 3, figsize=(14, 3.8))
    w = 0.8 / len(pols)
    rows = []
    for k, pol in enumerate(pols):
        groups = _groups(pr[pol])
        for r_, (lo, hi, lab) in enumerate(REGIMES):
            sel = {g: v for g, v in groups.items() if lo <= load_ratio(g[2], v[0][0]["x"]) < hi}
            if not sel:
                continue
            allm = ci95([np.mean([s["qoe"]["mos"] - b["qoe"]["mos"] for s, b in v]) for v in sel.values()])
            buy = ci95([np.mean([s["qoe"]["mos"] - b["qoe"]["mos"] for s, b in v if s["granted"]])
                        for v in sel.values() if any(s["granted"] for s, _ in v)])
            oth = ci95([np.mean([s["qoe"]["mos"] - b["qoe"]["mos"] for s, b in v if not s["granted"]]) for v in sel.values()])
            x0 = r_ + k * w - 0.4 + w / 2
            n = len(sel)
            for j, val in enumerate((buy, oth, allm)):
                if np.isfinite(val[0]):
                    # n < 2 groups: no confidence interval -> hatched bar + "n=1" so it is not read as certain
                    ax[j].bar(x0, val[0], w, yerr=val[1] if n >= 2 else None, capsize=2, color=COL.get(pol, "#333333"),
                              hatch="///" if n < 2 else None)
                    if n < 2:
                        ax[j].annotate(f"n={n}", (x0, val[0]), xytext=(0, 3 if val[0] >= 0 else -3), textcoords="offset points",
                                       ha="center", va="bottom" if val[0] >= 0 else "top", fontsize=6.5, rotation=90)
            fm = lambda t: f"{t[0]:+.3f} ± {t[1]:.3f}" if np.isfinite(t[0]) else "–"
            rows.append(f"| {LAB.get(pol, pol)} | {lab.split(chr(10))[0]} | {len(sel)} | {fm(allm)} | {fm(buy)} | {fm(oth)} |")
    for j, t in enumerate(("Granted sessions: Δ MOS", "All other sessions: Δ MOS", "All sessions: Δ MOS")):
        ax[j].set_xticks(range(len(REGIMES)))
        ax[j].set_xticklabels([r[2] for r in REGIMES])
        ax[j].axhline(0, color="k", lw=0.8)
        ax[j].set_title(t)
    from matplotlib.patches import Patch
    fig.legend(handles=[Patch(color=COL.get(p, "#333333"), label=LAB.get(p, p)) for p in pols], loc="lower center",
               ncol=len(pols), bbox_to_anchor=(0.5, -0.1), fontsize=8)
    fig.suptitle(f"Results by congestion regime (measured on the real core, {rationing} admission, 95% CI; "
                 f"hatched = 1 group, no CI)", y=1.02)
    save(fig, f"figT6_regimes_{rationing}")
    with open(os.path.join(FIG, f"tableT_regimes_{rationing}.md"), "w") as fh:
        fh.write("| policy | regime | groups | Δ MOS all | granted | others |\n|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n")
    print("\n".join(rows))


def fig_externality():
    pts = jl(rpath("results", "testbed", "externality", "points.jsonl"))
    if not pts:
        print("  (no externality data)")
        return
    P, cal, _ = model_bundle()
    R = P["capacity_R"]
    tbp = rpath("results", "calibration_testbed.json")
    tb = json.load(open(tbp)) if os.path.exists(tbp) else None
    fig, ax = plt.subplots(1, 4, figsize=(14, 3.3), sharey=False)
    for j, c in enumerate(CLASSES):
        rows = [p for p in pts if p["class"] == c and p["D"] is not None]
        ax[j].scatter([p["L_over_R"] for p in rows], [p["D"] for p in rows], s=16, color=BLUE, zorder=3,
                      label="measured (testbed)")
        cc = next(k for k in cal["classes"] if k["name"] == c)
        ax[j].plot(np.asarray(cc["curve_L"]) / R, cc["curve_D"], color=RED, lw=1.4, label="analytical model (ITU)")
        Lg = np.linspace(0.3, 1.3, 60)
        if tb:
            ct = next(k for k in tb["classes"] if k["name"] == c)
            ax[j].plot(Lg, np.maximum(ct["delta"] + ct["kappa"] * Lg, ct.get("floor", 0.0)), "--", color=GREEN, lw=1.6,
                       label="testbed fit")
        ax[j].axhline(0, color="k", lw=0.6)
        ax[j].set_title(c)
        ax[j].set_xlabel("load L / R")
    ax[0].set_ylabel("D(L) = MOS guaranteed − MOS best effort")
    ax[0].legend(fontsize=7)
    save(fig, "figT5_externality")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", nargs="*", default=[])
    ap.add_argument("--latency", action="store_true")
    ap.add_argument("--externality", action="store_true")
    a = ap.parse_args()
    if a.latency:
        fig_latency()
    if a.externality:
        fig_externality()
    if a.run:
        sess, slots, reqs = load_runs(a.run)
        for rationing in sorted({s["rationing"] for s in sess}):
            fig_regimes(sess, rationing)                     # all runs together, each group placed by its load
            for d in a.run:
                tag = os.path.basename(os.path.normpath(d))
                S = [r for r in sess if r["run"] == tag]
                L = [r for r in slots if r["run"] == tag]
                Q = [r for r in reqs if r["run"] == tag]
                if not any(r["rationing"] == rationing for r in S):
                    continue
                print(f"--- run {tag}")
                fig_qoe(S, L, Q, rationing, peak_only=True, tag=tag)
                fig_qoe(S, L, Q, rationing, peak_only=False, tag=tag)
                fig_timeline(S, L, rationing, tag=tag)


if __name__ == "__main__":
    main()
