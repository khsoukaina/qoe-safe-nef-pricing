"""Profils de demande sur 24 h, prévision de la NEF et incertitude."""
from __future__ import annotations

import numpy as np


def two_peaks(hours) -> np.ndarray:
    """Profil diurne stylisé, maximum 1 : creux nocturne, pointe de midi, pointe du soir."""
    t = np.asarray(hours, float)
    p = (0.18 + 0.45 * np.exp(-0.5 * ((t - 12.5) / 1.6) ** 2)
         + 0.82 * np.exp(-0.5 * ((t - 20.5) / 2.0) ** 2)
         + 0.20 * np.exp(-0.5 * ((t - 9.0) / 1.5) ** 2))
    return p / p.max()


def class_profiles(classes: list, slots: int = 24) -> np.ndarray:
    """Matrice (créneaux x classes) des sessions actives attendues."""
    h = np.arange(slots) + 0.5
    base = two_peaks(h)
    evening = np.exp(-0.5 * ((h - 20.5) / 2.5) ** 2)
    out = []
    for c in classes:
        w = c.get("profile_weight_evening", 1.0)
        prof = base * (1 + (w - 1) * evening)
        out.append(c["X_peak"] * prof / prof.max())
    return np.array(out).T


class Forecast:
    """Prévision de la NEF : moyenne m_t (profil attendu) et écart-type s_t = cv * m_t.

    bias : biais multiplicatif de la prévision (robustesse) ; la demande réalisée suit
    X_t = m_t (1 + cv Z), Z ~ N(0, 1) (tronquée à 5 % de m_t).
    """

    def __init__(self, mean: np.ndarray, cv: float, bias: float = 0.0, seed: int = 0):
        self.mean = np.asarray(mean, float)
        self.cv = cv
        self.bias = bias
        self.rng = np.random.default_rng(seed)

    def predicted_mean(self):
        return self.mean * (1 + self.bias)

    def predicted_std(self):
        return self.cv * self.predicted_mean()

    def sample_scale(self, n: int) -> np.ndarray:
        """Facteurs d'échelle de demande réalisée (communs aux classes, parts fixes)."""
        return np.maximum(1 + self.cv * self.rng.standard_normal(n), 0.05)
