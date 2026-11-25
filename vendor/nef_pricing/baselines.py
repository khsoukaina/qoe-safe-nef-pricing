"""Stratégies de tarification (références et propositions) et évaluation sur 24 h.

La demande d'équilibre y^NE(p, x) d'un créneau (x = facteur d'échelle de la demande
réalisée par rapport à la prévision) est précalculée sur une grille (DemandSurface) puis
interpolée : cela rend les évaluations Monte-Carlo rapides.

Stratégies (prix par créneau, annoncés AVANT la réalisation de la demande) :
  no_nef       pas d'exposition (référence basse)
  cost         prix = coût marginal c (aucune régulation : ruée en pointe)
  flat         prix unique sur la journée (pratique actuelle : tarif fixe)
  revenue_max  prix qui maximise le profit de l'opérateur, par créneau
  mean_A       prix optimal (objectif A) calculé sur la demande moyenne prévue
  mean_B       prix optimal (objectif B, MOS) calculé sur la demande moyenne prévue
  quantile     prix au quantile 1 - eps (Théorème du quantile)
  cantelli     prix robuste sans hypothèse de loi (Cantelli)
  oracle_A     prix optimal A avec la demande réalisée (borne haute, inatteignable)
  kelly        enchère : volume K dimensionné au quantile (Corollaire), prix émergent,
               prix de réserve = coût marginal (la NEF réduit le volume si besoin)
"""
from __future__ import annotations

import hashlib
import os
import pickle

import numpy as np
from scipy.stats import norm

from . import admission, kelly, leader, metrics
from .followers import nash
from .model import Model

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "results", "cache")


class DemandSurface:
    """y^NE(p, x) sur une grille de prix (log) x échelles de demande."""

    def __init__(self, model: Model, prices=None, scales=None, n_p: int = 26):
        self.model = model
        hi = leader.p_bar(model)
        self.prices = np.unique(np.r_[model.c, np.geomspace(max(model.c, 1e-4), hi, n_p - 1)]) if prices is None else np.asarray(prices)
        self.scales = np.linspace(0.6, 1.5, 7) if scales is None else np.asarray(scales)
        Y = np.zeros((len(self.prices), len(self.scales), model.N))
        for j, x in enumerate(self.scales):
            mx = model.with_X(model.X * x)
            y = None
            for k, p in enumerate(self.prices):
                y = nash(mx, p, y0=y)
                Y[k, j] = y
        self.Y = Y

    def y(self, p: float, x: float) -> np.ndarray:
        P, S = self.prices, self.scales
        p = float(np.clip(p, P[0], P[-1])); x = float(np.clip(x, S[0], S[-1]))
        k = int(np.clip(np.searchsorted(P, p) - 1, 0, len(P) - 2))
        j = int(np.clip(np.searchsorted(S, x) - 1, 0, len(S) - 2))
        tp = (p - P[k]) / (P[k + 1] - P[k]); tx = (x - S[j]) / (S[j + 1] - S[j])
        Y = self.Y
        yk = (1 - tx) * Y[k, j] + tx * Y[k, j + 1]
        yk1 = (1 - tx) * Y[k + 1, j] + tx * Y[k + 1, j + 1]
        # au-dessus du prix maximal de la grille : personne ne réserve
        return (1 - tp) * yk + tp * yk1

    def load(self, p, x) -> float:
        m = self.model
        return float(m.a @ (m.X * x) + m.Delta @ self.y(p, x))


def _key(models, tag):
    h = hashlib.md5()
    for m in models:
        h.update(np.r_[m.X, m.a, m.b, m.theta, m.q2, m.R, m.c].tobytes())
        h.update(repr(type(m.degr)).encode()); h.update(repr(m.sens).encode())
        if hasattr(m.degr, "delta"):
            h.update(np.r_[m.degr.delta, m.degr.kappa].tobytes())
        else:
            for Lg, Dv in m.degr.curves:
                h.update(np.r_[Lg, Dv].tobytes())
    return f"{tag}_{h.hexdigest()[:12]}"


def surfaces(models, tag="surf", **kw):
    os.makedirs(CACHE, exist_ok=True)
    # la clé inclut la grille (n_p, scales) : le mode rapide et le mode complet ne partagent pas de cache
    grid = hashlib.md5(repr(sorted((k, np.asarray(v).tolist()) for k, v in kw.items())).encode()).hexdigest()[:6]
    path = os.path.join(CACHE, _key(models, tag) + f"_{grid}.pkl")
    if os.path.exists(path):
        with open(path, "rb") as f:
            S = pickle.load(f)
        for s, m in zip(S, models):
            s.model = m
        return S
    S = [DemandSurface(m, **kw) for m in models]
    with open(path, "wb") as f:
        pickle.dump(S, f)
    return S


# ---------------------------------------------------------------- prix des stratégies
def clearing_from_surface(s: DemandSurface, x: float) -> float:
    m = s.model
    if s.load(m.c, x) <= m.R:
        return m.c
    lo, hi = m.c, s.prices[-1]
    for _ in range(50):
        mid = 0.5 * (lo + hi)
        if s.load(mid, x) <= m.R:
            hi = mid
        else:
            lo = mid
    return hi


def slot_outcome(s: DemandSurface, p: float, x: float, rationing: str):
    m = s.model.with_X(s.model.X * x)
    return m, admission.admit(m, s.y(p, x), p, rationing)


def expected_score(s: DemandSurface, p: float, xs, rationing="random", key="welfare"):
    vals = []
    for x in xs:
        m, out = slot_outcome(s, p, x, rationing)
        if key == "welfare":
            vals.append(metrics.summarize(m, out)["welfare"])
        elif key == "mos":
            vals.append(metrics.summarize(m, out)["mos_mean"])
        else:
            vals.append(metrics.summarize(m, out)["profit"])
    return float(np.mean(vals))


def best_price(s: DemandSurface, key: str, xs=(1.0,), rationing="random", grid=None):
    grid = s.prices if grid is None else grid
    sc = [expected_score(s, p, xs, rationing, key) for p in grid]
    k = int(np.argmax(sc))
    lo, hi = grid[max(k - 1, 0)], grid[min(k + 1, len(grid) - 1)]
    fine = np.linspace(lo, hi, 9)
    sc2 = [expected_score(s, p, xs, rationing, key) for p in fine]
    return float(fine[int(np.argmax(sc2))])


def strategy_prices(S, cv: float, eps: float = 0.05, rationing: str = "random", flat_grid=None):
    """Prix par créneau pour chaque stratégie à prix affiché (annoncés avant la demande)."""
    T = len(S)
    q = 1 + norm.ppf(1 - eps) * cv
    qc = 1 + cv * np.sqrt((1 - eps) / eps)
    P = {"no_nef": np.full(T, np.inf), "cost": np.array([s.model.c for s in S])}
    P["quantile"] = np.array([clearing_from_surface(s, q) for s in S])
    P["cantelli"] = np.array([clearing_from_surface(s, min(qc, s.scales[-1])) for s in S])
    P["mean_A"] = np.array([max(best_price(s, "welfare"), clearing_from_surface(s, 1.0)) for s in S])
    P["mean_B"] = np.array([best_price(s, "mos") for s in S])
    P["revenue_max"] = np.array([best_price(s, "profit") for s in S])
    grid = np.geomspace(S[0].model.c, np.median([s.prices[-1] for s in S]), 18) if flat_grid is None else flat_grid
    tot = [sum(expected_score(s, p, (1.0,), rationing, "welfare") for s in S) for p in grid]
    P["flat"] = np.full(T, grid[int(np.argmax(tot))])
    return P


# ---------------------------------------------------------------- évaluation
def evaluate(S, prices: dict, xs_by_slot, rationing="random", kelly_volumes=None, oracle=False):
    """Moyennes Monte-Carlo par créneau et par stratégie.
    xs_by_slot : tableau (T, n) des échelles de demande réalisées."""
    T = len(S)
    res = {}
    names = list(prices) + (["kelly"] if kelly_volumes is not None else []) + (["oracle_A"] if oracle else [])
    for name in names:
        rows = []
        for t in range(T):
            s = S[t]
            acc = []
            for x in xs_by_slot[t]:
                m = s.model.with_X(s.model.X * x)
                if name == "no_nef":
                    out = admission.Outcome(np.inf, np.zeros(m.N), np.zeros(m.N), m.base_load())
                    r = metrics.summarize(m, out, 0.0)
                elif name == "kelly":
                    K = kelly_volumes[t]
                    y, pk, bids, _ = kelly.kelly_reserve(m, K)
                    out = admission.realize_kelly(m, y)
                    out.extra["revenue"] = float(bids.sum())
                    r = metrics.summarize(m, out)
                    r["price"] = pk / 1.0
                elif name == "oracle_A":
                    p = clearing_from_surface(s, x)
                    p = max(p, best_price(s, "welfare", xs=(x,), rationing=rationing,
                                          grid=np.linspace(p, s.prices[-1] * 0.5, 8)))
                    out = admission.admit(m, s.y(p, x), p, rationing)
                    r = metrics.summarize(m, out)
                else:
                    p = prices[name][t]
                    out = admission.admit(m, s.y(p, x), p, rationing)
                    r = metrics.summarize(m, out)
                acc.append(r)
            keys = acc[0].keys()
            rows.append({k: float(np.mean([a[k] for a in acc])) for k in keys})
        res[name] = rows
    return res


def kelly_volumes(S, cv: float, eps: float = 0.05):
    """Volume (Mbit/s de réservation) dimensionné au quantile de la charge (Corollaire)."""
    q = 1 + norm.ppf(1 - eps) * cv
    out = []
    for s in S:
        m = s.model
        ratio = float(np.mean(m.Delta / m.b))
        out.append(max(0.0, (m.R - float(m.a @ (m.X * q))) / ratio))
    return np.array(out)
