"""Niveau suiveurs : équilibre de Nash (généralisé) entre AF.

- best_response : meilleure réponse d'une AF (condition du premier ordre, ou
  minimisation directe si la dégradation n'est pas linéaire) ;
- nash : itération de Gauss-Seidel des meilleures réponses (converge de façon
  monotone dans le jeu supermodulaire, Théorème de complémentarité) ;
- extragradient : méthode de projection pour l'inégalité variationnelle (contrôle) ;
- variational_equilibrium : équilibre avec contrainte de capacité ; le multiplicateur
  commun mu est le prix ombre (Corollaire de décentralisation) ;
- kkt_residual, is_unique : diagnostics.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import brentq, minimize_scalar

from .model import LinearDegradation, Model


def _br_scalar(model: Model, y: np.ndarray, i: int, p_i: float) -> float:
    Xi = model.X[i]
    if Xi <= 0:
        return 0.0

    def g(yi):
        z = y.copy()
        z[i] = yi
        return model.grad(z, p_i)[i]

    if isinstance(model.degr, LinearDegradation):
        g0, g1 = g(0.0), g(Xi)
        if g0 >= 0:
            return 0.0
        if g1 <= 0:
            return Xi
        return brentq(g, 0.0, Xi, xtol=1e-10)

    def c(yi):
        z = y.copy()
        z[i] = yi
        return model.cost(z, p_i)[i]

    res = minimize_scalar(c, bounds=(0.0, Xi), method="bounded", options={"xatol": 1e-7 * max(Xi, 1)})
    cand = [0.0, Xi, res.x]
    return float(min(cand, key=c))


def best_response(model: Model, y, i: int, p) -> float:
    p_vec = np.broadcast_to(np.asarray(p, float), (model.N,))
    return _br_scalar(model, np.asarray(y, float).copy(), i, float(p_vec[i]))


def nash(model: Model, p, y0=None, tol: float = 1e-9, maxit: int = 2000, info: bool = False):
    """Équilibre de Nash du jeu régulé par le prix (sans contrainte dure de capacité)."""
    p_vec = np.broadcast_to(np.asarray(p, float), (model.N,)).astype(float)
    y = np.zeros(model.N) if y0 is None else np.clip(np.asarray(y0, float).copy(), 0, model.X)
    it = 0
    for it in range(1, maxit + 1):
        y_old = y.copy()
        for i in range(model.N):
            y[i] = _br_scalar(model, y, i, p_vec[i])
        if np.max(np.abs(y - y_old)) < tol * max(1.0, model.X.max()):
            break
    if info:
        return y, {"iterations": it, "residual": kkt_residual(model, y, p_vec)}
    return y


def extragradient(model: Model, p, y0=None, step: float = None, tol: float = 1e-8, maxit: int = 20000):
    """Méthode d'extragradient (Korpelevich) pour VI(box, F). Sert de contrôle croisé."""
    p_vec = np.broadcast_to(np.asarray(p, float), (model.N,)).astype(float)
    y = np.zeros(model.N) if y0 is None else np.asarray(y0, float).copy()
    if step is None:
        step = 0.5 / max(1e-12, np.max(model.theta * model.degr.D(model.R) / model.X))
    proj = lambda z: np.clip(z, 0.0, model.X)
    for _ in range(maxit):
        z = proj(y - step * model.grad(y, p_vec))
        y_new = proj(y - step * model.grad(z, p_vec))
        if np.max(np.abs(y_new - y)) < tol * max(1.0, model.X.max()):
            return y_new
        y = y_new
    return y


def kkt_residual(model: Model, y, p) -> float:
    """Résidu naturel ||y - proj(y - F(y))||_inf (nul à l'équilibre)."""
    y = np.asarray(y, float)
    r = y - np.clip(y - model.grad(y, p), 0.0, model.X)
    return float(np.max(np.abs(r)))


def is_unique(model: Model, p, starts=(0.0, 0.5, 1.0), tol: float = 1e-5) -> bool:
    sols = [nash(model, p, y0=model.X * s) for s in starts]
    return max(np.max(np.abs(sols[0] - s)) for s in sols) < tol * max(1.0, model.X.max())


def variational_equilibrium(model: Model, p: float, mu_max: float = None, tol: float = 1e-7):
    """Équilibre variationnel avec la contrainte de capacité.

    Retourne (y, mu) où mu est le prix ombre par unité de surcoût Delta.
    Les AF voient le prix effectif p + mu * Delta_i / b_i (Corollaire de décentralisation).
    """
    y = nash(model, p)
    if model.load(y) <= model.R + 1e-9:
        return y, 0.0
    if model.m2_capacity() <= 0:
        return np.zeros(model.N), np.inf

    def load_at(mu):
        return model.load(nash(model, p + mu * model.Delta / model.b, y0=y)) - model.R

    hi = 1.0 if mu_max is None else mu_max
    while load_at(hi) > 0:
        hi *= 2.0
        if hi > 1e9:
            break
    mu = brentq(load_at, 0.0, hi, xtol=tol)
    return nash(model, p + mu * model.Delta / model.b), mu


def demand_load(model: Model, p) -> float:
    """Charge demandée à l'équilibre sans rationnement (L^NE(p, X))."""
    return model.load(nash(model, p))
