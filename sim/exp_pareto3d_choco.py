"""Exact 3-D Pareto front of an evening QoD pricing plan, computed with the Choco constraint solver.

Decision. For every hour t of the window (default: the evening hours of pricing.window_slots), the
NEF posts the price of ONE level k among K levels. The levels are the policies already pre-computed
in results/prices/prices.json (closed = no NEF, QoE-safe + gating, QoE-safe, conformal quantile,
congestion w/o externality, flat commercial, revenue max) plus one intermediate price (geometric
mean of QoE-safe and revenue-max). A plan = one level per hour -> K^T plans (8^7 = 2,097,152).

Objectives (additive over the hours -> the front is EXACT, not sampled), one per stakeholder:
  x  profit ↑  operator profit over the window                               (operator)
  y  harm   ↓  MOS lost by the best-effort (non-guaranteed) sessions,         (users who do not pay:
               per session of the window, x 1e-3 MOS                           the externality)
  z  dmos   ↑  Δ mean MOS vs no NEF, all sessions of the window              (QoE, what QoD is for)
  colour p5 :  5th-percentile MOS, session-weighted mean of the hours        (worst-served users)
  (--axes / --color accept: profit, harm, dmos, p5, dissat, welfare, reject)
Why not "% dissatisfied" on an axis: with profit and harm it only measures the externality twice
and ignores the QoE bought by the guaranteed sessions (a flat price would then "dominate" QoE-safe).

Method. ε-constraint with Choco: maximise z (default: the QoE gain) subject to x >= εx (revenue
target) and y >= εy (externality budget) on an nx x ny grid; each optimum is refined
lexicographically (then max x, then max y at that z) so that every Choco point is Pareto-optimal. The full exact front is
computed by dynamic programming with dominance pruning and, when K^T <= --bf-max, by exhaustive
enumeration: every Choco optimum is checked against both (gap must be 0). Front quality: share of
the exact hypervolume captured by the Choco points. Knee = plan closest to the ideal point after
normalisation. Optional business constraint, the reason to use a CP solver:
  --max-switch S : at most S price-level changes between consecutive hours (smooth price plans).

Stages (cached, so only the first one is slow):
  1. tables  Monte-Carlo expectation of every (hour, level) cell with the Stackelberg-Nash model,
             common random numbers across levels, parallel over cells  -> results/sim/p3d_tables.json
  2. front   DP + Choco + enumeration (seconds)                          -> p3d_front_*.csv, tableP3_*.md
  3. plots   (seconds)                        -> figP3_pareto3d_choco.(png|pdf), figP3a_pareto3d.(png|pdf),
                                                 figP3_pareto3d_choco.html (interactive, if plotly is installed)

Usage (core container; same calibration as the prices):
  pip3 install pychoco plotly        # once per container (without pychoco: exact DP front only)
  NEF_LAB_CALIBRATION=results/calibration_testbed.json python3 sim/exp_pareto3d_choco.py
  python3 sim/exp_pareto3d_choco.py --plot-only --view 25 -60      # re-plot in seconds
  python3 sim/exp_pareto3d_choco.py --max-switch 2                 # smooth plans (Choco constraint)
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
import warnings

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
warnings.filterwarnings("ignore")

try:
    from pychoco import Model as ChocoModel  # type: ignore
    HAS_CHOCO = True
except Exception:  # pragma: no cover
    HAS_CHOCO = False

# ----------------------------------------------------------------------------- definitions
FIELDS = ["profit", "welfare", "reject", "dmos", "dissat", "dissat_base", "harm", "buyers_share", "p5", "p5_base",
          "sessions"]
# metric -> (axis label, sense +1 max / -1 min, aggregation over hours, display factor, integer scale)
METRIC = {
    "profit": ("Operator profit", +1, "sum", 1.0, 1e3),
    "welfare": ("Social welfare gain", +1, "sum", 1.0, 1e3),
    "dmos": ("Δ mean MOS vs no NEF", +1, "mean", 1.0, 1e6),
    "dissat": ("Dissatisfied sessions, MOS < 3 (%)", -1, "mean", 1.0, 1e4),
    "harm": ("Best-effort MOS loss ($10^{-3}$)", -1, "mean", 1e3, 1e7),
    "reject": ("Rejected QoD requests (%)", -1, "mean", 1.0, 1e4),
    "p5": ("5th-percentile MOS (hourly)", +1, "mean", 1.0, 1e4),
}
DEFAULT_LEVELS = ["no_nef", "safe_gated_mean", "safe_mean", "quantile", "congestion_noext", "mid", "flat_rev",
                  "revenue_max"]
LEVEL_LAB = {"no_nef": "Closed (no NEF)", "safe_gated_mean": "QoE-safe + gating", "safe_mean": "QoE-safe",
             "safe_strict": "QoE-safe (strict)", "safe_gated": "QoE-safe strict + gating",
             "safe_mean_eta": "QoE-safe (η)", "quantile": "Conformal quantile",
             "congestion_noext": "Congestion w/o ext.", "mid": "Intermediate price",
             "flat_rev": "Flat commercial", "revenue_max": "Revenue max."}
LEVEL_SHORT = {"no_nef": "closed", "safe_gated_mean": "gate", "safe_mean": "safe", "safe_strict": "strict",
               "safe_gated": "s+gate", "safe_mean_eta": "η", "quantile": "quant", "congestion_noext": "cong",
               "mid": "mid", "flat_rev": "flat", "revenue_max": "rev"}
MARK = {"no_nef": "s", "safe_gated_mean": "D", "safe_mean": "o", "safe_strict": "o", "safe_gated": "d",
        "safe_mean_eta": "h", "quantile": "^", "congestion_noext": "v", "mid": "p", "flat_rev": "s",
        "revenue_max": "P"}
# vivid IEEE palette (same as analysis/make_figures.py)
BLUE, ORANGE, YELLOW, PURPLE, GREEN, CYAN, RED, GREY = ("#0060C0", "#FF7F00", "#F2C200", "#8E3FBF", "#1FA02C",
                                                        "#00A9D6", "#E31A1C", "#6E6E6E")
LEVEL_COL = {"no_nef": BLUE, "safe_gated_mean": ORANGE, "safe_mean": GREEN, "safe_strict": YELLOW,
             "safe_gated": "#C05A00", "safe_mean_eta": PURPLE, "quantile": CYAN, "congestion_noext": RED,
             "mid": PURPLE, "flat_rev": "#A0522D", "revenue_max": "#9E0000"}
DARK_TEXT = {"no_nef", "mid", "flat_rev", "revenue_max", "congestion_noext", "safe_gated", "safe_mean_eta"}


def rpath(*p):
    return os.path.join(ROOT, *p)


# ============================================================================= stage 1: tables
_CTX: dict = {}


def _cell(job):
    """Expected metrics of one (hour, level) cell. Same demand draws for every level of an hour (CRN)."""
    from nef_pricing import admission, metrics
    from nef_pricing.followers import nash
    from sim.exp_q1_quality import group_metrics
    t, k = job
    C = _CTX
    m0, pv = C["models"][t], C["pv"][(t, k)]
    acc = []
    for r in range(C["n_seeds"]):
        rng = np.random.default_rng([1000 + r, t])
        y0 = None
        for x in np.maximum(1 + C["cv"] * rng.standard_normal(C["n_draws"]), 0.05):
            m = m0.with_X(m0.X * x)
            if np.all(np.isinf(pv)):
                out = admission.Outcome(np.inf, np.zeros(m.N), np.zeros(m.N), m.base_load())
                sm = metrics.summarize(m, out, 0.0)
            else:
                y = nash(m, pv, y0=y0)
                y0 = y
                out = admission.admit(m, y, pv, C["rationing"])
                sm = metrics.summarize(m, out)
            g = group_metrics(m, out)
            harm = -g["nonbuyers_dmos"] * (1.0 - g["buyers_share"])  # per session of the hour (additive)
            acc.append([sm["profit"], sm["welfare"], 100 * sm["rejection_rate"], g["dmos"], g["dissat"],
                        g["dissat_base"], harm, g["buyers_share"], g["p5"], g["p5_base"], float(m.X.sum())])
    return t, k, np.mean(np.asarray(acc, float), axis=0).tolist()


def price_vectors(tab, rationing, levels, slots):
    """{(t, k): price per AF} from prices.json; 'mid' = geometric mean of QoE-safe and revenue-max prices."""
    from labcommon import af_class_index, class_names, model_bundle
    from sim.exp_q1_quality import class_price_vector
    P = model_bundle()[0]
    names, cidx = class_names(P), af_class_index(P)
    prof = tab["meta"]["profiles"]
    pr = tab["prices"][rationing]

    def get(pol, t):
        if pol not in pr:
            sys.exit(f"policy '{pol}' not in prices.json (has: {', '.join(pr)}) -> drop it from --levels")
        if str(t) not in pr[pol]:
            sys.exit(f"slot {t} not in prices.json (has: {', '.join(sorted(pr[pol], key=int))}) -> change --slots")
        return class_price_vector(pr[pol][str(t)], prof, names, cidx)

    out = {}
    for t in slots:
        for k, lv in enumerate(levels):
            if lv == "mid":
                a, b = get("safe_mean", t), get("revenue_max", t)
                both = np.isfinite(a) & np.isfinite(b)
                out[(t, k)] = np.where(both, np.sqrt(np.where(both, a * b, 1.0)), np.where(np.isfinite(a), b, np.inf))
            else:
                out[(t, k)] = get(lv, t)
    return out


def compute_tables(a, levels, slots, prices_path):
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor
    from labcommon import load_cfg, model_bundle
    cfg = load_cfg()
    tab = json.load(open(prices_path))
    models = model_bundle()[2]
    _CTX.update(models=models, pv=price_vectors(tab, a.rationing, levels, slots), n_seeds=a.seeds,
                n_draws=a.draws, cv=float(cfg["scenario"]["forecast_cv"]), rationing=a.rationing)
    jobs = [(t, k) for t in slots for k in range(len(levels))]
    T, K = len(slots), len(levels)
    res = np.zeros((len(FIELDS), T, K))
    t0 = time.time()
    n_jobs = max(1, min(a.jobs, len(jobs)))
    print(f"[tables] {T} hours x {K} levels = {len(jobs)} cells, {a.seeds * a.draws} demand draws each, "
          f"{n_jobs} worker(s) ...", flush=True)

    def put(r, done):
        t, k, v = r
        res[:, slots.index(t), k] = v
        if done % max(1, len(jobs) // 10) == 0 or done == len(jobs):
            print(f"  {done}/{len(jobs)} cells  ({time.time() - t0:.0f} s)", flush=True)

    if n_jobs > 1 and "fork" in mp.get_all_start_methods():
        with ProcessPoolExecutor(n_jobs, mp_context=mp.get_context("fork")) as ex:
            for i, r in enumerate(ex.map(_cell, jobs), 1):
                put(r, i)
    else:
        for i, j in enumerate(jobs, 1):
            put(_cell(j), i)
    return {f: res[i].tolist() for i, f in enumerate(FIELDS)}, time.time() - t0


def file_sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()[:12]


# ============================================================================= stage 2: fronts
def contributions(tables, slots, levels):
    """Per-(hour, level) contribution of each metric to the window value (sums or session-weighted means)."""
    tb = {f: np.asarray(tables[f], float) for f in FIELDS if f in tables}
    sess = tb["sessions"][:, 0]
    w = sess / sess.sum()
    C = {}
    for m, (_, _, agg, _, _) in METRIC.items():
        C[m] = tb[m] if agg == "sum" else w[:, None] * tb[m]
    base = {"dissat": float(w @ tb["dissat_base"][:, 0]), "p5": tb["p5_base"][:, 0].tolist()}
    return C, w, base


def integer_tables(C, axes):
    """(3, T, K) integer tables in 'maximise' form + the scale used for each axis."""
    G, sc = [], []
    for m in axes:
        s = METRIC[m][4]
        T = C[m].shape[0]
        bound = T * float(np.max(np.abs(C[m]))) + 1.0
        while s * bound > 1.5e9:  # Choco IntVar = int32
            s /= 10.0
        G.append(METRIC[m][1] * np.rint(C[m] * s).astype(np.int64))
        sc.append(s)
    return np.stack(G), np.array(sc)


def nondominated(S):
    """Indices of the unique non-dominated rows of S (all objectives maximised)."""
    S = np.asarray(S)
    if len(S) == 0:
        return np.zeros(0, int)
    _, first = np.unique(S, axis=0, return_index=True)
    idx = np.sort(first)
    Q = S[idx]
    order = np.lexsort(tuple(-Q[:, j] for j in range(Q.shape[1] - 1, -1, -1)))  # lexicographic, descending
    F = np.empty_like(Q)
    keep, n = [], 0
    for i in order:  # a dominator always comes earlier in this order
        if n and np.any(np.all(F[:n] >= Q[i], axis=1)):
            continue
        F[n] = Q[i]
        n += 1
        keep.append(i)
    return idx[np.array(keep, int)]


def dp_front(G, max_switch=None):
    """Exact set of non-dominated objective vectors (+ one plan each) by DP with dominance pruning.
    With max_switch, states are grouped by (last level, switches so far): pruning stays exact."""
    _, T, K = G.shape
    groups = {(-1, 0): (np.zeros((1, G.shape[0]), np.int64), np.zeros((1, 0), np.int16))}
    for t in range(T):
        new = {}
        for (last, nsw), (S, Pl) in groups.items():
            for k in range(K):
                sw = nsw + int(max_switch is not None and last >= 0 and k != last)
                if max_switch is not None and sw > max_switch:
                    continue
                key = (k, sw) if max_switch is not None else (-1, 0)
                new.setdefault(key, []).append((S + G[:, t, k], np.c_[Pl, np.full(len(S), k, np.int16)]))
        groups = {}
        for key, lst in new.items():
            S = np.concatenate([s for s, _ in lst])
            Pl = np.concatenate([p for _, p in lst])
            keep = nondominated(S)
            groups[key] = (S[keep], Pl[keep])
    S = np.concatenate([s for s, _ in groups.values()])
    Pl = np.concatenate([p for _, p in groups.values()])
    keep = nondominated(S)
    return S[keep], Pl[keep].astype(int)


def brute_force(G, max_switch=None):
    """Objective totals of all K^T plans (validation only)."""
    _, T, K = G.shape
    tot = np.zeros((1, G.shape[0]), np.int64)
    last = np.full(1, -1, np.int16)
    nsw = np.zeros(1, np.int16)
    for t in range(T):
        tot = (tot[:, None, :] + G[:, t, :].T[None, :, :]).reshape(-1, G.shape[0])
        ks = np.tile(np.arange(K, dtype=np.int16), len(last))
        nsw = np.repeat(nsw, K) + ((np.repeat(last, K) >= 0) & (np.repeat(last, K) != ks))
        last = ks
    if max_switch is not None:
        tot = tot[nsw <= max_switch]
    return tot


def eps_grid(G, i, n):
    lo, hi = G[i].min(axis=1).sum(), G[i].max(axis=1).sum()
    return np.unique(np.ceil(np.linspace(lo, hi, n)).astype(np.int64))


def _choco_solve(G, lb, obj, max_switch, time_limit):
    """max sum_t G[obj, t, k_t]  s.t.  sum_t G[j, t, k_t] >= lb[j] (lb[j] None = free), optional smoothness.
    Returns (plan, optimum) or None."""
    _, T, K = G.shape
    m = ChocoModel("qod_plan")
    lv = [m.intvar(0, K - 1, f"k{t}") for t in range(T)]
    tot = None
    for j in range(3):
        if j != obj and lb[j] is None:
            continue
        vs = [m.intvar(int(G[j, t].min()), int(G[j, t].max()), f"o{j}_{t}") for t in range(T)]
        for t in range(T):
            m.element(vs[t], [int(v) for v in G[j, t]], lv[t]).post()
        if lb[j] is not None:
            m.sum(vs, ">=", int(lb[j])).post()
        if j == obj:
            tot = m.intvar(int(G[j].min(axis=1).sum()), int(G[j].max(axis=1).sum()), f"tot{j}")
            m.sum(vs, "=", tot).post()
    if max_switch is not None and T > 1:
        sw = [m.arithm(lv[t], "!=", lv[t + 1]).reify() for t in range(T - 1)]
        m.sum(sw, "<=", int(max_switch)).post()
    s = m.get_solver().find_optimal_solution(tot, True, time_limit=time_limit)
    if s is None:
        return None
    return [s.get_int_val(v) for v in lv], int(s.get_int_val(tot))


def choco_front(G, nx, ny, max_switch=None, time_limit="10s"):
    """ε-constraint with Choco: z* = max sum G[2] s.t. sum G[0] >= ex, sum G[1] >= ey, for every (ex, ey);
    then lexicographic refinement (max x at z >= z*, then max y at z >= z*, x >= x*) -> Pareto-optimal plans."""
    _, T, K = G.shape
    log, plans = [], []
    for ey in eps_grid(G, 1, ny):
        for ex in eps_grid(G, 0, nx):
            r = _choco_solve(G, [int(ex), int(ey), None], 2, max_switch, time_limit)
            if r is None:
                log.append((int(ex), int(ey), None))
                continue
            z = r[1]
            log.append((int(ex), int(ey), z))
            rx = _choco_solve(G, [None, int(ey), z], 0, max_switch, time_limit)
            ry = _choco_solve(G, [rx[1], None, z], 1, max_switch, time_limit) if rx else None
            plans.append((ry or rx or r)[0])
    if not plans:
        return np.zeros((0, 3), np.int64), np.zeros((0, T), int), log
    Pl = np.unique(np.array(plans, int), axis=0)
    S = np.stack([G[:, np.arange(T), p].sum(axis=1) for p in Pl])
    keep = nondominated(S)
    return S[keep], Pl[keep], log


def eps_optimum(S, ex, ey):
    ok = (S[:, 0] >= ex) & (S[:, 1] >= ey)
    return int(S[ok, 2].max()) if ok.any() else None


def hv2d(P):
    P = P[np.argsort(-P[:, 0])]
    hv, yb = 0.0, 0.0
    for x, y in P:
        if y > yb:
            hv += x * (y - yb)
            yb = y
    return hv


def hypervolume(S, lo, hi):
    """3-D hypervolume (maximisation) after normalisation to [0, 1], reference point 10 % beyond the nadir."""
    Q = (np.asarray(S, float) - lo) / np.where(hi > lo, hi - lo, 1.0) + 0.1
    Q = Q[np.argsort(-Q[:, 2])]
    hv = 0.0
    for i in range(len(Q)):
        z_next = Q[i + 1, 2] if i + 1 < len(Q) else 0.0
        if Q[i, 2] > z_next:
            hv += (Q[i, 2] - z_next) * hv2d(Q[: i + 1, :2])
    return hv


def knee_index(S):
    lo, hi = S.min(axis=0), S.max(axis=0)
    Q = (S - lo) / np.where(hi > lo, hi - lo, 1.0)
    return int(np.argmin(np.linalg.norm(1.0 - Q, axis=1)))


def plan_values(C, plan):
    T = len(plan)
    return {m: float(C[m][np.arange(T), plan].sum()) for m in METRIC}


# ============================================================================= stage 3: plots
def style():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "serif", "font.serif": ["STIXGeneral", "DejaVu Serif"], "mathtext.fontset": "stix",
                         "savefig.dpi": 300, "font.size": 8, "axes.titlesize": 8.5, "axes.labelsize": 7.5,
                         "xtick.labelsize": 6.5, "ytick.labelsize": 6.5, "legend.fontsize": 6.8,
                         "axes.spines.top": False, "axes.spines.right": False})
    return plt


def vivid_cmap():
    from matplotlib import colormaps
    from matplotlib.colors import ListedColormap
    return ListedColormap(colormaps["turbo"](np.linspace(0.08, 0.93, 256)), name="turbo_vivid")


def disp(m, v):
    return np.asarray(v, float) * METRIC[m][3]


def front_surface(ax, X, Y, Z, Cv, cmap, norm):
    """Interpolated front surface: Delaunay triangulation over (x, y), long edges removed (gaps of the front)."""
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    from scipy.spatial import Delaunay
    if len(X) < 4:
        return None
    U = np.c_[(X - X.min()) / (np.ptp(X) or 1), (Y - Y.min()) / (np.ptp(Y) or 1)]
    try:
        tri = Delaunay(U).simplices
    except Exception:
        return None
    e = np.max([np.linalg.norm(U[tri[:, i]] - U[tri[:, (i + 1) % 3]], axis=1) for i in range(3)], axis=0)
    tri = tri[e < 0.35]
    if not len(tri):
        return None
    faces = [np.c_[X[t_], Y[t_], Z[t_]] for t_ in tri]
    ea = 0.25 if len(X) < 150 else (0.10 if len(X) < 800 else 0.0)  # mesh edges only when the front is sparse
    pc = Poly3DCollection(faces, facecolors=cmap(norm(Cv[tri].mean(axis=1))), edgecolors=(0, 0, 0, ea),
                          linewidths=0.15, alpha=0.6)
    ax.add_collection3d(pc)
    return pc


def draw_3d(ax, R, a, cmap, norm, short=False):
    from matplotlib.lines import Line2D
    ax_x, ax_y, ax_z = a.axes
    E = R["exact"]
    X, Y, Z, Cv = (disp(m, E[m]) for m in (ax_x, ax_y, ax_z, a.color))
    pad = lambda v: (v.min() - 0.06 * (np.ptp(v) or 1), v.max() + 0.06 * (np.ptp(v) or 1))  # noqa: E731
    allx = np.r_[X, [disp(ax_x, v[ax_x]) for v in R["named"].values()]]
    ally = np.r_[Y, [disp(ax_y, v[ax_y]) for v in R["named"].values()]]
    allz = np.r_[Z, [disp(ax_z, v[ax_z]) for v in R["named"].values()]]
    (x0, x1), (y0, y1), (z0, z1) = pad(allx), pad(ally), pad(allz)
    front_surface(ax, X, Y, Z, Cv, cmap, norm)
    ax.scatter(X, Y, np.full_like(Z, z0), s=1.5, color="0.75", depthshade=False, lw=0)  # shadow on the floor
    ax.scatter(X, Y, Z, s=2.5 if len(X) > 300 else 7, c=cmap(norm(Cv)), lw=0, depthshade=False)
    if R.get("choco") is not None and len(R["choco"][ax_x]):
        Ch = R["choco"]
        ax.scatter(disp(ax_x, Ch[ax_x]), disp(ax_y, Ch[ax_y]), disp(ax_z, Ch[ax_z]), s=13,
                   c=cmap(norm(disp(a.color, Ch[a.color]))), edgecolor="black", linewidth=0.5, depthshade=False)
    handles = []
    for lv, v in R["named"].items():
        x, y, z = disp(ax_x, v[ax_x]), disp(ax_y, v[ax_y]), disp(ax_z, v[ax_z])
        on = v["on_front"]
        ax.plot([x, x], [y, y], [z0, z], color=LEVEL_COL[lv], lw=0.8, ls=":")
        ax.scatter([x], [y], [z], s=62, marker=MARK.get(lv, "o"), facecolor=LEVEL_COL[lv] if on else "white",
                   edgecolor="black" if on else LEVEL_COL[lv], linewidth=0.8 if on else 1.6, depthshade=False, zorder=10)
        handles.append(Line2D([], [], ls="none", marker=MARK.get(lv, "o"), markersize=6, markerfacecolor=LEVEL_COL[lv],
                              markeredgecolor="black", markeredgewidth=0.7,
                              label=f"{LEVEL_LAB.get(lv, lv)} [{LEVEL_SHORT.get(lv, lv)}]"))
    kn = R["knee"]
    kx, ky, kz = disp(ax_x, kn[ax_x]), disp(ax_y, kn[ax_y]), disp(ax_z, kn[ax_z])
    ax.plot([kx, kx], [ky, ky], [z0, kz], color="black", lw=1.0)
    ax.scatter([kx], [ky], [kz], s=190, marker="*", color=YELLOW, edgecolor="black", linewidth=1.0, depthshade=False,
               zorder=11)
    # ideal (utopia) point: best value of each objective on the front, not reachable
    ux = X.max() if METRIC[ax_x][1] > 0 else X.min()
    uy = Y.max() if METRIC[ax_y][1] > 0 else Y.min()
    uz = Z.max() if METRIC[ax_z][1] > 0 else Z.min()
    ax.scatter([ux], [uy], [uz], s=60, marker="*", facecolor="white", edgecolor="black", linewidth=0.8,
               depthshade=False, zorder=11)
    ax.text(ux, uy, uz, "  ideal", fontsize=6.5, color="black", zorder=12)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_zlim(z0, z1)
    arrow = lambda m: "↑" if METRIC[m][1] > 0 else "↓"  # noqa: E731
    SHORT = {"profit": "Profit", "harm": "BE MOS loss ($10^{-3}$)", "dmos": "Δ mean MOS", "p5": "P5 MOS",
             "dissat": "Dissatisfied (%)", "welfare": "Welfare", "reject": "Rejected (%)"}
    lab = (lambda m: SHORT[m]) if short else (lambda m: METRIC[m][0])  # noqa: E731
    ax.set_xlabel(f"{lab(ax_x)} {arrow(ax_x)}", labelpad=-2)
    ax.set_ylabel(f"{lab(ax_y)} {arrow(ax_y)}", labelpad=-2)
    ax.set_zlabel(f"{lab(ax_z)} {arrow(ax_z)}", labelpad=-3)
    ax.tick_params(axis="both", pad=-2)
    ax.view_init(*a.view)
    for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
        axis.pane.set_facecolor((1, 1, 1, 0))
        axis.pane.set_edgecolor("0.6")
        axis._axinfo["grid"].update({"color": (0.75, 0.75, 0.75, 0.6), "linestyle": ":", "linewidth": 0.5})
    from matplotlib.patches import Patch
    if "mid" in a.levels_used:
        handles.append(Patch(facecolor=LEVEL_COL["mid"], edgecolor="black", lw=0.5,
                             label=f"{LEVEL_LAB['mid']} [mid]"))
    extra = [Line2D([], [], ls="none", marker="o", markersize=6, markerfacecolor="white", markeredgecolor=GREY,
                    markeredgewidth=1.4, label="hollow = constant plan dominated"),
             Line2D([], [], ls="none", marker="o", markersize=2.5, markerfacecolor=cmap(0.55), markeredgecolor="none",
                    label=f"Exact front (DP, {len(X)} plans)"),
             Line2D([], [], ls="none", marker="*", markersize=10, markerfacecolor=YELLOW, markeredgecolor="black",
                    label="Knee (balanced plan)")]
    if R.get("choco") is not None and len(R["choco"][ax_x]):
        extra.insert(2, Line2D([], [], ls="none", marker="o", markersize=4.5, markerfacecolor=cmap(0.55),
                               markeredgecolor="black", markeredgewidth=0.6,
                               label=f"Choco ε-constraint ({len(R['choco'][ax_x])} plans)"))
    return handles + extra


def roof_plans(E, ax_x, ax_z, n_rows, knee):
    """Plans shown in panel (b): for each band of the x objective (e.g. profit), the Pareto plan with the best z
    (e.g. Δ MOS): the 'roof' of the front, i.e. the best QoE reachable at each revenue level. Knee always shown."""
    x = E[ax_x] * METRIC[ax_x][1]
    z = E[ax_z] * METRIC[ax_z][1]
    edges = np.linspace(x.min(), x.max(), n_rows)
    pick = []
    for k in range(len(edges) - 1):
        m = (x >= edges[k]) & (x <= edges[k + 1])
        if m.any():
            pick.append(int(np.flatnonzero(m)[np.argmax(z[m])]))
    pick.append(knee)
    pick = list(dict.fromkeys(pick))
    return sorted(pick, key=lambda p: x[p])


def draw_plan_map(ax, R, levels, slots, a, n_rows):
    from matplotlib.colors import ListedColormap
    E, Pl = R["exact"], R["exact_plans"]
    ax_x, ax_z = a.axes[0], a.axes[2]
    pick = roof_plans(E, ax_x, ax_z, n_rows, R["knee_idx"])
    o = np.argsort(E[ax_x] * METRIC[ax_x][1])
    M = Pl[pick]
    cm = ListedColormap([LEVEL_COL[lv] for lv in levels])
    ax.imshow(M, cmap=cm, vmin=-0.5, vmax=len(levels) - 0.5, aspect="auto", interpolation="nearest")
    for (r, c), k in np.ndenumerate(M):
        lv = levels[k]
        ax.text(c, r, LEVEL_SHORT.get(lv, lv), ha="center", va="center", fontsize=5.8,
                color="white" if lv in DARK_TEXT else "black", fontweight="bold" if pick[r] == R["knee_idx"] else None)
    ax.set_xticks(range(len(slots)))
    ax.set_xticklabels([f"{t}h" for t in slots])
    ax.set_yticks(range(len(pick)))
    f = lambda m, p: disp(m, E[m][p])  # noqa: E731
    labs = []
    for p in pick:
        s = " | ".join(f"{f(m, p) + 0.0:.2f}".replace("-0.00", "0.00") for m in a.axes)
        labs.append((r"$\bigstar$ " if p == R["knee_idx"] else "") + s)
    ax.set_yticklabels(labs, fontsize=6.2)
    for tl, p in zip(ax.get_yticklabels(), pick):
        if p == R["knee_idx"]:
            tl.set_fontweight("bold")
    ax.set_xticks(np.arange(len(slots) + 1) - 0.5, minor=True)
    ax.set_yticks(np.arange(len(pick) + 1) - 0.5, minor=True)
    ax.grid(which="minor", color="white", lw=1.2)
    ax.grid(which="major", visible=False)
    ax.tick_params(which="minor", length=0)
    for sp in ax.spines.values():
        sp.set_visible(False)
    short = {"profit": "profit", "harm": "harm", "dissat": "dissat.%", "dmos": "ΔMOS", "welfare": "welfare",
             "reject": "rej.%", "p5": "P5"}
    ax.text(-0.03, -0.5 / len(pick) * 0 + 1.0 + 0.02, " | ".join(short[m] for m in a.axes), transform=ax.transAxes,
            ha="right", va="bottom", fontsize=6.2, style="italic")
    return pick


def make_figures(R, levels, slots, a, out):
    plt = style()
    from matplotlib.cm import ScalarMappable
    from matplotlib.colors import Normalize
    from matplotlib.lines import Line2D
    cmap = vivid_cmap()
    cvals = disp(a.color, R["exact"][a.color])
    cvals_all = np.r_[cvals, [disp(a.color, v[a.color]) for v in R["named"].values()]]
    norm = Normalize(cvals_all.min(), cvals_all.max() if np.ptp(cvals_all) else cvals_all.min() + 1)
    sw = f", ≤ {a.max_switch} price changes" if a.max_switch is not None else ""

    # ---- figure P3: (a) 3-D front, (b) plans on the front
    a.levels_used = [levels[k] for k in sorted(set(R["exact_plans"].ravel()))]
    fig = plt.figure(figsize=(7.16, 3.35))
    ax = fig.add_axes([0.0, 0.06, 0.52, 0.90], projection="3d", computed_zorder=False)
    handles = draw_3d(ax, R, a, cmap, norm)
    ax.set_title(f"(a) Exact 3-D Pareto front (Choco){sw}", y=0.98)
    cax = fig.add_axes([0.035, 0.20, 0.010, 0.52])
    cb = fig.colorbar(ScalarMappable(norm, cmap), cax=cax)
    cb.set_label(METRIC[a.color][0], fontsize=6.8, labelpad=1)
    cax.yaxis.set_label_position("left")
    cax.yaxis.set_ticks_position("left")
    cb.ax.tick_params(labelsize=6)
    ax2 = fig.add_axes([0.70, 0.14, 0.29, 0.70])
    draw_plan_map(ax2, R, levels, slots, a, a.rows)
    ax2.set_title("(b) Best-QoE Pareto plan per revenue level: price per hour", fontsize=8.5, loc="right", pad=14)
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.04), ncol=4, frameon=False,
               columnspacing=1.0, handletextpad=0.3)
    if a.note:
        fig.text(0.5, 0.995, a.note, ha="center", va="top", fontsize=8, color=RED, fontweight="bold")
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(out, f"figP3_pareto3d_choco.{ext}"), bbox_inches="tight")
    plt.close(fig)
    print(f"  -> {os.path.relpath(out)}/figP3_pareto3d_choco.(png|pdf)")

    # ---- figure P3a: 3-D panel alone (IEEE single column)
    fig = plt.figure(figsize=(3.5, 3.45))
    ax = fig.add_axes([0.05, 0.20, 0.80, 0.78], projection="3d", computed_zorder=False)
    a.levels_used = [levels[k] for k in sorted(set(R["exact_plans"].ravel()))]
    handles = draw_3d(ax, R, a, cmap, norm, short=True)
    cax = fig.add_axes([0.06, 0.24, 0.018, 0.40])
    cb = fig.colorbar(ScalarMappable(norm, cmap), cax=cax)
    cb.set_label(METRIC[a.color][0], fontsize=6.5, labelpad=1)
    cax.yaxis.set_label_position("left")
    cax.yaxis.set_ticks_position("left")
    cb.ax.tick_params(labelsize=6)
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.10), ncol=2, frameon=False, fontsize=6,
               columnspacing=0.8, handletextpad=0.3)
    if a.note:
        fig.text(0.5, 0.995, a.note, ha="center", va="top", fontsize=7, color=RED, fontweight="bold")
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(out, f"figP3a_pareto3d.{ext}"), bbox_inches="tight")
    plt.close(fig)
    print(f"  -> {os.path.relpath(out)}/figP3a_pareto3d.(png|pdf)")
    _ = Line2D  # keep import (used by draw_3d)
    html(R, levels, slots, a, out)


def html(R, levels, slots, a, out):
    try:
        import plotly.graph_objects as go
    except ImportError:
        print("  (plotly not installed: no interactive HTML -> pip3 install plotly)")
        return
    ax_x, ax_y, ax_z = a.axes
    E, Pl = R["exact"], R["exact_plans"]
    X, Y, Z, Cv = (disp(m, E[m]) for m in (ax_x, ax_y, ax_z, a.color))
    plan_txt = lambda p: " ".join(f"{t}h:{LEVEL_SHORT.get(levels[k], levels[k])}" for t, k in zip(slots, p))  # noqa: E731
    hover = [f"{METRIC[ax_x][0]}: {X[i]:.3f}<br>{METRIC[ax_y][0].replace('$10^{-3}$', '1e-3')}: {Y[i]:.3f}<br>"
             f"{METRIC[ax_z][0]}: {Z[i]:.3f}<br>{METRIC[a.color][0]}: {Cv[i]:.4f}<br>{plan_txt(Pl[i])}"
             for i in range(len(X))]
    turbo = [[0, "#30123b"], [0.15, "#4686fb"], [0.35, "#1ae4b6"], [0.5, "#a2fc3c"], [0.65, "#fabb39"],
             [0.85, "#e4460a"], [1, "#7a0403"]]
    tr = [go.Scatter3d(x=X, y=Y, z=Z, mode="markers", text=hover, hoverinfo="text", name="Exact front (DP)",
                       marker=dict(size=4, color=Cv, colorscale=turbo, line=dict(width=0.5, color="black"),
                                   colorbar=dict(title=METRIC[a.color][0], thickness=12)))]
    if len(X) >= 4:
        tr.insert(0, go.Mesh3d(x=X, y=Y, z=Z, intensity=Cv, colorscale=turbo, opacity=0.45, showscale=False,
                               delaunayaxis="z", name="front surface", hoverinfo="skip"))
    for lv, v in R["named"].items():
        tr.append(go.Scatter3d(x=[disp(ax_x, v[ax_x])], y=[disp(ax_y, v[ax_y])], z=[disp(ax_z, v[ax_z])],
                               mode="markers", name=f"{LEVEL_LAB.get(lv, lv)} (all hours)",
                               marker=dict(size=9, color=LEVEL_COL[lv], symbol="diamond", line=dict(width=1, color="black"))))
    kn = R["knee"]
    tr.append(go.Scatter3d(x=[disp(ax_x, kn[ax_x])], y=[disp(ax_y, kn[ax_y])], z=[disp(ax_z, kn[ax_z])],
                           mode="markers+text", text=["knee"], name="Knee: " + plan_txt(Pl[R["knee_idx"]]),
                           marker=dict(size=11, color=YELLOW, symbol="x", line=dict(width=2, color="black"))))
    lab = lambda m: METRIC[m][0].replace("$10^{-3}$", "1e-3") + (" ↑" if METRIC[m][1] > 0 else " ↓")  # noqa: E731
    f = go.Figure(tr)
    f.update_layout(title=f"Exact 3-D Pareto front of the QoD price plan ({slots[0]}h–{slots[-1] + 1}h), Choco solver",
                    scene=dict(xaxis_title=lab(ax_x), yaxis_title=lab(ax_y), zaxis_title=lab(ax_z)),
                    legend=dict(font=dict(size=10)), template="plotly_white")
    path = os.path.join(out, "figP3_pareto3d_choco.html")
    f.write_html(path, include_plotlyjs="cdn")
    print(f"  -> {os.path.relpath(path)}  (open in a browser: rotate, hover = plan per hour)")


# ============================================================================= orchestration
def analyse(tables, levels, slots, a):
    C, w, base = contributions(tables, slots, levels)
    G, sc = integer_tables(C, a.axes)
    T, K = len(slots), len(levels)
    t0 = time.time()
    S_dp, P_dp = dp_front(G, a.max_switch)
    t_dp = time.time() - t0
    info = {"hours": f"{slots[0]}h-{slots[-1] + 1}h", "levels": K, "plans": int(K ** T), "max_switch": a.max_switch,
            "dp_front": int(len(S_dp)), "dp_seconds": round(t_dp, 2), "axes": list(a.axes), "color": a.color,
            "rationing": a.rationing}
    bf = None
    if K ** T <= a.bf_max:
        t0 = time.time()
        bf = brute_force(G, a.max_switch)
        info["bruteforce_plans"] = int(len(bf))
        if len(bf) <= 3e5:  # full equality of the fronts (cheap at this size)
            keep = nondominated(bf)
            info["dp_equals_bruteforce"] = {tuple(r) for r in bf[keep]} == {tuple(r) for r in S_dp}
        info["bruteforce_seconds"] = round(time.time() - t0, 2)
    R = {"choco": None}
    if HAS_CHOCO and not a.no_choco:
        t0 = time.time()
        S_ch, P_ch, log = choco_front(G, a.grid[0], a.grid[1], a.max_switch, a.time_limit)
        info.update(choco_problems=len(log), choco_seconds=round(time.time() - t0, 1), choco_front=int(len(S_ch)))
        g_dp = [(eps_optimum(S_dp, ex, ey), z) for ex, ey, z in log]
        gap = [0 if (r is None and z is None) else (np.inf if (r is None) != (z is None) else r - z) for r, z in g_dp]
        info["max_gap_vs_dp"] = float(np.max(gap)) if gap else None
        if bf is not None:
            g_bf = [(eps_optimum(bf, ex, ey), z) for ex, ey, z in log]
            gap = [0 if (r is None and z is None) else (np.inf if (r is None) != (z is None) else r - z) for r, z in g_bf]
            info["max_gap_vs_bruteforce"] = float(np.max(gap)) if gap else None
        lo, hi = S_dp.min(axis=0), S_dp.max(axis=0)
        info["choco_hypervolume_share"] = round(hypervolume(S_ch, lo, hi) / hypervolume(S_dp, lo, hi), 4) if len(S_ch) else 0
        exact_set = {tuple(r) for r in S_dp}
        info["choco_points_on_exact_front"] = int(sum(tuple(r) in exact_set for r in S_ch))
        R["choco"] = {m: np.array([plan_values(C, p)[m] for p in P_ch]) for m in METRIC}
        R["choco_plans"] = P_ch
    elif not HAS_CHOCO:
        print("[front] pychoco not installed -> exact DP front only (pip3 install pychoco)")
    vals = [plan_values(C, p) for p in P_dp]
    R["exact"] = {m: np.array([v[m] for v in vals]) for m in METRIC}
    R["exact_plans"] = P_dp
    R["knee_idx"] = knee_index(S_dp.astype(float))
    R["knee"] = {m: R["exact"][m][R["knee_idx"]] for m in METRIC}
    R["named"] = {}
    for k, lv in enumerate(levels):
        if lv == "mid":
            continue
        p = np.full(T, k)
        v = plan_values(C, p)
        s = G[:, np.arange(T), p].sum(axis=1)
        dom = np.all(S_dp >= s, axis=1) & np.any(S_dp > s, axis=1)
        v["on_front"] = not dom.any()
        R["named"][lv] = v
    info["dissat_no_nef"] = base["dissat"]
    return R, info, C


def write_outputs(R, info, levels, slots, a, out):
    hdr = list(METRIC) + [f"{t}h" for t in slots]
    for tag, key in (("exact", "exact_plans"), ("choco", "choco_plans")):
        if R.get(key) is None:
            continue
        V = R[tag]
        with open(os.path.join(out, f"p3d_front_{tag}.csv"), "w", newline="") as f:
            wr = csv.writer(f)
            wr.writerow(hdr)
            for i, p in enumerate(R[key]):
                wr.writerow([f"{V[m][i]:.6g}" for m in METRIC] + [levels[k] for k in p])
    f3 = lambda m, v: f"{disp(m, v):.3f}"  # noqa: E731
    cols = ["profit", "harm", "dmos", "p5", "dissat", "welfare"]
    th = "| Plan | " + " | ".join(METRIC[m][0].replace("$10^{-3}$", "1e-3") for m in cols) + " | On front | Levels per hour |"
    lines = [th, "|" + "---|" * (len(cols) + 3)]
    for lv, v in R["named"].items():
        lines.append(f"| {LEVEL_LAB.get(lv, lv)} (all hours) | " + " | ".join(f3(m, v[m]) for m in cols) +
                     f" | {'yes' if v['on_front'] else 'no'} | {LEVEL_SHORT.get(lv, lv)} x {len(slots)} |")
    kn, kp = R["knee"], R["exact_plans"][R["knee_idx"]]
    lines.append("| **Knee (balanced)** | " + " | ".join(f3(m, kn[m]) for m in cols) + " | yes | " +
                 " ".join(f"{t}h:{LEVEL_SHORT.get(levels[k], levels[k])}" for t, k in zip(slots, kp)) + " |")
    o = np.argsort(R["exact"]["profit"])
    rep = o[np.unique(np.linspace(0, len(o) - 1, min(a.rows, len(o))).round().astype(int))]
    lines2 = [th.replace("On front | ", ""), "|" + "---|" * (len(cols) + 2)]
    for i in rep:
        lines2.append(f"| #{i} | " + " | ".join(f3(m, R["exact"][m][i]) for m in cols) + " | " +
                      " ".join(f"{t}h:{LEVEL_SHORT.get(levels[k], levels[k])}" for t, k in zip(slots, R["exact_plans"][i])) + " |")
    with open(os.path.join(out, "tableP3_pareto3d.md"), "w") as f:
        f.write(f"3-D Pareto front of the QoD price plan ({info['hours']}, {a.rationing} admission)\n\n")
        f.write("Validation\n\n" + "\n".join(f"- {k}: {v}" for k, v in info.items()) + "\n\n")
        f.write(f"Constant plans and knee (no NEF: dissatisfied {info['dissat_no_nef']:.3f} %)\n\n" + "\n".join(lines) + "\n\n")
        f.write("Representative Pareto-optimal plans (sorted by profit)\n\n" + "\n".join(lines2) + "\n")
    with open(os.path.join(out, "p3d_validation.json"), "w") as f:
        json.dump(info, f, indent=2, default=str)
    print(f"  -> {os.path.relpath(out)}/tableP3_pareto3d.md, p3d_front_*.csv, p3d_validation.json")
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slots", type=int, nargs="+", default=None, help="hours of the plan (default: evening hours "
                    "of pricing.window_slots, i.e. >= 17h)")
    ap.add_argument("--levels", nargs="+", default=DEFAULT_LEVELS)
    ap.add_argument("--rationing", choices=["random", "priority"], default="random")
    ap.add_argument("--axes", nargs=3, default=["profit", "harm", "dmos"], choices=list(METRIC),
                    help="x y z of the 3-D plot; Choco optimises z under ε-constraints on x and y")
    ap.add_argument("--color", default="p5", choices=list(METRIC))
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--draws", type=int, default=20, help="demand draws per seed and cell")
    ap.add_argument("--quick", action="store_true", help="2 seeds x 8 draws, 10 x 5 ε-grid (smoke test)")
    ap.add_argument("--grid", type=int, nargs=2, default=[30, 15], help="ε-grid (x, y) of the Choco problems")
    ap.add_argument("--max-switch", type=int, default=None, help="max price-level changes between consecutive hours")
    ap.add_argument("--time-limit", default="10s")
    ap.add_argument("--no-choco", action="store_true")
    ap.add_argument("--bf-max", type=float, default=3e6, help="enumerate all plans when K^T <= this")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--recompute", action="store_true", help="ignore the cached tables")
    ap.add_argument("--plot-only", action="store_true", help="use the cached tables, never recompute")
    ap.add_argument("--from-tables", default=None, help="use this tables JSON (skips the Monte-Carlo stage)")
    ap.add_argument("--view", type=float, nargs=2, default=[24, -52], help="elevation azimuth of the 3-D view")
    ap.add_argument("--rows", type=int, default=9, help="plans shown in panel (b)")
    ap.add_argument("--note", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--prices", default=rpath("results", "prices", "prices.json"))
    ap.add_argument("--out", default=rpath("results", "sim"))
    a = ap.parse_args()
    if a.quick:
        a.seeds, a.draws, a.grid = 2, 8, [12, 6]
    os.makedirs(a.out, exist_ok=True)
    cache = a.from_tables or os.path.join(a.out, "p3d_tables.json")

    tables, meta = None, None
    if os.path.exists(cache) and not a.recompute:
        d = json.load(open(cache))
        meta, tables = d["meta"], d["tables"]
        if not (a.plot_only or a.from_tables):
            want = {"levels": a.levels, "rationing": a.rationing, "seeds": a.seeds, "draws": a.draws,
                    "calibration": os.environ.get("NEF_LAB_CALIBRATION", ""), "prices_sha1": file_sha1(a.prices)}
            if a.slots:
                want["slots"] = a.slots
            diff = [k for k, v in want.items() if meta.get(k) != v]
            if diff:
                print(f"[tables] cache does not match ({', '.join(diff)}) -> recomputing")
                tables = None
            else:
                print(f"[tables] reusing {os.path.relpath(cache)} (--recompute to force)")
    if tables is None:
        if a.plot_only or a.from_tables:
            sys.exit(f"no tables at {cache}: run once without --plot-only")
        if not os.path.exists(a.prices):
            sys.exit(f"missing {a.prices}: run pricing/precompute_prices.py first")
        tab = json.load(open(a.prices))
        avail = [int(t) for t in tab["meta"]["window_slots"]]
        slots = a.slots or [t for t in avail if t >= 17]
        cal = os.environ.get("NEF_LAB_CALIBRATION", "")
        print(f"[tables] calibration: {cal or 'analytical (NEF_LAB_CALIBRATION unset)'}")
        if not cal:
            print("[tables] WARNING: your prices were computed with the testbed calibration -> set "
                  "NEF_LAB_CALIBRATION=results/calibration_testbed.json for consistent numbers")
        tables, secs = compute_tables(a, a.levels, slots, a.prices)
        meta = {"slots": slots, "levels": a.levels, "rationing": a.rationing, "seeds": a.seeds, "draws": a.draws,
                "calibration": cal, "prices_sha1": file_sha1(a.prices), "seconds": round(secs, 1)}
        with open(cache, "w") as f:
            json.dump({"meta": meta, "tables": tables}, f)
        print(f"[tables] {os.path.relpath(cache)} written ({secs:.0f} s)")
    slots, levels = [int(t) for t in meta["slots"]], list(meta["levels"])
    a.rationing = meta.get("rationing", a.rationing)

    print(f"[front] {len(levels)} levels ^ {len(slots)} hours = {len(levels) ** len(slots):,} plans; axes "
          f"{'/'.join(a.axes)}, colour {a.color}" + (f", max {a.max_switch} switches" if a.max_switch is not None else ""))
    R, info, _ = analyse(tables, levels, slots, a)
    for k, v in info.items():
        print(f"  {k}: {v}")
    write_outputs(R, info, levels, slots, a, a.out)
    make_figures(R, levels, slots, a, a.out)


if __name__ == "__main__":
    main()
