"""Réalisation d'un créneau : demande des AF, admission par la NEF, rationnement.

À un prix affiché p, les AF demandent y^NE(p) (équilibre sans contrainte). Si la
demande dépasse la capacité, la NEF rejette une partie des requêtes :
- 'random'   : premier arrivé, premier servi ; chaque requête a la même chance ;
- 'priority' : la NEF admet d'abord les sessions de plus forte sensibilité par unité
               de surcoût (signalée par l'AF, appliquée via l'ARP de la QoS 5G).
Les sessions rejetées restent en best-effort.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import brentq

from .followers import nash
from .model import Model


@dataclass
class Outcome:
    p: object
    y_demand: np.ndarray
    y_served: np.ndarray
    load: float
    rationing: str = "none"
    phi: float = 1.0                       # part servie (rationnement aléatoire)
    u_min: np.ndarray = None               # seuil de position admise (priorité)
    extra: dict = field(default_factory=dict)

    @property
    def rejected(self) -> float:
        return float(np.sum(self.y_demand - self.y_served))

    @property
    def rejection_rate(self) -> float:
        d = float(np.sum(self.y_demand))
        return self.rejected / d if d > 0 else 0.0


def admit(model: Model, y_demand, p, rationing: str = "random") -> Outcome:
    y_demand = np.asarray(y_demand, float)
    cap = model.m2_capacity()
    need = float(model.Delta @ y_demand)
    if need <= cap + 1e-9:
        return Outcome(p, y_demand, y_demand.copy(), model.load(y_demand), "none",
                       1.0, 1.0 - y_demand / model.X)
    if cap <= 0:
        z = np.zeros(model.N)
        return Outcome(p, y_demand, z, model.load(z), rationing, 0.0, np.ones(model.N))
    if rationing == "random":
        phi = cap / need
        y = phi * y_demand
        return Outcome(p, y_demand, y, model.load(y), "random", phi, 1.0 - y_demand / model.X)
    # priorité : seuil tau sur Q(u) * D_i(R) / Delta_i
    D = model.degr.D(model.R)
    v_d = 1.0 - y_demand / model.X           # positions demandeuses : u >= v_d

    def served(tau):
        out = np.zeros(model.N)
        u_min = np.zeros(model.N)
        for i, s in enumerate(model.sens):
            target = tau * model.Delta[i] / max(D[i], 1e-12)
            if target >= s.Q(1.0):
                u_min[i] = 1.0
                continue
            u_thr = 0.0 if target <= 0 else brentq(lambda u: s.Q(u) - target, 0.0, 1.0)
            u_min[i] = max(v_d[i], u_thr)
            out[i] = model.X[i] * (1.0 - u_min[i])
        return out, u_min

    f = lambda tau: model.Delta @ served(tau)[0] - cap
    hi = float(np.max(D / model.Delta)) * 1.0001
    tau = brentq(f, 0.0, hi) if f(0.0) > 0 else 0.0
    y, u_min = served(tau)
    return Outcome(p, y_demand, y, model.load(y), "priority", 1.0, u_min)


def realize(model: Model, p, rationing: str = "random", y0=None) -> Outcome:
    """Créneau complet au prix affiché p (scalaire ou par AF)."""
    y_d = nash(model, p, y0=y0)
    return admit(model, y_d, p, rationing)


def realize_kelly(model: Model, y_alloc) -> Outcome:
    """Créneau en mode enchère : l'allocation de Kelly est servie telle quelle."""
    y = np.asarray(y_alloc, float)
    return Outcome("kelly", y, y.copy(), model.load(y), "none", 1.0, 1.0 - y / model.X)
