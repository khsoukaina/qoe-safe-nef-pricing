"""Modèles QoS -> QoE (MOS).

Statut de chaque modèle (à rappeler dans l'article) :
- voix      : E-model ITU-T G.107 (forme simplifiée usuelle : Id de Cole-Rosenbluth,
              Ie,eff avec Bpl, conversion R -> MOS normative)            [NORMATIF]
- vidéo     : ITU-T P.1203 mode 0 via le paquet open source itu-p1203 si disponible ;
              sinon repli logarithmique (loi de Weber-Fechner) + pénalité d'arrêt
              (forme de Hossfeld et al.)                                  [NORMATIF / REPLI]
- jeu cloud : forme IQX sur le RTT et les pertes, paramètres choisis pour reproduire les
              ordres de grandeur de G.1072 (bon < 20 ms, dégradé vers 100 ms) [HYPOTHÈSE À CALIBRER]
- web       : relation logarithmique MOS / temps de chargement (type G.1030)  [HYPOTHÈSE À CALIBRER]
- iqx       : relation exponentielle générique de Fiedler, Hossfeld, Tran-Gia.
"""
from __future__ import annotations

import functools

import numpy as np

try:  # P.1203 (installé depuis le dépôt open source itu-p1203)
    from itu_p1203 import P1203Standalone  # type: ignore
    HAS_P1203 = True
except Exception:  # pragma: no cover
    HAS_P1203 = False


# ---------------------------------------------------------------- génériques
def iqx(x, alpha, beta, gamma):
    """IQX : MOS = alpha * exp(-beta x) + gamma."""
    return alpha * np.exp(-beta * np.asarray(x, float)) + gamma


def r_to_mos(R):
    """Conversion normative G.107 du facteur R vers le MOS."""
    R = np.asarray(R, float)
    mos = 1 + 0.035 * R + 7e-6 * R * (R - 60) * (100 - R)
    return np.where(R <= 0, 1.0, np.where(R >= 100, 4.5, mos))


# ---------------------------------------------------------------- voix
def emodel_mos(delay_ms, loss_frac, Ie: float = 0.0, Bpl: float = 25.1, burst_r: float = 1.0):
    """E-model simplifié (codec G.711 avec masquage de pertes : Ie = 0, Bpl = 25,1)."""
    d = np.asarray(delay_ms, float)
    Id = 0.024 * d + 0.11 * (d - 177.3) * (d > 177.3)
    Ppl = 100.0 * np.asarray(loss_frac, float)
    Ie_eff = Ie + (95 - Ie) * Ppl / (Ppl / burst_r + Bpl)
    R = 93.2 - Id - Ie_eff
    return r_to_mos(R)


# ---------------------------------------------------------------- vidéo
LADDER_KBPS = [(400, "640x360"), (1000, "854x480"), (2500, "1280x720"), (5000, "1920x1080")]


@functools.lru_cache(maxsize=4096)
def _p1203(bitrate_kbps: int, resolution: str, stall_s: float) -> float:
    segs = [{"bitrate": bitrate_kbps, "codec": "h264", "duration": 10, "fps": 30,
             "resolution": resolution, "start": 10 * k} for k in range(6)]
    stalls = [] if stall_s <= 0.05 else [[0, round(stall_s, 2)]]
    inp = {"I11": {"segments": [{"bitrate": 128, "codec": "aaclc", "duration": 60, "start": 0}], "streamId": 1},
           "I13": {"segments": segs, "streamId": 1},
           "I23": {"stalling": stalls, "streamId": 1},
           "IGen": {"device": "mobile", "displaySize": "1920x1080", "viewingDistance": "150cm"}}
    return float(P1203Standalone(inp, quiet=True).calculate_complete()["O46"])


def video_mos(throughput_mbps, use_p1203: bool = True):
    """Lecteur adaptatif : choisit le plus haut palier <= 0,8 x débit disponible ;
    si le débit est inférieur au palier minimal, la lecture s'arrête (durée d'arrêt
    proportionnelle au déficit sur une session de 60 s)."""
    t_kbps = float(throughput_mbps) * 1000.0
    rung = LADDER_KBPS[0]
    for br, res in LADDER_KBPS:
        if br <= 0.8 * t_kbps:
            rung = (br, res)
    deficit = max(0.0, 1.0 - t_kbps / LADDER_KBPS[0][0])
    stall = 60.0 * deficit
    if use_p1203 and HAS_P1203:
        return _p1203(int(rung[0]), rung[1], round(stall, 1))
    q = 1 + 3.5 * np.log(1 + rung[0] / 400.0) / np.log(1 + 5000 / 400.0)
    return float(1.0 + (q - 1.0) * np.exp(-0.15 * stall))


# ---------------------------------------------------------------- jeu cloud
def gaming_mos(rtt_ms, loss_frac):
    """Forme IQX (hypothèse) : 4,5 jusqu'à 20 ms, ~2,3 à 100 ms ; pertes multiplicatives."""
    r = np.maximum(np.asarray(rtt_ms, float) - 20.0, 0.0)
    m = 1.0 + 3.5 * np.exp(-0.0115 * r)
    return 1.0 + (m - 1.0) * np.exp(-25.0 * np.asarray(loss_frac, float))


# ---------------------------------------------------------------- web
def web_mos(plt_s):
    """Relation logarithmique (hypothèse de type G.1030) : 4,5 à 1 s, 1 vers 18 s."""
    plt = np.maximum(np.asarray(plt_s, float), 1e-3)
    return np.clip(4.5 - 1.2 * np.log(np.maximum(plt, 1.0)), 1.0, 4.5)


def page_load_time(throughput_mbps, rtt_ms, page_mb: float = 2.5, round_trips: int = 12):
    return page_mb * 8.0 / np.maximum(throughput_mbps, 1e-3) + round_trips * rtt_ms / 1000.0


# ---------------------------------------------------------------- dispatch
def class_mos(kind: str, qos: dict) -> float:
    """MOS d'une session de la classe `kind` pour une QoS donnée.
    qos : {'throughput': Mbit/s, 'delay': ms aller simple, 'rtt': ms, 'loss': fraction}."""
    if kind == "voice":
        return float(emodel_mos(qos["delay"], qos["loss"]))
    if kind == "video":
        return video_mos(qos["throughput"] * (1 - qos["loss"]))
    if kind == "gaming":
        return float(gaming_mos(qos["rtt"], qos["loss"]))
    if kind == "web":
        return float(web_mos(page_load_time(qos["throughput"] * (1 - qos["loss"]), qos["rtt"])))
    raise ValueError(kind)
