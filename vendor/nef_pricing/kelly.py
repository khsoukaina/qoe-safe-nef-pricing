"""Mode enchère : mécanisme proportionnel de Kelly avec externalité best-effort.

La NEF met en vente K unités de ressource (Mbit/s de réservation). L'AF i mise m_i,
reçoit r_i = m_i K / sum_j m_j unités, soit y_i = r_i / b_i sessions M2.

Neutralisation (Lemme) : si le rapport (b_i - a_i)/b_i est commun à toutes les AF,
la charge L = sum a_i X_i + ((b-a)/b) K ne dépend pas du partage : l'externalité ne
fausse pas la concurrence. L'équilibre est celui du jeu de Kelly standard aux
utilités U_i(r) = theta_i [f_i(0; L_K) - f_i(r/b_i; L_K)] (Johari-Tsitsiklis).

Fonctions :
- kelly_equilibrium : équilibre price-anticipating (condition U_i'(r_i)(1 - r_i/K) = p) ;
- gross_gain, efficiency_terms : S(K), E(K)+coût, xi ; borne (3-4 xi)/(4(1-xi)) ;
- optimal_volume : K* = argmax_K OPT(K) ;
- identify_theta : identification exacte des theta_i à partir des mises observées ;
- tight_instance : famille qui atteint la borne (Proposition d'atteinte).
"""
from __future__ import annotations

import warnings

import numpy as np
from scipy.optimize import brentq, minimize_scalar

from .model import Model


def _check_ratio(model: Model):
    r = model.Delta / model.b
    if np.ptp(r) > 1e-9:
        warnings.warn("(b_i - a_i)/b_i non commun : la neutralisation de l'externalité ne s'applique pas exactement.")
    return float(r.mean())


def load_at_volume(model: Model, K: float) -> float:
    return model.base_load() + _check_ratio(model) * K


def marginal_utility(model: Model, r, K: float):
    """U_i'(r_i) à la charge L_K (en u.m. par unité de ressource)."""
    L = load_at_volume(model, K)
    y = np.asarray(r, float) / model.b
    return model.theta * model.degr.D(L) * model.Qv(y) / model.b


def kelly_equilibrium(model: Model, K: float, bid_scale=None):
    """Équilibre de Kelly (AF anticipant le prix). bid_scale : facteur appliqué à la
    valeur perçue par chaque AF (pour simuler des AF qui biaisent leurs mises).
    Retourne (y, prix unitaire p, mises m)."""
    scale = np.ones(model.N) if bid_scale is None else np.asarray(bid_scale, float)
    rmax = np.minimum(model.b * model.X, K)

    def r_of(p):
        out = np.zeros(model.N)
        for i in range(model.N):
            def h(r):
                z = np.zeros(model.N); z[i] = r
                return scale[i] * marginal_utility(model, z, K)[i] * (1 - r / K) - p
            if h(0.0) <= 0:
                continue
            out[i] = brentq(h, 0.0, rmax[i] * (1 - 1e-12)) if h(rmax[i] * (1 - 1e-12)) < 0 else rmax[i]
        return out

    u0 = scale * marginal_utility(model, np.zeros(model.N), K)
    if K <= 0 or u0.max() <= 0:
        return np.zeros(model.N), 0.0, np.zeros(model.N)
    total = lambda p: r_of(p).sum() - K
    if total(1e-12 * u0.max()) < 0:          # volume supérieur à ce que les AF veulent
        r = r_of(1e-12 * u0.max())
        return r / model.b, 0.0, np.zeros(model.N)
    p = brentq(total, 1e-12 * u0.max(), u0.max() * (1 - 1e-12), xtol=1e-14 * u0.max())
    r = r_of(p)
    return r / model.b, p, p * r


def kelly_reserve(model: Model, K: float, reserve: float = None, tol: float = 1e-3):
    """Enchère avec prix de réserve : la NEF ne vend pas sous le coût marginal.
    Si le prix émergent est inférieur à la réserve, la NEF réduit le volume jusqu'à ce que
    le prix émergent atteigne la réserve (le prix décroît avec le volume).
    Retourne (y, p, mises, volume effectif)."""
    reserve = model.c if reserve is None else reserve
    y, p, m = kelly_equilibrium(model, K)
    if p >= reserve or K <= 0:
        return y, p, m, K
    lo, hi = 0.0, K
    if kelly_equilibrium(model, K * 1e-3)[1] < reserve:
        return np.zeros(model.N), reserve, np.zeros(model.N), 0.0
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if kelly_equilibrium(model, mid)[1] >= reserve:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol * K:
            break
    y, p, m = kelly_equilibrium(model, lo)
    return y, p, m, lo


def gross_gain(model: Model, y, K: float) -> float:
    """Gain brut à charge fixée L_K : sum_i theta_i [f_i(0; L_K) - f_i(y; L_K)]."""
    L = load_at_volume(model, K)
    return float(model.theta @ (model.loss(np.zeros(model.N), L) - model.loss(y, L)))


def best_split(model: Model, K: float):
    """S(K) : meilleur partage d'un volume K (égalisation des utilités marginales)."""
    if K <= 0:
        return np.zeros(model.N), 0.0
    cap = model.b * model.X

    def r_of(lam):
        out = np.zeros(model.N)
        for i in range(model.N):
            def h(r):
                z = np.zeros(model.N); z[i] = r
                return marginal_utility(model, z, K)[i] - lam
            if h(0.0) <= 0:
                continue
            out[i] = cap[i] if h(cap[i] * (1 - 1e-12)) > 0 else brentq(h, 0.0, cap[i] * (1 - 1e-12))
        return out

    hi = float(marginal_utility(model, np.zeros(model.N), K).max())
    if r_of(0.0).sum() <= K:
        r = r_of(0.0)
    else:
        lam = brentq(lambda l: r_of(l).sum() - K, 0.0, hi)
        r = r_of(lam)
    y = r / model.b
    return y, gross_gain(model, y, K)


def net_opt(model: Model, K: float):
    """OPT(K) = S(K) - E(K) - coût, avec le partage optimal."""
    y, S = best_split(model, K)
    return y, model.welfare(y), S


def optimal_volume(model: Model, K_max: float = None):
    """K* = argmax_K OPT(K) sur [0, capacité M2 en unités de ressource]."""
    ratio = _check_ratio(model)
    K_cap = model.m2_capacity() / ratio
    K_max = K_cap if K_max is None else min(K_max, K_cap)
    K_max = min(K_max, float(model.b @ model.X))
    grid = np.linspace(K_max / 40, K_max, 40)
    vals = [net_opt(model, K)[1] for K in grid]
    k = int(np.argmax(vals))
    a, b = grid[max(k - 1, 0)], grid[min(k + 1, len(grid) - 1)]
    res = minimize_scalar(lambda K: -net_opt(model, K)[1], bounds=(a, b), method="bounded")
    return (res.x if -res.fun >= vals[k] else grid[k])


def efficiency_terms(model: Model, K: float):
    """Retourne dict : S (gain brut optimal), W_opt, xi, et à l'équilibre de Kelly : W_ne, ratio, bound."""
    y_opt, W_opt, S = net_opt(model, K)
    xi = (S - W_opt) / S if S > 0 else np.nan
    y_ne, p, m = kelly_equilibrium(model, K)
    W_ne = model.welfare(y_ne)
    return {"K": K, "S": S, "W_opt": W_opt, "xi": xi, "W_ne": W_ne,
            "ratio": W_ne / W_opt if W_opt > 0 else np.nan,
            "bound": bound(xi), "price": p, "y_ne": y_ne, "y_opt": y_opt,
            "gross_eff": gross_gain(model, y_ne, K) / S if S > 0 else np.nan}


def bound(xi):
    xi = np.asarray(xi, float)
    return (3 - 4 * xi) / (4 * (1 - xi))


def identify_theta(model: Model, y, p: float, K: float):
    """Proposition d'identification : theta_i = p / [D_i(L_K) Q(v_i) / b_i (1 - r_i/K)].
    Retourne NaN pour les AF non intérieures (inactive, saturée, ou prix nul) : seule une
    borne est alors identifiable."""
    L = load_at_volume(model, K)
    y = np.asarray(y, float)
    r = y * model.b
    den = model.degr.D(L) * model.Qv(y) / model.b * (1 - r / K)
    interior = (y > 1e-9) & (y < model.X * (1 - 1e-7)) & (p > 0)
    th = np.where(interior, p / np.where(interior, den, 1.0), np.nan)
    return th


def tight_instance(n: int, eta: float = 1e-6):
    """Famille de la Proposition d'atteinte : 1 AF de pente 1, n AF de pente 1/2,
    volume normalisé à 1 et X_i = 1/eta (régime quasi linéaire).
    Retourne l'efficacité brute à l'équilibre et la formule exacte (3n+2)/(4n+2)."""
    A = np.r_[1.0, np.full(n, 0.5)]
    X = np.full(n + 1, 1.0 / eta)

    def r_of(p):
        out = np.zeros(n + 1)
        for i in range(n + 1):
            if A[i] > p:
                out[i] = brentq(lambda r: A[i] * (1 - r / X[i]) * (1 - r) - p, 0.0, 1.0)
        return out

    p = brentq(lambda p: r_of(p).sum() - 1.0, 1e-14, 1 - 1e-14)
    r = r_of(p)
    G = float(np.sum(A * (r - r ** 2 / (2 * X))))
    S = 1.0 * (1 - 1 / (2 * X[0]))
    return G / S, (3 * n + 2) / (4 * n + 2)
