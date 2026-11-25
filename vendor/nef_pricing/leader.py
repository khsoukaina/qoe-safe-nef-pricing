"""Niveau leader (NEF) : équilibre de Stackelberg.

- p_bar, clearing_price : prix au-delà duquel personne ne réserve ; prix d'équilibre
  de marché (plus petit prix tel que la demande tient dans la capacité) ;
- social_optimum, pigou_prices : optimum A ou B et prix qui l'implémentent
  (coût + prix ombre + taxe pigouvienne) ;
- optimal_uniform_price, revenue_max_price : recherche à une dimension sur le prix ;
- quantile_price, cantelli_quantile, gaussian_quantile : principe du quantile ;
- ramsey_schedule : recouvrement des coûts sur l'horizon (décomposition lagrangienne).
"""
from __future__ import annotations

from typing import Callable, Sequence

import numpy as np
from scipy.optimize import brentq, minimize, minimize_scalar
from scipy.stats import norm

from .followers import nash, variational_equilibrium
from .model import Model


# ---------------------------------------------------------------- prix extrêmes
def p_bar(model: Model) -> float:
    """Prix au-delà duquel aucune AF ne bascule de session (y* = 0)."""
    L_max = float(model.b @ model.X)
    D = model.degr.D(L_max)
    Q1 = np.array([s.Q(1.0) for s in model.sens])
    return float(np.max(model.theta * D * Q1 / model.b)) * 1.05 + model.c


def clearing_price(model: Model, tol: float = 1e-7) -> float:
    """Plus petit p >= c tel que la demande d'équilibre tienne dans la capacité."""
    if model.base_load() > model.R:
        return np.inf
    excess = lambda p: model.load(nash(model, p)) - model.R
    if excess(model.c) <= 0:
        return model.c
    hi = p_bar(model)
    return brentq(excess, model.c, hi, xtol=tol)


# ---------------------------------------------------------------- optimum social
def _grad_objective(model: Model, y, objective: str):
    L = model.load(y)
    D, dD = model.degr.D(L), model.degr.dD(L)
    M = model.Mv(y)
    w = model.theta if objective == "A" else np.ones(model.N)
    ext_total = np.sum(w * dD * model.X * M)            # sum_j w_j dD_j X_j M_j
    own = model.dloss_own(y)
    ext_minus = ext_total - w * dD * model.X * M        # termes j != i
    g = -w * own - model.Delta * ext_minus
    if objective == "A":
        g = g - model.c * model.b
    return g


def social_optimum(model: Model, objective: str = "A", y0=None):
    """Maximise W (A) ou le MOS total (B) sous la contrainte de capacité.

    Retourne (y_opt, mu) ; mu est le multiplicateur de capacité par unité de Delta
    (en u.m. pour A, en points de MOS pour B).
    """
    f = (lambda y: -model.welfare(y)) if objective == "A" else (lambda y: -model.mos_total(y))
    jac = lambda y: -_grad_objective(model, y, objective)
    cap = model.m2_capacity()
    cons = [{"type": "ineq", "fun": lambda y: cap - model.Delta @ y, "jac": lambda y: -model.Delta}]
    x0 = model.X * 0.3 if y0 is None else np.asarray(y0, float)
    best = None
    for start in ([x0] if y0 is not None else [model.X * s for s in (0.05, 0.3, 0.7)]):
        res = minimize(f, start, jac=jac, bounds=[(0, x) for x in model.X], constraints=cons,
                       method="SLSQP", options={"ftol": 1e-12, "maxiter": 2000})
        if best is None or res.fun < best.fun:
            best = res
    y = np.clip(best.x, 0, model.X)
    g = _grad_objective(model, y, objective)
    interior = (y > 1e-6 * model.X) & (y < model.X * (1 - 1e-6))
    mu = 0.0
    if model.Delta @ y > cap - 1e-6 * max(cap, 1) and interior.any():
        mu = max(0.0, float(np.median(g[interior] / model.Delta[interior])))
    return y, mu


def pigou_prices(model: Model, y_opt, mu: float, objective: str = "A") -> np.ndarray:
    """Prix personnalisés qui implémentent l'optimum (Théorèmes de décomposition et par classe)."""
    L = model.load(y_opt)
    dD = model.degr.dD(L)
    M = model.Mv(y_opt)
    w = model.theta if objective == "A" else np.ones(model.N)
    tot = np.sum(w * dD * model.X * M)
    E_minus = tot - w * dD * model.X * M
    if objective == "A":
        return model.c + model.Delta / model.b * (mu + E_minus)
    return model.theta * model.Delta / model.b * (mu + E_minus)


# ---------------------------------------------------------------- prix uniformes
def evaluate_price(model: Model, p: float, capacity: str = "shadow"):
    """Résultat au prix p : capacity='shadow' -> équilibre variationnel (prix ombre),
    capacity='none' -> demande sans contrainte."""
    if capacity == "shadow":
        y, mu = variational_equilibrium(model, p)
    else:
        y, mu = nash(model, p), 0.0
    return {"p": p, "y": y, "mu": mu, "load": model.load(y), "welfare": model.welfare(y),
            "mos": model.mos_mean(y), "revenue": model.revenue(y, p),
            "profit": model.revenue(y, p) - model.c * float(model.b @ y)}


def price_curve(model: Model, prices: Sequence[float], capacity: str = "shadow"):
    rows = [evaluate_price(model, float(p), capacity) for p in prices]
    return {k: np.array([r[k] for r in rows]) for k in ("p", "load", "welfare", "mos", "revenue", "profit", "mu")}


def _argmax_price(model: Model, score: Callable[[float], float], n_grid: int = 40):
    lo, hi = model.c, p_bar(model)
    grid = np.linspace(lo, hi, n_grid)
    vals = np.array([score(p) for p in grid])
    k = int(np.argmax(vals))
    a, b = grid[max(k - 1, 0)], grid[min(k + 1, n_grid - 1)]
    res = minimize_scalar(lambda p: -score(p), bounds=(a, b), method="bounded", options={"xatol": 1e-5 * hi})
    return (res.x, -res.fun) if -res.fun >= vals[k] else (grid[k], vals[k])


def optimal_uniform_price(model: Model, objective: str = "A"):
    """Meilleur prix uniforme (capacité respectée par le prix ombre)."""
    key = "welfare" if objective == "A" else "mos"
    p, _ = _argmax_price(model, lambda p: evaluate_price(model, p)[key])
    return max(p, clearing_price(model))


def revenue_max_price(model: Model):
    """Prix qui maximise le profit de l'opérateur (au moins le prix d'équilibre de marché)."""
    p_clr = clearing_price(model)
    p, _ = _argmax_price(model, lambda p: evaluate_price(model, max(p, p_clr), "none")["profit"])
    return max(p, p_clr)


# ---------------------------------------------------------------- incertitude
def gaussian_quantile(m: float, s: float, eps: float) -> float:
    return m + norm.ppf(1 - eps) * s


def cantelli_quantile(m: float, s: float, eps: float) -> float:
    """Borne de Cantelli : P(X >= m + k s) <= 1/(1+k^2) pour toute loi."""
    return m + s * np.sqrt((1 - eps) / eps)


def quantile_price(model_for_scale: Callable[[float], Model], q: float) -> float:
    """Théorème du quantile : p_eps = p_clr(q_{1-eps})."""
    return clearing_price(model_for_scale(q))


# ---------------------------------------------------------------- horizon et budget
def ramsey_schedule(models: Sequence[Model], F: float, n_grid: int = 40):
    """Recouvrement des coûts (objectif A) par décomposition lagrangienne.

    Pour lambda fixé, chaque créneau maximise W_t(p) + lambda * profit_t(p) sur une grille ;
    une bissection sur lambda ajuste sum_t profit_t >= F.
    Retourne (prix par créneau, lambda, profit total, bien-être total).
    """
    curves = []
    for m in models:
        grid = np.linspace(m.c, p_bar(m), n_grid)
        cv = price_curve(m, grid)
        curves.append((grid, cv["welfare"], cv["profit"]))

    def solve(lam):
        ps, W, P = [], 0.0, 0.0
        for grid, w, pr in curves:
            k = int(np.argmax(w + lam * pr))
            ps.append(grid[k]); W += w[k]; P += pr[k]
        return np.array(ps), W, P

    ps, W, P = solve(0.0)
    if P >= F:
        return ps, 0.0, P, W
    lo, hi = 0.0, 1.0
    while solve(hi)[2] < F and hi < 1e6:
        hi *= 2
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if solve(mid)[2] >= F:
            hi = mid
        else:
            lo = mid
    ps, W, P = solve(hi)
    return ps, hi, P, W
