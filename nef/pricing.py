"""Run-time pricing state of the NEF (posted prices are pre-computed, see pricing/).

At the start of each slot the scenario driver calls POST /lab/v1/slot; the NEF then
  * posts the price of the active policy for every qosReference (GET /nef-pricing/v1/prices),
  * sets its admission budget: m2 capacity = R_tb - forecast best-effort load (x = 1),
    exactly the quantity used by the simulation's admission (m2_capacity at the forecast).
"""
from __future__ import annotations

import json
import os

import numpy as np

from labcommon import af_class_index, from_fnum, model_bundle


class PricingState:
    def __init__(self, cfg: dict, profiles: dict, scaling):
        self.cfg, self.prof, self.sc = cfg, profiles, scaling
        self.table = None
        self.policy = cfg["pricing"]["default_policy"]
        self.rationing = cfg["pricing"]["default_rationing"]
        self.slot = None
        self.prices = {r: np.inf for r in profiles}
        self.base_load_tb = 0.0
        self.g_per_delta = {}
        self.load()

    def load(self):
        path = self.cfg["pricing"]["prices_file"]
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), path)
        if os.path.exists(path):
            with open(path) as f:
                self.table = json.load(f)
        return self.table is not None

    def configure(self, policy=None, rationing=None):
        if policy:
            if self.table and policy not in self.table["prices"]["random"]:
                raise ValueError(f"unknown policy {policy}")
            self.policy = policy
        if rationing:
            if rationing not in ("random", "priority", "qoe_gain"):
                raise ValueError(f"unknown rationing {rationing}")
            self.rationing = rationing

    def set_slot(self, slot: int, x_forecast: float = 1.0):
        if self.table is None:
            raise RuntimeError("prices.json missing: run pricing/precompute_prices.py first")
        key = str(int(slot))
        rat = "random" if self.rationing == "random" else "priority"   # qoe_gain uses priority tables
        pr = self.table["prices"][rat][self.policy][key]
        self.prices = {ref: from_fnum(pr[ref]) for ref in self.prof}
        meta = self.table["slots"][key]
        # forecast best-effort load of the testbed: sum_i a_tb_i * X_i(t) * x / k
        P, cal, models = model_bundle()
        m = models[int(slot)]
        cidx = af_class_index(P)
        names = [c["name"] for c in P["classes"]]
        a_tb = np.array([self._a_tb(names[j]) for j in cidx])
        self.base_load_tb = float(a_tb @ (m.X * x_forecast)) / self.sc.k
        self.g_per_delta = meta["g_per_delta"]
        self.slot = int(slot)

    def _a_tb(self, cls):
        for v in self.prof.values():
            if v["class"] == cls:
                return v["a_tb"]
        raise KeyError(cls)

    def price(self, ref: str) -> float:
        return float(self.prices.get(ref, np.inf))

    def m2_capacity(self) -> float:
        return self.sc.R_tb - self.base_load_tb

    def public(self) -> dict:
        return {"slot": self.slot, "policy": self.policy, "rationing": self.rationing,
                "currency": "model-units per model-Mbit/s per slot",
                "prices": {r: (None if np.isinf(p) else p) for r, p in self.prices.items()},
                "closed": [r for r, p in self.prices.items() if np.isinf(p)]}
