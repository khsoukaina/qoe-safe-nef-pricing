"""Unit tests of the new math helpers on a toy model (tiny grids; not an experiment)."""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import labcommon  # noqa: E402,F401
from nef_pricing import admission, qoe_safe  # noqa: E402
from nef_pricing.baselines import DemandSurface  # noqa: E402
from nef_pricing.model import symmetric_model  # noqa: E402


@pytest.fixture(scope="module")
def surf():
    m = symmetric_model(4, 400.0, R=900.0, c=0.05)
    return DemandSurface(m, n_p=6, scales=np.array([0.9, 1.0, 1.1]))


def test_gated_table_equals_score_table_when_all_allowed(surf):
    from pricing.precompute_prices import gated_table
    xs = np.array([1.0])
    a = qoe_safe.score_table(surf, xs, "random", p_min=None, n_fine=4)
    b = gated_table(surf, xs, np.ones(surf.model.N, bool), "random", None, 4)
    assert np.allclose(a["price"], b["price"])
    # exact Nash (gated) vs interpolated surface (score_table): equal up to the grid interpolation error
    assert np.allclose(a["mos"], b["mos"], atol=1e-2)


def test_gated_table_closed_classes_buy_nothing(surf):
    from pricing.precompute_prices import gated_table
    allowed = np.array([True, True, False, False])
    t = gated_table(surf, np.array([1.0]), allowed, "random", None, 4)
    assert len(t["price"]) == len(t["mos"]) and np.all(np.isfinite(t["mos"]))
    from nef_pricing.followers import nash
    pv = np.where(allowed, float(t["price"][0]), np.inf)
    y = nash(surf.model, pv)
    assert np.all(y[~allowed] == 0)


def test_group_metrics(surf):
    from sim.exp_q1_quality import group_metrics
    m = surf.model
    none = admission.Outcome(np.inf, np.zeros(m.N), np.zeros(m.N), m.base_load())
    g0 = group_metrics(m, none)
    assert abs(g0["dmos"]) < 1e-12 and g0["buyers_share"] == 0
    p = float(surf.prices[1])
    for rat in ("random", "priority"):
        out = admission.admit(m, surf.y(p, 1.0), p, rat)
        g = group_metrics(m, out)
        if out.y_served.sum() > 0:
            assert g["buyers_uplift"] > 0 and g["nonbuyers_dmos"] <= 1e-12


def test_crn_draws_identical_across_policies():
    from scenario.run_scenario import draw_slot
    from nef_pricing.calibration import load_params, slot_models
    P = load_params()
    cal = {"classes": [{"name": c["name"], "q2": 4.3, "delta": -0.5, "kappa": 1.2, "floor": 0.05,
                        "curve_L": [0, 1], "curve_D": [0, 1]} for c in P["classes"]]}
    models = slot_models(P, cal)
    cfg = labcommon.load_cfg()
    a = draw_slot(1, 20, models, cfg, P)
    b = draw_slot(1, 20, models, cfg, P)
    assert a[0] == b[0] and a[1] == b[1] and np.allclose(a[2], b[2])
    n_sess = len(a[1])
    assert 20 <= n_sess <= 80, n_sess          # testbed size at peak with k = 20
