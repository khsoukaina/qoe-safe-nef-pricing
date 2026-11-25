"""Fronts de Pareto.

- non_dominated : filtre exact des points non dominés (objectifs à maximiser) ;
- choco_pareto : front de Pareto exact d'un plan tarifaire sur 24 h, calculé avec le
  solveur de contraintes Choco (pychoco) par la méthode epsilon-contrainte :
  pour chaque seuil de revenu et de rejet, maximiser la QoE ; puis filtrage ;
- dp_pareto_2d : programmation dynamique exacte (QoE, revenu) pour valider Choco.

Le plan tarifaire choisit, pour chaque créneau t, un niveau de prix k parmi K niveaux.
Les tables (créneau x niveau) de QoE, revenu et rejets sont précalculées (espérances
Monte-Carlo) ; les objectifs sont additifs sur la journée.
"""
from __future__ import annotations

import numpy as np

try:
    from pychoco import Model as ChocoModel  # type: ignore
    HAS_CHOCO = True
except Exception:  # pragma: no cover
    HAS_CHOCO = False


def non_dominated(points: np.ndarray, maximize=None) -> np.ndarray:
    """Masque des points non dominés. maximize : liste de booléens par objectif."""
    P = np.asarray(points, float)
    sign = np.ones(P.shape[1]) if maximize is None else np.where(maximize, 1.0, -1.0)
    Q = P * sign
    keep = np.ones(len(Q), bool)
    for i in range(len(Q)):
        if not keep[i]:
            continue
        dom = np.all(Q >= Q[i], axis=1) & np.any(Q > Q[i], axis=1)
        if dom.any():
            keep[i] = False
    return keep


def _scaled(tab, scale):
    return np.rint(np.asarray(tab) * scale).astype(int)


def choco_pareto(mos, rev, rej, n_rev: int = 14, n_rej: int = 6, scale=(1000, 100, 1000), time_limit="20s",
                 log=None):
    """Front de Pareto (max QoE, max revenu, min rejets) d'un plan tarifaire.

    mos, rev, rej : tableaux (T, K). Retourne une liste de dicts {levels, mos, rev, rej}.
    log : liste optionnelle qui reçoit (seuil_revenu, seuil_rejets, optimum entier ou None)
    pour chaque problème epsilon-contrainte résolu.
    """
    if not HAS_CHOCO:
        raise ImportError("pychoco indisponible")
    T, K = mos.shape
    Mi, Ri, Ji = _scaled(mos, scale[0]), _scaled(rev, scale[1]), _scaled(rej, scale[2])
    rev_lo, rev_hi = Ri.min(axis=1).sum(), Ri.max(axis=1).sum()
    rej_lo, rej_hi = Ji.min(axis=1).sum(), Ji.max(axis=1).sum()
    sols = []
    for rj in ([rej_hi] if n_rej == 1 else np.linspace(rej_lo, rej_hi, n_rej)):
        for rv in np.linspace(rev_lo, rev_hi, n_rev):
            m = ChocoModel("plan")
            lv = [m.intvar(0, K - 1, f"k{t}") for t in range(T)]
            mv = [m.intvar(int(Mi[t].min()), int(Mi[t].max()), f"m{t}") for t in range(T)]
            rv_ = [m.intvar(int(Ri[t].min()), int(Ri[t].max()), f"r{t}") for t in range(T)]
            jv = [m.intvar(int(Ji[t].min()), int(Ji[t].max()), f"j{t}") for t in range(T)]
            for t in range(T):
                m.element(mv[t], [int(v) for v in Mi[t]], lv[t]).post()
                m.element(rv_[t], [int(v) for v in Ri[t]], lv[t]).post()
                m.element(jv[t], [int(v) for v in Ji[t]], lv[t]).post()
            tot_m = m.intvar(int(Mi.min(axis=1).sum()), int(Mi.max(axis=1).sum()), "M")
            m.sum(mv, "=", tot_m).post()
            m.sum(rv_, ">=", int(np.ceil(rv))).post()
            m.sum(jv, "<=", int(np.floor(rj))).post()
            s = m.get_solver().find_optimal_solution(tot_m, True, time_limit=time_limit)
            if log is not None:
                log.append((int(np.ceil(rv)), int(np.floor(rj)), None if s is None else s.get_int_val(tot_m)))
            if s is None:
                continue
            levels = [s.get_int_val(v) for v in lv]
            sols.append(levels)
    out, seen = [], set()
    for levels in sols:
        key = tuple(levels)
        if key in seen:
            continue
        seen.add(key)
        idx = (np.arange(T), np.array(levels))
        out.append({"levels": list(levels), "mos": float(mos[idx].sum()), "rev": float(rev[idx].sum()),
                    "rej": float(rej[idx].sum())})
    if not out:
        return out
    pts = np.array([[o["mos"], o["rev"], o["rej"]] for o in out])
    keep = non_dominated(pts, [True, True, False])
    return [o for o, k in zip(out, keep) if k]


def dp_pareto_2d(mos, rev, scale=100):
    """Front exact (max QoE, max revenu) par programmation dynamique sur le revenu entier."""
    T, K = mos.shape
    Ri = np.rint(np.asarray(rev) * scale).astype(int)
    best = {0: 0.0}
    for t in range(T):
        nb = {}
        for r0, m0 in best.items():
            for k in range(K):
                r1 = r0 + int(Ri[t, k]); m1 = m0 + float(mos[t, k])
                if m1 > nb.get(r1, -np.inf):
                    nb[r1] = m1
        best = nb
    pts = np.array(sorted((r / scale, m) for r, m in best.items()))
    keep = non_dominated(pts[:, ::-1])
    return pts[keep]


def dp_pareto_3d(Mi, Ri, Ji):
    """Ensemble exact des triplets entiers (QoE, revenu, rejets) non dominés atteignables,
    par programmation dynamique avec élagage de dominance. Sert à valider Choco."""
    Mi, Ri, Ji = (np.asarray(a, int) for a in (Mi, Ri, Ji))
    T, K = Mi.shape
    states = np.zeros((1, 3), int)                       # colonnes : mos, rev, rej
    for t in range(T):
        cand = (states[:, None, :] + np.stack([Mi[t], Ri[t], Ji[t]], 1)[None, :, :]).reshape(-1, 3)
        cand = np.unique(cand, axis=0)
        states = cand[non_dominated(cand, [True, True, False])]
    return states


def dp_eps_constraint(states, rev_min, rej_max):
    """Optimum exact de max QoE s.c. revenu >= rev_min et rejets <= rej_max (entiers)."""
    ok = (states[:, 1] >= rev_min) & (states[:, 2] <= rej_max)
    return int(states[ok, 0].max()) if ok.any() else None


def choco_eps_queries(mos, rev, rej, n_rev, n_rej, scale):
    """Seuils (revenu, rejets) entiers utilisés par choco_pareto (pour la validation)."""
    Ri, Ji = _scaled(rev, scale[1]), _scaled(rej, scale[2])
    rev_lo, rev_hi = Ri.min(axis=1).sum(), Ri.max(axis=1).sum()
    rej_lo, rej_hi = Ji.min(axis=1).sum(), Ji.max(axis=1).sum()
    out = []
    for rj in ([rej_hi] if n_rej == 1 else np.linspace(rej_lo, rej_hi, n_rej)):
        for rv in np.linspace(rev_lo, rev_hi, n_rev):
            out.append((int(np.ceil(rv)), int(np.floor(rj))))
    return out
