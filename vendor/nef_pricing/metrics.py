"""Métriques de QoE et économiques.

- distribution des MOS par session (grille de sensibilités) ;
- MOS moyen, MOS du 5e centile, % de sessions satisfaites (MOS >= 3,5),
  indice d'équité QoE de Hossfeld et al. F = 1 - 2 sigma / (H - L), H = 5, L = 1 ;
- revenu, profit, taux de rejet, utilisation, bien-être.
"""
from __future__ import annotations

import numpy as np

from .admission import Outcome
from .model import Model

SATISFIED = 3.5


def mos_distribution(model: Model, out: Outcome, grid: int = 200, with_af: bool = False):
    """Retourne (valeurs de MOS, poids en sessions[, indice d'AF])."""
    L = out.load
    D = model.degr.D(L)
    vals, wts, idx = [], [], []
    u = (np.arange(grid) + 0.5) / grid
    for i, s in enumerate(model.sens):
        n0 = len(vals)
        w = model.X[i] / grid
        m1 = model.q2[i] - s.Q(u) * D[i]
        if out.rationing == "random":
            v_d = 1.0 - out.y_demand[i] / model.X[i]
            dem = u >= v_d
            vals += list(m1[~dem]); wts += [w] * int((~dem).sum())
            nd = int(dem.sum())
            vals += [model.q2[i]] * nd; wts += [w * out.phi] * nd
            vals += list(m1[dem]); wts += [w * (1 - out.phi)] * nd
        else:
            thr = out.u_min[i] if out.u_min is not None else 1.0 - out.y_served[i] / model.X[i]
            m2 = u >= thr
            vals += list(np.where(m2, model.q2[i], m1)); wts += [w] * grid
        idx += [i] * (len(vals) - n0)
    vals, wts, idx = np.array(vals), np.array(wts), np.array(idx)
    keep = wts > 0
    if with_af:
        return vals[keep], wts[keep], idx[keep]
    return vals[keep], wts[keep]


def weighted_quantile(vals, wts, q):
    o = np.argsort(vals)
    cw = np.cumsum(wts[o]) / wts.sum()
    return float(np.interp(q, cw, vals[o]))


def summarize(model: Model, out: Outcome, price=None) -> dict:
    vals, wts = mos_distribution(model, out)
    mean = float(np.average(vals, weights=wts))
    sd = float(np.sqrt(np.average((vals - mean) ** 2, weights=wts)))
    y = out.y_served
    p = out.p if price is None else price
    if isinstance(p, str):
        revenue = 0.0
    else:   # prix infini (QoS fermée) : rien n'est vendu, revenu nul (évite inf x 0 = nan)
        pv = np.broadcast_to(np.asarray(p, float), y.shape)
        revenue = float(np.sum(np.where(y > 0, pv * model.b * y, 0.0)))
    if isinstance(p, str):
        revenue = out.extra.get("revenue", 0.0)
    cost = model.c * float(model.b @ y)
    # bien-être effectif (tient compte du rationnement) : sum_i theta_i * gain de MOS
    v1, w1, i1 = mos_distribution(model, out, with_af=True)
    base = Outcome("none", np.zeros(model.N), np.zeros(model.N), model.base_load())
    v0, w0, i0 = mos_distribution(model, base, with_af=True)
    gain = np.array([np.sum((v1 * w1)[i1 == i]) - np.sum((v0 * w0)[i0 == i]) for i in range(model.N)])
    welfare_eff = float(model.theta @ gain) - cost
    return {
        "mos_mean": mean,
        "mos_p5": weighted_quantile(vals, wts, 0.05),
        "pct_satisfied": float(wts[vals >= SATISFIED].sum() / wts.sum()),
        "qoe_fairness": 1.0 - 2.0 * sd / 4.0,
        "revenue": revenue,
        "profit": revenue - cost,
        "rejection_rate": out.rejection_rate,
        "rejected": out.rejected,
        "utilization": out.load / model.R,
        "demand_ratio": model.load(out.y_demand) / model.R,
        "welfare": welfare_eff,
        "welfare_model": model.welfare(y),
        "m2_share": float(y.sum() / model.X.sum()),
    }
