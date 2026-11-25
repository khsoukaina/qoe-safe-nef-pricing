"""Modèle système : AF, charge, dégradation best-effort, pertes de MOS, coûts.

Notations (voir le document de cadrage) :
    X_i      sessions actives de l'AF i
    y_i      sessions basculées en QoS garantie (M2) via la NEF
    a_i,b_i  ressource par session M1 / M2 (Mbit/s) ; Delta_i = b_i - a_i
    L        charge = sum_i a_i X_i + Delta_i y_i ; capacité R
    D_i(L)   dégradation best-effort d'une session de sensibilité 1
    f_i(y)   perte de MOS des sessions M1 de l'AF i = D_i(L) X_i M_i(1 - y_i/X_i)
             (M_i = moyenne partielle de la loi de sensibilité ; uniforme : v^2/2)
    C_i      coût de l'AF = p b_i y_i + theta_i f_i(y)

Le modèle accepte des a_i, b_i par classe. La neutralisation de l'externalité dans
l'enchère de Kelly exige un rapport (b_i - a_i)/b_i commun (voir kelly.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, Optional, Sequence

import numpy as np
from scipy import special, stats


# ---------------------------------------------------------------- sensibilités
class Sensitivity:
    """Loi de la sensibilité sigma des sessions, sur [0, 1].

    Q(v) : fonction quantile ; M(v) = int_0^v Q(u) du (moyenne partielle).
    Une AF bascule d'abord ses sessions les plus sensibles : si elle garde la part
    v = 1 - y/X en best-effort, ces sessions ont les quantiles [0, v].
    """

    def __init__(self, kind: str = "uniform", alpha: float = 1.0, beta: float = 1.0):
        self.kind, self.alpha, self.beta = kind, alpha, beta
        if kind == "uniform":
            self.alpha = self.beta = 1.0

    def Q(self, v):
        v = np.clip(v, 0.0, 1.0)
        if self.kind == "uniform":
            return v
        return stats.beta.ppf(v, self.alpha, self.beta)

    def M(self, v):
        v = np.clip(v, 0.0, 1.0)
        if self.kind == "uniform":
            return v * v / 2.0
        s = self.Q(v)
        a, b = self.alpha, self.beta
        return a / (a + b) * special.betainc(a + 1.0, b, s)

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    def __repr__(self):
        return f"Sensitivity({self.kind}, {self.alpha}, {self.beta})"


# ---------------------------------------------------------------- dégradation
class LinearDegradation:
    """D_i(L) = delta_i + kappa_i L / R (linéarisation locale d'un modèle QoE).

    floor : si non None, D_i(L) = max(delta_i + kappa_i L / R, floor_i) (forme « en crosse »
    obtenue quand la linéarisation est faite dans la zone de congestion, delta_i < 0 ;
    floor_i = écart intrinsèque garanti / best-effort mesuré à faible charge).
    """

    def __init__(self, delta, kappa, R, floor=None):
        self.delta = np.asarray(delta, float)
        self.kappa = np.asarray(kappa, float)
        self.R = float(R)
        self.floor = None if floor is None else np.broadcast_to(np.asarray(floor, float), self.delta.shape)

    @property
    def smooth(self) -> bool:
        return self.floor is None

    def D(self, L):
        d = self.delta + self.kappa * L / self.R
        return d if self.floor is None else np.maximum(d, self.floor)

    def dD(self, L):
        g = self.kappa / self.R
        if self.floor is None:
            return g
        return np.where(self.delta + self.kappa * L / self.R > self.floor, g, 0.0)


class CurveDegradation:
    """D_i(L) donnée par des courbes (issues des modèles QoE normalisés ou du banc).

    curves : liste de (L_grid, D_values) par AF ; interpolation linéaire par morceaux,
    extrapolation linéaire aux bords.
    """

    def __init__(self, curves: Sequence[tuple]):
        self.curves = [(np.asarray(Lg, float), np.asarray(Dv, float)) for Lg, Dv in curves]

    def D(self, L):
        out = []
        for Lg, Dv in self.curves:
            if L <= Lg[0]:
                s = (Dv[1] - Dv[0]) / (Lg[1] - Lg[0])
                out.append(Dv[0] + s * (L - Lg[0]))
            elif L >= Lg[-1]:
                s = (Dv[-1] - Dv[-2]) / (Lg[-1] - Lg[-2])
                out.append(Dv[-1] + s * (L - Lg[-1]))
            else:
                out.append(np.interp(L, Lg, Dv))
        return np.maximum(np.array(out), 0.0)

    def dD(self, L, h=1e-3):
        return (self.D(L + h) - self.D(L - h)) / (2 * h)


# ---------------------------------------------------------------- scénario
@dataclass
class Model:
    """Un créneau : N AF, capacité R, coût c."""

    names: list
    X: np.ndarray            # sessions actives
    a: np.ndarray            # Mbit/s par session best-effort
    b: np.ndarray            # Mbit/s par session garantie
    q2: np.ndarray           # MOS garanti en M2
    theta: np.ndarray        # u.m. par point de MOS, par session et par créneau
    R: float                 # capacité (Mbit/s)
    c: float = 0.0           # coût marginal par Mbit/s réservé et par créneau
    degr: object = None      # LinearDegradation ou CurveDegradation
    sens: list = field(default_factory=list)  # Sensitivity par AF

    def __post_init__(self):
        for k in ("X", "a", "b", "q2", "theta"):
            setattr(self, k, np.asarray(getattr(self, k), float))
        if not self.sens:
            self.sens = [Sensitivity() for _ in self.names]

    # ---------- dimensions
    @property
    def N(self) -> int:
        return len(self.names)

    @property
    def Delta(self) -> np.ndarray:
        return self.b - self.a

    def with_X(self, X) -> "Model":
        return replace(self, X=np.asarray(X, float))

    # ---------- charge et capacité
    def load(self, y) -> float:
        return float(self.a @ self.X + self.Delta @ np.asarray(y, float))

    def base_load(self) -> float:
        return float(self.a @ self.X)

    def m2_capacity(self) -> float:
        """Ressource disponible pour le surcoût des sessions M2 : R - sum a_i X_i."""
        return self.R - self.base_load()

    # ---------- pertes de MOS
    def v(self, y):
        return np.clip(1.0 - np.asarray(y, float) / self.X, 0.0, 1.0)

    def Mv(self, y):
        v = self.v(y)
        return np.array([s.M(vi) for s, vi in zip(self.sens, v)])

    def Qv(self, y):
        v = self.v(y)
        return np.array([s.Q(vi) for s, vi in zip(self.sens, v)])

    def loss(self, y, L: Optional[float] = None):
        """f_i(y) pour tout i (perte de MOS agrégée des sessions M1)."""
        L = self.load(y) if L is None else L
        return self.degr.D(L) * self.X * self.Mv(y)

    def dloss_own(self, y):
        """d f_i / d y_i (en tenant compte de l'effet de y_i sur L)."""
        L = self.load(y)
        return self.degr.dD(L) * self.Delta * self.X * self.Mv(y) - self.degr.D(L) * self.Qv(y)

    def cost(self, y, p):
        """C_i(y; p) ; p scalaire ou vecteur (prix personnalisés)."""
        y = np.asarray(y, float)
        return p * self.b * y + self.theta * self.loss(y)

    def grad(self, y, p):
        """Pseudo-gradient F_i = d C_i / d y_i."""
        return p * self.b + self.theta * self.dloss_own(y)

    # ---------- objectifs du leader
    def welfare(self, y) -> float:
        """Objectif A : gain de bien-être pondéré par rapport à l'absence d'exposition."""
        y = np.asarray(y, float)
        f0 = self.loss(np.zeros(self.N))
        return float(self.theta @ (f0 - self.loss(y)) - self.c * (self.b @ y))

    def mos_total(self, y) -> float:
        """Objectif B : MOS total (sessions M1 + M2)."""
        return float(self.q2 @ self.X - self.loss(y).sum())

    def mos_mean(self, y) -> float:
        return self.mos_total(y) / self.X.sum()

    def revenue(self, y, p) -> float:
        return float(np.sum(p * self.b * np.asarray(y, float)))


def linear_model(names, X, a, b, q2, theta, delta, kappa, R, c=0.0, sens=None) -> Model:
    """Constructeur pratique pour le modèle linéaire du document de cadrage."""
    m = Model(names=list(names), X=X, a=a, b=b, q2=q2, theta=theta, R=R, c=c,
              degr=LinearDegradation(delta, kappa, R), sens=sens or [])
    return m


def symmetric_model(N, X_total, a=1.0, b=3.0, R=1000.0, delta=2.0, kappa=1.0,
                    theta=4.0, q2=4.5, c=0.5) -> Model:
    """Instance symétrique utilisée dans les rapports (vérification des théorèmes)."""
    return linear_model([f"AF{i}" for i in range(N)], np.full(N, X_total / N),
                        np.full(N, a), np.full(N, b), np.full(N, q2), np.full(N, theta),
                        np.full(N, delta), np.full(N, kappa), R, c)
