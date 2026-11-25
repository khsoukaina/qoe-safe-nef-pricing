"""Modèle de QoS réseau du best-effort en fonction de la charge (simulation).

Hypothèses (à remplacer par les mesures du banc, testbed/) :
- ordonnancement prioritaire : les flux garantis (GBR) passent d'abord ;
- délai d'attente du best-effort : file M/M/1 à priorité non préemptive,
  W = s / ((1 - rho_G)(1 - rho)), avec rho = L/R et rho_G = charge garantie / R ;
- pertes : file M/M/1/K (K paquets) à la charge totale ;
- débit par session best-effort : partage de processeur (modèle de flux élastiques) sur la
  capacité résiduelle C_res = R - L_gbr : débit = débit crête x (1 - rho_be),
  rho_be = L_be / C_res, débit crête = peak_factor x a_i.
"""
from __future__ import annotations

import numpy as np


def mm1k_loss(rho: float, K: int = 60) -> float:
    if rho <= 0:
        return 0.0
    if abs(rho - 1.0) < 1e-9:
        return 1.0 / (K + 1)
    return float((1 - rho) * rho ** K / (1 - rho ** (K + 1)))


def be_qos(a_i: float, base_delay_ms: float, L: float, R: float, L_gbr: float, L_be: float,
           service_ms: float = 1.5, max_wait_ms: float = 400.0, K: int = 60,
           peak_factor: float = 2.0) -> dict:
    """QoS d'une session best-effort de demande a_i (Mbit/s).

    L : charge totale ; L_gbr : charge des flux garantis ; L_be : charge best-effort.
    """
    rho = min(L / R, 0.999)
    rho_g = min(L_gbr / R, 0.999)
    wait = min(service_ms / ((1 - rho_g) * (1 - rho)), max_wait_ms)
    loss = mm1k_loss(L / R, K)
    rho_be = L_be / max(R - L_gbr, 1e-9)
    thr = peak_factor * a_i * max(1.0 - rho_be, 0.02)
    return {"throughput": thr, "delay": base_delay_ms + wait,
            "rtt": 2 * (base_delay_ms + wait), "loss": loss}
