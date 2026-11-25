"""Correctif T1 : tarification « QoE-safe » et test de gain marginal.

Test de gain marginal (dérivée du MOS total en y = 0) :
    g_i = D_i(L0) Q_i(1) - Delta_i * sum_j D_j'(L0) X_j M_j(1),   L0 = sum_j a_j X_j
g_i > 0 : vendre un peu de QoS garantie à l'AF i augmente le MOS total (le gain des sessions
qui basculent dépasse la dégradation imposée au best-effort) ; g_i < 0 : il le diminue.

Prix QoE-safe : parmi les prix candidats, le NEF retient celui qui maximise le bien-être
espéré sous la contrainte  MOS moyen espéré >= MOS moyen sans NEF - eta  (et, en option,
prix >= prix quantile, pour borner la surcharge). Si aucun prix ne respecte la contrainte,
le NEF ferme la QoS sur ce créneau (prix infini).
"""
from __future__ import annotations

import numpy as np

from . import admission, metrics


def marginal_gain(model) -> np.ndarray:
    """g_i (points de MOS par session basculée), évalué à y = 0."""
    y0 = np.zeros(model.N)
    L0 = model.base_load()
    D = np.asarray(model.degr.D(L0), float)
    dD = np.asarray(model.degr.dD(L0), float)
    Q1 = model.Qv(y0)
    M1 = model.Mv(y0)
    return D * Q1 - model.Delta * float(np.sum(dD * model.X * M1))


def marginal_gain_fd(model, i: int, h: float = 1e-4) -> float:
    """Même quantité par différence finie (pour les tests)."""
    y = np.zeros(model.N)
    y[i] = h * model.X[i]
    return (model.mos_total(y) - model.mos_total(np.zeros(model.N))) / y[i]


def slot_scores(s, p, xs, rationing: str = "priority") -> dict:
    """Moyennes sur les tirages de demande xs d'un créneau (surface s).
    p = None : sans NEF (aucune QoS vendue)."""
    acc = []
    for x in xs:
        m = s.model.with_X(s.model.X * x)
        if p is None:
            out = admission.Outcome(np.inf, np.zeros(m.N), np.zeros(m.N), m.base_load())
            r = metrics.summarize(m, out, 0.0)
        else:
            out = admission.admit(m, s.y(p, x), p, rationing)
            r = metrics.summarize(m, out)
        acc.append([r["mos_mean"], r["welfare"], r["profit"], r["rejection_rate"]])
    a = np.mean(np.asarray(acc, float), axis=0)
    return {"mos": float(a[0]), "welfare": float(a[1]), "profit": float(a[2]), "reject": float(a[3])}


def qoe_safe_price(s, xs, eta: float = 0.0, rationing: str = "priority", p_min: float = None, grid=None) -> float:
    """Prix QoE-safe d'un créneau.

    xs     : tirages de demande de *conception* (prévision), distincts de ceux de l'évaluation ;
    eta    : tolérance sur le MOS moyen (points de MOS) ;
    p_min  : prix plancher optionnel (par exemple le prix quantile, pour borner la surcharge) ;
    retour : prix retenu, ou np.inf si la QoS doit être fermée sur ce créneau.
    """
    base = slot_scores(s, None, xs, rationing)["mos"]
    grid = np.asarray(s.prices if grid is None else grid, float)
    if p_min is not None:
        grid = grid[grid >= p_min * (1 - 1e-9)]
        if p_min <= s.prices[-1]:
            grid = np.unique(np.r_[p_min, grid])
    best_p, best_w = np.inf, 0.0          # fermer : bien-être 0 et MOS = MOS sans NEF
    for p in grid:
        sc = slot_scores(s, float(p), xs, rationing)
        if sc["mos"] >= base - eta - 1e-12 and sc["welfare"] > best_w:
            best_p, best_w = float(p), sc["welfare"]
    return best_p


# ------------------------------------------------------------------------------------------
# Version sans paramètre arbitraire (correctif de eta)
#
# Le NEF évalue une seule fois, pour chaque prix candidat, l'espérance de quatre indicateurs
# (MOS moyen, MOS du 5e centile, bien-être, profit) puis choisit selon la contrainte voulue :
#   "mean" : MOS moyen      >= MOS moyen sans NEF      - eta_mean
#   "p5"   : MOS 5e centile >= MOS 5e centile sans NEF - eta_p5
#   "both" : les deux (garantie de non-régression pour la moyenne ET pour les plus mal servis)
# Avec eta = 0, la règle n'a AUCUN paramètre libre : c'est la stratégie de référence du papier.
# Les valeurs eta > 0 servent seulement à tracer la frontière de politique (prix de la QoE).
# ------------------------------------------------------------------------------------------
def score_table(s, xs, rationing: str = "priority", p_min: float = None, n_fine: int = 60) -> dict:
    """Indicateurs espérés pour une grille fine de prix (grille de la surface + n_fine prix log)."""
    lo = s.model.c if p_min is None else max(p_min, s.model.c)
    hi = float(s.prices[-1])
    grid = np.unique(np.r_[[p for p in s.prices if p >= lo], np.geomspace(max(lo, 1e-9), hi, n_fine)])
    rows = []
    for p in grid:
        acc = []
        for x in xs:
            m = s.model.with_X(s.model.X * x)
            out = admission.admit(m, s.y(float(p), x), float(p), rationing)
            r = metrics.summarize(m, out)
            acc.append([r["mos_mean"], r["mos_p5"], r["welfare"], r["profit"], r["rejection_rate"]])
        rows.append(np.mean(np.asarray(acc, float), axis=0))
    base = []
    for x in xs:
        m = s.model.with_X(s.model.X * x)
        out = admission.Outcome(np.inf, np.zeros(m.N), np.zeros(m.N), m.base_load())
        r = metrics.summarize(m, out, 0.0)
        base.append([r["mos_mean"], r["mos_p5"]])
    base = np.mean(np.asarray(base, float), axis=0)
    R = np.asarray(rows)
    return {"price": grid, "mos": R[:, 0], "p5": R[:, 1], "welfare": R[:, 2], "profit": R[:, 3], "reject": R[:, 4],
            "base_mos": float(base[0]), "base_p5": float(base[1])}


def select_price(tab: dict, constraint: str = "both", eta_mean: float = 0.0, eta_p5: float = 0.0) -> float:
    """Prix qui maximise le bien-être espéré sous la contrainte choisie ; np.inf = fermer la QoS."""
    ok = np.ones(len(tab["price"]), bool)
    if constraint in ("mean", "both"):
        ok &= tab["mos"] >= tab["base_mos"] - eta_mean - 1e-12
    if constraint in ("p5", "both"):
        ok &= tab["p5"] >= tab["base_p5"] - eta_p5 - 1e-12
    ok &= tab["welfare"] > 0
    if not ok.any():
        return np.inf
    i = int(np.argmax(np.where(ok, tab["welfare"], -np.inf)))
    return float(tab["price"][i])
