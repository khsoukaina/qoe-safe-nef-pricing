"""Correctif T2 : quantile conforme (garantie sans hypothèse de loi).

Si les demandes normalisées x_1, ..., x_n, x_{n+1} sont échangeables (par exemple i.i.d.),
q = x_(k) avec k = ceil((n + 1)(1 - eps)) vérifie  P(x_{n+1} > q) <= eps  en échantillon fini.
Comme la charge d'équilibre croît avec x, le prix p = p_clr(q) donne P(surcharge) <= eps.
Il faut k <= n, c'est-à-dire n >= 1/eps - 1 : avec moins d'historique, aucune garantie n'est
possible et la fonction renvoie +inf (le NEF ferme la QoS).

AdaptiveConformal : version adaptative (Gibbs et Candès, 2021) pour une demande qui dérive ;
garantie sur la fréquence moyenne de dépassement à long terme.
"""
from __future__ import annotations

import numpy as np


def conformal_quantile(history, eps: float) -> float:
    h = np.sort(np.asarray(history, float))
    n = len(h)
    if n == 0:
        return np.inf
    k = int(np.ceil((n + 1) * (1.0 - eps)))
    if k > n:
        return np.inf
    return float(h[max(k, 1) - 1])


def min_history(eps: float) -> int:
    """Taille d'historique minimale pour que le quantile conforme soit fini."""
    return int(np.ceil(1.0 / eps - 1.0))


class AdaptiveConformal:
    """Quantile conforme adaptatif : alpha_{t+1} = alpha_t + gamma (eps - 1{x_t > q_t})."""

    def __init__(self, eps: float, gamma: float = 0.01, window: int = None):
        self.eps, self.gamma, self.window = eps, gamma, window
        self.alpha = eps
        self.hist = []
        self.errors = []

    def quantile(self) -> float:
        h = self.hist[-self.window:] if self.window else self.hist
        if self.alpha >= 1.0:
            return -np.inf
        return conformal_quantile(h, max(self.alpha, 1e-9))

    def update(self, x: float, q: float):
        err = float(x > q)
        self.errors.append(err)
        self.alpha += self.gamma * (self.eps - err)
        self.hist.append(float(x))
