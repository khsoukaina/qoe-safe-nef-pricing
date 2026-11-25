"""Shared helpers: configuration, vendored model, testbed scaling.

The pricing mathematics is NOT re-implemented here: it is imported from vendor/nef_pricing,
a frozen copy of the simulation lab package (Stackelberg leader / Nash followers, QoE-safe
selection, conformal quantile, admission and rationing, QoE models). The simulation lab
itself is never touched, so its results stay valid whatever happens in this testbed.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

import numpy as np
import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(ROOT, "vendor")
if VENDOR not in sys.path:
    sys.path.insert(0, VENDOR)

from nef_pricing.calibration import calibrate, load_params, slot_models  # noqa: E402


def load_cfg(path: str = None) -> dict:
    path = path or os.environ.get("NEF_LAB_CONFIG", os.path.join(ROOT, "config", "lab.yaml"))
    with open(path) as f:
        cfg = yaml.safe_load(f)
    cfg["_path"] = path
    return cfg


def rpath(*parts) -> str:
    """Path relative to the lab root (results, config...)."""
    return os.path.join(ROOT, *parts)


# ------------------------------------------------------------------------------ model
_MODEL_CACHE = {}


def model_bundle():
    """(params, calibration, list of 24 slot models) from the vendored package (cached)."""
    if "b" not in _MODEL_CACHE:
        P = load_params()
        cal = calibrate(P)
        alt = os.environ.get("NEF_LAB_CALIBRATION")       # e.g. results/calibration_testbed.json
        if alt:
            with open(alt if os.path.isabs(alt) else rpath(alt)) as f:
                cal = json.load(f)
        _MODEL_CACHE["b"] = (P, cal, slot_models(P, cal))
    return _MODEL_CACHE["b"]


def class_names(P=None):
    P = P or model_bundle()[0]
    return [c["name"] for c in P["classes"]]


def af_class_index(P=None):
    """Class index of each AF of the vendored model (12 AFs = 4 classes x 3 market shares)."""
    P = P or model_bundle()[0]
    n_af = len(P.get("afs_per_class", [1.0]))
    return np.repeat(np.arange(len(P["classes"])), n_af)


# ------------------------------------------------------------------------------ scaling
@dataclass
class Scaling:
    k: float
    s: float
    R_model: float
    R_tb: float

    def rate_tb(self, rate_model: float, cls: str) -> float:
        return float(rate_model) if cls == "voice" else float(rate_model) * self.s


def scaling(cfg: dict) -> Scaling:
    P = model_bundle()[0]
    k = float(cfg["scaling"]["sample_k"])
    s = float(cfg["scaling"]["rate_scale"])
    R = float(P["capacity_R"])
    R_tb = cfg["scaling"].get("capacity_override_mbps") or R * s / k
    return Scaling(k, s, R, float(R_tb))


def profile_table(cfg: dict) -> dict:
    """qosReference -> {class, medType, a_tb, b_tb, ul_mbps, a_model, b_model, class_idx}."""
    P = model_bundle()[0]
    sc = scaling(cfg)
    by_name = {c["name"]: (j, c) for j, c in enumerate(P["classes"])}
    out = {}
    for ref, pr in cfg["profiles"].items():
        j, c = by_name[pr["class"]]
        out[ref] = {"class": pr["class"], "class_idx": j, "medType": pr["medType"],
                    "a_model": float(c["a"]), "b_model": float(c["b"]),
                    "a_tb": sc.rate_tb(c["a"], pr["class"]), "b_tb": sc.rate_tb(c["b"], pr["class"]),
                    "ul_mbps": float(pr.get("ul_mbps", 0.1))}
    return out


def ref_of_class(cfg: dict) -> dict:
    return {v["class"]: k for k, v in profile_table(cfg).items()}


def peak_slot() -> int:
    models = model_bundle()[2]
    return int(np.argmax([m.base_load() for m in models]))


def stochastic_round(x: float, rng) -> int:
    f = int(np.floor(x))
    return f + int(rng.random() < x - f)


def jdump(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, default=_jsonable)


def _jsonable(o):
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))


def fnum(x):
    """JSON-safe float (inf -> 'inf')."""
    x = float(x)
    return "inf" if np.isinf(x) else x


def from_fnum(x):
    return np.inf if x in ("inf", None) else float(x)
