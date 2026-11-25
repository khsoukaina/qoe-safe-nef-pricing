"""Calibration : des modèles QoE normalisés aux paramètres (delta_i, kappa_i) du modèle.

Procédure (identique à celle du banc, étape 13, avec ici une QoS simulée) :
1. pour plusieurs niveaux de demande et de parts de sessions garanties, calculer la QoS
   best-effort (netqos) puis le MOS par classe (qoe_models) ;
2. D_i(L) = q2_i - MOS_BE_i(L), q2_i = MOS obtenu avec la réservation GBR ;
3. ajuster D_i(L) ~ delta_i + kappa_i L / R (moindres carrés) ; conserver aussi la
   courbe moyenne D_i(L) pour les tests de robustesse (QoE non linéaire).
"""
from __future__ import annotations

import json
import os

import numpy as np
import yaml

from . import netqos, qoe_models
from .demand import class_profiles
from .model import CurveDegradation, LinearDegradation, Model

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_params(path: str = None) -> dict:
    path = path or os.path.join(ROOT, "config", "params.yaml")
    with open(path) as f:
        return yaml.safe_load(f)


def gbr_qos(P: dict, b_i: float, L_gbr: float) -> dict:
    net = P["network"]
    rho_g = min(L_gbr / P["capacity_R"], 0.999)
    wait = net["service_ms"] / (1 - rho_g)
    d = net["base_delay_ms"] + wait
    return {"throughput": b_i, "delay": d, "rtt": 2 * d, "loss": 0.0}


def sweep(P: dict, levels=(0.3, 0.5, 0.7, 0.85, 1.0), shares=np.linspace(0, 1, 21)):
    cls = P["classes"]
    a = np.array([c["a"] for c in cls]); b = np.array([c["b"] for c in cls])
    Xp = np.array([c["X_peak"] for c in cls], float)
    R = P["capacity_R"]; net = P["network"]
    rows = []
    for lev in levels:
        X = lev * Xp
        for g in shares:
            y = g * X
            L_gbr, L_be = float(b @ y), float(a @ (X - y))
            L = L_gbr + L_be
            if L > 1.35 * R:
                continue
            for i, c in enumerate(cls):
                be = netqos.be_qos(c["a"], net["base_delay_ms"], L, R, L_gbr, L_be,
                                   net["service_ms"], K=net["buffer_pkts"],
                                   peak_factor=net.get("peak_factor", 2.0))
                q2 = qoe_models.class_mos(c["qoe_model"], gbr_qos(P, c["b"], L_gbr))
                mos_be = qoe_models.class_mos(c["qoe_model"], be)
                rows.append((i, L, q2, mos_be, max(q2 - mos_be, 0.0)))
    return np.array(rows)


def fit(P: dict, rows: np.ndarray) -> dict:
    R = P["capacity_R"]
    out = {"classes": []}
    for i, c in enumerate(P["classes"]):
        r = rows[rows[:, 0] == i]
        m = (r[:, 1] <= R) & (r[:, 1] >= P.get("fit_L_min", 0.5) * R)
        A = np.c_[np.ones(m.sum()), r[m, 1] / R]
        coef, *_ = np.linalg.lstsq(A, r[m, 4], rcond=None)
        delta, kappa = float(coef[0]), float(max(coef[1], 0.0))
        pred = np.maximum(delta + kappa * r[m, 1] / R, 0.0)
        r2 = 1 - np.sum((r[m, 4] - pred) ** 2) / max(np.sum((r[m, 4] - r[m, 4].mean()) ** 2), 1e-12)
        r = r[r[:, 1] <= 1.02 * R]           # au-delà de R : points non réalisables
        bins = np.linspace(r[:, 1].min(), r[:, 1].max(), 25)
        idx = np.digitize(r[:, 1], bins)
        Lg, Dg = [], []
        for k in range(1, len(bins) + 1):
            sel = idx == k
            if sel.any():
                Lg.append(float(r[sel, 1].mean())); Dg.append(float(r[sel, 4].mean()))
        low = r[r[:, 1] <= P.get("fit_L_min", 0.5) * R]
        floor = float(max(np.mean(low[:, 4]) if len(low) else 0.0, 0.02))
        out["classes"].append({"name": c["name"], "q2": float(np.median(r[:, 2])), "delta": delta, "floor": floor,
                               "kappa": kappa, "r2": float(r2), "curve_L": Lg, "curve_D": Dg,
                               "D_at_R": float(max(delta + kappa, floor))})
    return out


def calibrate(P: dict = None, cache: str = None, force: bool = False) -> dict:
    P = P or load_params()
    cache = cache or os.path.join(ROOT, "results", "calibration.json")
    if os.path.exists(cache) and not force:
        with open(cache) as f:
            return json.load(f)
    rows = sweep(P)
    cal = fit(P, rows)
    cal["p1203"] = qoe_models.HAS_P1203
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    np.savetxt(cache.replace(".json", "_points.csv"), rows, delimiter=",",
               header="class,L,q2,mos_be,D", comments="")
    with open(cache, "w") as f:
        json.dump(cal, f, indent=2)
    return cal


def slot_models(P: dict = None, cal: dict = None, nonlinear: bool = False, sens=None,
                X_matrix: np.ndarray = None) -> list:
    """Un Model par créneau (sessions = profils de demande) ; nonlinear=True utilise les
    courbes D_i(L) issues des modèles QoE au lieu de la linéarisation."""
    P = P or load_params()
    cal = cal or calibrate(P)
    cls = P["classes"]
    Xm = class_profiles(cls, P["slots"]) if X_matrix is None else X_matrix
    shares = P.get("afs_per_class", [1.0])
    tmul = P.get("theta_spread", [1.0] * len(shares))
    names, a, b, th, q2, cidx, split = [], [], [], [], [], [], []
    for j, c in enumerate(cls):
        for k, (sh, tm) in enumerate(zip(shares, tmul)):
            names.append(f"{c['name']}-{k + 1}" if len(shares) > 1 else c["name"])
            a.append(c["a"]); b.append(c["b"]); th.append(c["theta"] * tm)
            q2.append(cal["classes"][j]["q2"]); cidx.append(j); split.append(sh)
    cidx, split = np.array(cidx), np.array(split)
    R = P["capacity_R"]
    kc = cal["classes"]
    if nonlinear:
        degr = CurveDegradation([(kc[j]["curve_L"], kc[j]["curve_D"]) for j in cidx])
    else:
        degr = LinearDegradation([kc[j]["delta"] for j in cidx], [kc[j]["kappa"] for j in cidx], R,
                                 floor=np.array([kc[j]["floor"] for j in cidx]))
    from .model import Sensitivity
    if isinstance(sens, Sensitivity):          # même loi pour toutes les AF
        sens = [sens] * len(names)
    models = []
    for t in range(Xm.shape[0]):
        X = np.maximum(Xm[t][cidx] * split, 1e-6)
        models.append(Model(names=names, X=X, a=a, b=b, q2=q2, theta=th, R=R, c=P["cost_c"], degr=degr,
                            sens=list(sens) if sens else []))
    return models
