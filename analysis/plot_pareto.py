"""Pareto fronts of the QoD pricing policies (simulation Q1, peak hours) -- plotting only, runs in seconds.

One IEEE double-column figure, three trade-offs that matter for a QoD price:
  (a) operator profit            vs  Δ mean MOS (vs no NEF)   -> revenue vs average QoE
  (b) operator profit            vs  5th-percentile MOS       -> revenue vs worst-served users (dashed = no NEF)
  (c) harm to best-effort users  vs  social welfare gain      -> externality vs efficiency
Black staircase = Pareto front (non-dominated policies); grey area = region it dominates;
dotted grey arrow = "is dominated by"; error bars = 95 % CI over seeds (from the CSV).
A table gives the Pareto status of every policy per trade-off and for all 5 objectives together
(profit↑, ΔMOS↑, P5↑, welfare↑, harm↓); a dominance is flagged "n.s." if it does not survive the CIs.

Usage (inside the core container, or on the host if numpy + matplotlib are installed):
  python3 analysis/plot_pareto.py                       # random admission (default)
  python3 analysis/plot_pareto.py --rationing priority
  python3 analysis/plot_pareto.py --rationing both      # random (filled) + priority (hollow, dashed front)
Inputs : results/sim/q1_who_gains.csv   (fallback: results/sim/q1_key_numbers.json, means only)
         results/sim/q1_shift.csv       (optional -> φ front, figure P2)
Outputs: results/sim/figP1_pareto_<rationing>.(png|pdf), results/sim/figP2_shift_front.(png|pdf),
         results/sim/tableP_pareto_<rationing>.(md|csv)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

try:  # same style and palette as the other figures of the lab
    from analysis.make_figures import COL, LAB  # noqa: E402,F401  (also sets the IEEE rcParams)
except Exception:  # standalone fallback (vivid IEEE palette)
    COL = {"no_nef": "#0060C0", "safe_mean": "#1FA02C", "safe_strict": "#F2C200", "safe_gated_mean": "#FF7F00",
           "safe_gated": "#C05A00", "safe_mean_eta": "#8E3FBF", "quantile": "#00A9D6", "congestion_noext": "#E31A1C",
           "flat_rev": "#A0522D", "revenue_max": "#9E0000"}
    LAB = {"no_nef": "No NEF", "safe_mean": "QoE-safe (mean)", "safe_strict": "QoE-safe (strict)",
           "safe_gated_mean": "QoE-safe + gating", "safe_gated": "QoE-safe strict + gating",
           "safe_mean_eta": "QoE-safe (η=0.01)", "quantile": "Conformal quantile",
           "congestion_noext": "Congestion w/o externality", "flat_rev": "Flat commercial price",
           "revenue_max": "Revenue max."}
    plt.rcParams.update({"font.family": "serif", "font.serif": ["STIXGeneral", "DejaVu Serif"],
                         "mathtext.fontset": "stix", "axes.spines.top": False, "axes.spines.right": False})
plt.rcParams.update({"savefig.dpi": 300, "font.size": 8, "axes.titlesize": 8.5, "axes.labelsize": 8,
                     "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 7, "axes.grid": True,
                     "grid.alpha": 0.25, "grid.linestyle": ":"})

SIM = os.path.join(ROOT, "results", "sim")
KEYS = ("dmos", "buyers_uplift", "buyers_share", "nonbuyers_dmos", "dissat", "dissat_base", "p5", "p5_base",
        "profit", "welfare", "reject")
SHOW = ["safe_mean", "safe_gated_mean", "quantile", "congestion_noext", "flat_rev", "revenue_max"]
ALIASES = ["safe_strict", "safe_mean_eta", "safe_gated"]  # merged into a shown point if numerically identical
MARK = {"safe_mean": "o", "safe_strict": "o", "safe_mean_eta": "h", "safe_gated_mean": "D", "safe_gated": "d",
        "quantile": "^", "congestion_noext": "v", "flat_rev": "s", "revenue_max": "P"}
SHORT = {"safe_strict": "strict", "safe_mean_eta": "η=0.01", "safe_gated": "strict + gating"}
TAG = {"safe_mean": "QoE-safe", "safe_strict": "strict", "safe_mean_eta": "η", "safe_gated_mean": "gating",
       "safe_gated": "strict+gating", "quantile": "quantile", "congestion_noext": "congestion", "flat_rev": "flat",
       "revenue_max": "rev-max"}
# objective -> (label, sense: +1 maximise / -1 minimise, scale for display)
OBJ = {"profit": ("Operator profit (peak)", +1, 1.0), "dmos": ("Δ mean MOS vs no NEF", +1, 1.0),
       "p5": ("5th-percentile MOS", +1, 1.0), "welfare": ("Social welfare gain (peak)", +1, 1.0),
       "harm": ("Harm to best-effort users ($10^{-3}$ MOS)", -1, 1e3)}
PANELS = [("profit", "dmos", "(a) Revenue vs average QoE"),
          ("profit", "p5", "(b) Revenue vs worst-served users"),
          ("harm", "welfare", "(c) Efficiency vs externality")]


# ----------------------------------------------------------------------------- data
def load(csv_path, json_path):
    rows = {}
    if os.path.exists(csv_path):
        with open(csv_path) as f:
            rd = csv.reader(f)
            next(rd)
            for r in rd:
                v = list(map(float, r[2:]))
                rows[(r[0], r[1])] = {k: (v[2 * i], v[2 * i + 1]) for i, k in enumerate(KEYS)}
        src = csv_path
    elif os.path.exists(json_path):
        d = json.load(open(json_path))["who_gains"]
        rows = {(rat, p): {k: (float(v[k]), 0.0) for k in KEYS} for rat, dd in d.items() for p, v in dd.items()}
        src = json_path + " (means only, no CI)"
    else:
        sys.exit(f"missing {csv_path} (run sim/exp_q1_quality.py first)")
    for r in rows.values():  # harm to the non-buyers = minus their MOS change
        r["harm"] = (-r["nonbuyers_dmos"][0], r["nonbuyers_dmos"][1])
    print(f"[pareto] data: {src}")
    return rows


def policy_set(rows, rat, wanted):
    """Shown policies + aliases folded into identical points (e.g. strict == mean at the calibrated prices)."""
    pols = [p for p in wanted if (rat, p) in rows]
    label = {p: LAB.get(p, p) for p in pols}
    alias = {p: [] for p in pols}
    for a in ALIASES:
        if (rat, a) not in rows or a in pols:
            continue
        twin = next((p for p in pols if all(np.isclose(rows[(rat, a)][k][0], rows[(rat, p)][k][0], rtol=1e-9, atol=1e-12)
                                            for k in KEYS)), None)
        if twin:
            alias[twin].append(SHORT.get(a, a))
            print(f"[pareto] {rat}: {a} coincides with {twin} -> merged")
        else:
            pols.append(a)
            label[a] = LAB.get(a, a)
    for p, al in alias.items():
        if al:
            label[p] += " [≡ " + ", ".join(al) + "]"
    return pols, label


def arrays(rows, rat, pols, keys):
    M = np.array([[rows[(rat, p)][k][0] for k in keys] for p in pols])
    H = np.array([[rows[(rat, p)][k][1] for k in keys] for p in pols])
    S = np.array([OBJ[k][1] for k in keys], float)
    return M, H, S


# ----------------------------------------------------------------------------- Pareto
def dominators(M, S, H=None):
    """For each point i: list of (j, robust) with j dominating i (all objectives >=, one >, in the max sense).
    robust = dominance still holds with the dominator at its worst CI bound and i at its best."""
    Q = M * S
    H = np.zeros_like(M) if H is None else H
    out = []
    for i in range(len(Q)):
        d = []
        for j in range(len(Q)):
            if j != i and np.all(Q[j] >= Q[i]) and np.any(Q[j] > Q[i]):
                d.append((j, bool(np.all(Q[j] - H[j] >= Q[i] + H[i]))))
        out.append(d)
    return out


def first_dom(doms):
    """Dominator shown by the arrow: the first in the policy order (the proposed QoE-safe rule comes first)."""
    return min(doms, key=lambda jr: jr[0])


def draw_front(ax, x, y, sx, sy, front, ls="-", fill=True):
    """Attainment staircase of the non-dominated set + shaded dominated region (both senses supported)."""
    xs, ys = x[front] * sx, y[front] * sy  # transformed: maximise both
    o = np.lexsort((-ys, xs))
    xs, ys = xs[o], ys[o]
    (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
    xw, yw = min(x0 * sx, x1 * sx), min(y0 * sy, y1 * sy)  # worst corner
    bx, by = [xw], [ys[0]]
    for k in range(len(xs)):
        bx += [xs[k], xs[k]]
        by += [ys[k], ys[k + 1] if k + 1 < len(xs) else yw]
    bx, by = np.array(bx) * sx, np.array(by) * sy
    if fill:
        ax.fill(np.r_[bx, xw * sx], np.r_[by, yw * sy], color="0.88", lw=0, zorder=0)
    ax.plot(bx, by, color="black", lw=1.3, ls=ls, zorder=2, solid_joinstyle="miter")
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)


def set_limits(ax, x, y, hx, hy, extra_y=()):
    lo_x, hi_x = np.min(x - hx), np.max(x + hx)
    lo_y, hi_y = min(np.min(y - hy), *extra_y) if extra_y else np.min(y - hy), np.max(y + hy)
    px, py = 0.10 * (hi_x - lo_x or 1), 0.14 * (hi_y - lo_y or 1)
    ax.set_xlim(lo_x - px, hi_x + px)
    ax.set_ylim(lo_y - py, hi_y + py)


def place_box(ax, txt, obstacles, avoid):
    """Put the key-number box in the corner where it covers the fewest points/labels (never the 'ideal' corner)."""
    r = ax.figure.canvas.get_renderer()
    pts = (ax.transData + ax.transAxes.inverted()).transform(np.asarray(obstacles, float))
    corners = {"ll": (0.03, 0.04, "left", "bottom"), "lr": (0.97, 0.04, "right", "bottom"),
               "ul": (0.03, 0.96, "left", "top"), "ur": (0.97, 0.96, "right", "top"),
               "ml": (0.03, 0.40, "left", "bottom"), "mr": (0.97, 0.40, "right", "bottom")}
    best, nb = None, 1e9
    for name, (x, y, ha, va) in corners.items():
        if name == avoid:
            continue
        t = ax.text(x, y, txt, transform=ax.transAxes, ha=ha, va=va, fontsize=6.3)
        bb = t.get_window_extent(r).transformed(ax.transAxes.inverted())
        t.remove()
        m = 0.05
        n = np.sum((pts[:, 0] > bb.x0 - m) & (pts[:, 0] < bb.x1 + m) & (pts[:, 1] > bb.y0 - m) & (pts[:, 1] < bb.y1 + m))
        if n < nb:
            best, nb = name, n
    x, y, ha, va = corners[best]
    ax.text(x, y, txt, transform=ax.transAxes, ha=ha, va=va, fontsize=6.3, zorder=5,
            bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="0.3", lw=0.6))


def key_text(rows, rat, panel):
    g = lambda p, k: rows[(rat, p)][k][0] if (rat, p) in rows else None  # noqa: E731
    try:
        if panel == 0:
            a, b = g("safe_mean", "dmos"), g("revenue_max", "dmos")
            pa, pb = g("safe_mean", "profit"), g("revenue_max", "profit")
            return f"QoE-safe vs Revenue max.:\nΔMOS ×{a / b:.2f}, profit ×{pa / pb:.2f}"
        if panel == 1:
            return f"P5: QoE-safe {g('safe_mean', 'p5'):.2f}\nRevenue max. {g('revenue_max', 'p5'):.2f}"
        if panel == 2:
            h = g("congestion_noext", "harm") / g("safe_mean", "harm")
            w = 100 * (g("congestion_noext", "welfare") / g("safe_mean", "welfare") - 1)
            return f"Congestion w/o ext.\nvs QoE-safe: harm ×{h:.1f},\nwelfare {w:+.0f} %".replace("-", "−")
    except (TypeError, ZeroDivisionError):
        return None


# ----------------------------------------------------------------------------- figure P1
def fig_pareto(rows, rats, wanted, arrows, out):
    fig, axs = plt.subplots(1, 3, figsize=(7.16, 2.55))
    fig.subplots_adjust(wspace=0.38)
    handles, seen = [], set()
    pols_all, label_all = {}, {}
    for rat in rats:
        pols_all[rat], label_all[rat] = policy_set(rows, rat, wanted)
    for k, (kx, ky, title) in enumerate(PANELS):
        ax = axs[k]
        (lx, sx, cx), (ly, sy, cy) = OBJ[kx], OBJ[ky]
        allx, ally, allhx, allhy = [], [], [], []
        for rat in rats:
            M, H, _ = arrays(rows, rat, pols_all[rat], [kx, ky])
            allx += list(M[:, 0] * cx); ally += list(M[:, 1] * cy)
            allhx += list(H[:, 0] * cx); allhy += list(H[:, 1] * cy)
        base = [rows[(rats[0], "safe_mean")]["p5_base"][0]] if ky == "p5" and (rats[0], "safe_mean") in rows else []
        set_limits(ax, np.array(allx), np.array(ally), np.array(allhx), np.array(allhy), extra_y=base)
        for r_, rat in enumerate(rats):
            pols = pols_all[rat]
            M, H, S = arrays(rows, rat, pols, [kx, ky])
            x, y, hx, hy = M[:, 0] * cx, M[:, 1] * cy, H[:, 0] * cx, H[:, 1] * cy
            dom = dominators(M, S, H)
            front = [i for i in range(len(pols)) if not dom[i]]
            draw_front(ax, x, y, sx, sy, front, ls="-" if r_ == 0 else "--", fill=(r_ == 0))
            hollow = rat == "priority" and len(rats) == 2
            for i, p in enumerate(pols):
                c = COL.get(p, "black")
                on = not dom[i]
                ax.errorbar(x[i], y[i], xerr=hx[i] or None, yerr=hy[i] or None, fmt="none", ecolor=c, elinewidth=0.9,
                            capsize=2, zorder=3)
                ax.scatter(x[i], y[i], s=58 if on else 40, marker=MARK.get(p, "o"),
                           facecolor="white" if hollow else c, edgecolor=c if hollow else "black",
                           linewidth=1.4 if hollow else (1.0 if on else 0.6), zorder=4)
                if arrows and len(rats) == 1 and dom[i]:
                    j, robust = first_dom(dom[i])
                    ax.annotate("", xy=(x[j], y[j]), xytext=(x[i], y[i]), zorder=3,
                                arrowprops=dict(arrowstyle="-|>", color="0.35", lw=0.8, ls=":" if robust else (0, (1, 3)),
                                                shrinkA=5, shrinkB=6, mutation_scale=7))
                if p not in seen:
                    seen.add(p)
                    handles.append(Line2D([], [], ls="none", marker=MARK.get(p, "o"), markersize=6, markerfacecolor=c,
                                          markeredgecolor="black", markeredgewidth=0.7, label=label_all[rat][p]))
            if len(rats) == 2 and r_ == 1:  # random -> priority shift of each policy
                M0, _, _ = arrays(rows, rats[0], pols, [kx, ky])
                for i, p in enumerate(pols):
                    ax.plot([M0[i, 0] * cx, x[i]], [M0[i, 1] * cy, y[i]], color=COL.get(p, "k"), lw=0.7, zorder=2)
        obst = list(zip(allx, ally))
        if base:
            ax.axhline(base[0], color=COL["no_nef"], ls="--", lw=1.2, zorder=1)
            xl = ax.get_xlim()
            ax.text(xl[0] + 0.5 * (xl[1] - xl[0]), base[0], f"no NEF ({base[0]:.2f})", color=COL["no_nef"],
                    ha="center", va="bottom", fontsize=6.5)
            obst += [(xl[0] + f * (xl[1] - xl[0]), base[0]) for f in (0.35, 0.5, 0.65)]
        ideal = ("u" if sy > 0 else "l") + ("r" if sx > 0 else "l")
        ix, iy = (0.97 if sx > 0 else 0.03), (0.96 if sy > 0 else 0.04)
        ax.text(ix, iy, ("better " if sx > 0 else "") + ("↗" if sx > 0 else "↖") + ("" if sx > 0 else " better"),
                transform=ax.transAxes, ha="right" if sx > 0 else "left", va="top" if sy > 0 else "bottom",
                fontsize=7, fontweight="bold", color="0.25")
        txt = key_text(rows, rats[0], k)
        if txt:
            place_box(ax, txt, obst, ideal)
        ax.set_xlabel(lx)
        ax.set_ylabel(ly)
        ax.set_title(title)
    extra = [Line2D([], [], color="black", lw=1.3, label="Pareto front")]
    if arrows and len(rats) == 1:
        extra.append(Line2D([], [], color="0.35", lw=0.8, ls=":", marker=">", markersize=3, label="is dominated by"))
    if len(rats) == 2:
        extra += [Line2D([], [], color="black", lw=1.3, ls="--", label="front, priority admission"),
                  Line2D([], [], ls="none", marker="o", markerfacecolor="white", markeredgecolor="black",
                         label="hollow = priority admission")]
    fig.legend(handles=handles + extra, loc="lower center", ncol=5, bbox_to_anchor=(0.5, -0.20), frameon=False,
               columnspacing=1.0, handletextpad=0.3)
    tag = "+".join(rats) if len(rats) == 2 else rats[0]
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(out, f"figP1_pareto_{'both' if len(rats) == 2 else tag}.{ext}"), bbox_inches="tight")
    plt.close(fig)
    print(f"  -> {os.path.relpath(out)}/figP1_pareto_{'both' if len(rats) == 2 else tag}.(png|pdf)")


# ----------------------------------------------------------------------------- table
def table(rows, rat, wanted, out):
    pols, label = policy_set(rows, rat, wanted)
    status = {p: [] for p in pols}
    for kx, ky, title in PANELS + [(None, None, "global")]:
        keys = [kx, ky] if kx else ["profit", "dmos", "p5", "welfare", "harm"]
        M, H, S = arrays(rows, rat, pols, keys)
        dom = dominators(M, S, H)
        for i, p in enumerate(pols):
            if not dom[i]:
                status[p].append("front")
            else:
                tags = [TAG.get(pols[j], pols[j]) + ("" if rb or not H.any() else "*") for j, rb in dom[i]]
                status[p].append("dom. by " + ", ".join(tags))
    f2 = lambda m, h, s=1.0, d=3: f"{m * s:.{d}f}" + (f" ± {h * s:.{d}f}" if h else "")  # noqa: E731
    hdr = ["Policy", "Profit", "ΔMOS", "P5 MOS", "Dissat. %", "Welfare", "Harm (1e-3)", "Granted %",
           "(a) rev–QoE", "(b) rev–tail", "(c) eff–ext", "All 5 obj."]
    lines = []
    for p in pols:
        r = rows[(rat, p)]
        lines.append([label[p], f2(*r["profit"], d=2), f2(*r["dmos"]), f2(*r["p5"], d=2), f2(*r["dissat"], d=2),
                      f2(*r["welfare"], d=2), f2(*r["harm"], s=1e3, d=1), f"{100 * r['buyers_share'][0]:.0f}"]
                     + status[p])
    b = rows.get((rat, "safe_mean"))
    note = (f"\nReference no NEF: ΔMOS 0, P5 {b['p5_base'][0]:.2f}, dissatisfied {b['dissat_base'][0]:.2f} %, harm 0 "
            f"(excluded from the fronts: it is the baseline). '*' = dominance not robust to the 95 % CI.\n") if b else ""
    md = ["| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)] + ["| " + " | ".join(l_) + " |" for l_ in lines]
    with open(os.path.join(out, f"tableP_pareto_{rat}.md"), "w") as f:
        f.write(f"Pareto status of the pricing policies — peak hours, {rat} admission\n\n" + "\n".join(md) + "\n" + note)
    with open(os.path.join(out, f"tableP_pareto_{rat}.csv"), "w", newline="") as f:
        csv.writer(f).writerows([hdr] + lines)
    print(f"  -> {os.path.relpath(out)}/tableP_pareto_{rat}.(md|csv)")
    print("\n".join(md) + note)


# ----------------------------------------------------------------------------- figure P2 (φ)
def fig_shift(path, out):
    if not os.path.exists(path):
        return
    d = np.loadtxt(path, delimiter=",", skiprows=1, ndmin=2)  # phi, dmos, ci, profit, profit_ci, ...
    phi, y, hy, x, hx = d[:, 0], d[:, 1], d[:, 2], d[:, 3], d[:, 4]
    M, H, S = np.c_[x, y], np.c_[hx, hy], np.array([1.0, 1.0])
    dom = dominators(M, S, H)
    front = [i for i in range(len(phi)) if not dom[i]]
    cols = ["#0060C0", "#1FA02C", "#FF7F00", "#E31A1C", "#8E3FBF", "#00A9D6"]
    fig, ax = plt.subplots(figsize=(3.5, 2.5))
    set_limits(ax, x, y, hx, hy)
    draw_front(ax, x, y, 1, 1, front)
    for i in range(len(phi)):
        c = cols[i % len(cols)]
        ax.errorbar(x[i], y[i], xerr=hx[i], yerr=hy[i], fmt="none", ecolor=c, elinewidth=0.9, capsize=2, zorder=3)
        ax.scatter(x[i], y[i], s=55, color=c, edgecolor="black", lw=0.8 if not dom[i] else 0.5, zorder=4)
        # label on the side away from the closest other point (no overlap with its error bars)
        sx_, sy_ = np.ptp(x) or 1.0, np.ptp(y) or 1.0
        others = [j for j in range(len(phi)) if j != i]
        jn = min(others, key=lambda j: np.hypot((x[j] - x[i]) / sx_, (y[j] - y[i]) / sy_)) if others else i
        close = np.hypot((x[jn] - x[i]) / sx_, (y[jn] - y[i]) / sy_) < 0.3
        off, ha, va = ((-6, -9), "right", "top") if close and x[jn] > x[i] else ((11, 0), "left", "center")
        ax.annotate(f"φ = {phi[i]:g}" + (" (dominated)" if dom[i] else ""), (x[i], y[i]), xytext=off, ha=ha, va=va,
                    textcoords="offset points", fontsize=7, color=c, fontweight="bold", zorder=5)
    ax.annotate("", xy=(x[-1], y[-1]), xytext=(x[0], y[0]), zorder=2,
                arrowprops=dict(arrowstyle="-|>", color="0.45", lw=0.8, ls="--", connectionstyle="arc3,rad=-0.25",
                                shrinkA=7, shrinkB=7, mutation_scale=8))
    ax.text(0.97, 0.96, "better ↗", transform=ax.transAxes, ha="right", va="top", fontsize=7, fontweight="bold",
            color="0.25")
    ax.set_xlabel("Operator profit (evening window)")
    ax.set_ylabel("Δ mean MOS vs no NEF (evening)")
    ax.set_title("QoE-safe price with time shifting φ")
    fig.text(0.5, -0.10, "φ: share of deferrable sessions leaving the peak (behavioural hypothesis).", ha="center",
             fontsize=6.5, style="italic")
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(out, f"figP2_shift_front.{ext}"), bbox_inches="tight")
    plt.close(fig)
    print(f"  -> {os.path.relpath(out)}/figP2_shift_front.(png|pdf)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rationing", choices=["random", "priority", "both"], default="random")
    ap.add_argument("--policies", nargs="+", default=SHOW)
    ap.add_argument("--no-arrows", action="store_true")
    ap.add_argument("--csv", default=os.path.join(SIM, "q1_who_gains.csv"))
    ap.add_argument("--json", default=os.path.join(SIM, "q1_key_numbers.json"))
    ap.add_argument("--shift", default=os.path.join(SIM, "q1_shift.csv"))
    ap.add_argument("--out", default=SIM)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    rows = load(a.csv, a.json)
    rats = ["random", "priority"] if a.rationing == "both" else [a.rationing]
    fig_pareto(rows, rats, a.policies, not a.no_arrows, a.out)
    for rat in rats:
        table(rows, rat, a.policies, a.out)
    fig_shift(a.shift, a.out)


if __name__ == "__main__":
    main()
