"""Unit tests of sim/exp_conformal_coverage.py (random draws and a toy model, no lab simulation)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sim import exp_conformal_coverage as cc  # noqa: E402


def test_conformal_meets_eps_on_exchangeable_demand():
    for sc in ("normal", "heavy", "skewed"):
        res, *_ = cc.coverage(sc, 0.10, 60, 3000, 0.12, seed=1)
        assert res["conformal"]["overload"] <= 0.10 + 2 * res["conformal"]["ci"]
        assert res["mean"]["overload"] > 0.3  # pricing on the mean overloads about half of the days


def test_rules_use_past_only_and_tiers():
    x = np.r_[np.ones(10), 2.0, 2.0]
    Q = cc.run_rules(x, 10, 0.2, 0.12)
    assert np.isnan(Q["conformal"][:10]).all()
    assert Q["mean"][10] == 1.0  # day 10 sees only days 0..9
    assert Q["tiers"][11] == 1 + 0.12  # yesterday (day 10) was high
    assert cc.tiers_scale(0.5, 0.12) < 1 < cc.tiers_scale(1.5, 0.12)


def test_surge_breaks_static_guarantee():
    res, x, Q, n, shift = cc.coverage("surge", 0.05, 60, 2000, 0.12, seed=2)
    assert shift is not None and res["conformal"]["overload"] > 0.05  # honest limit: not exchangeable


def test_econ_on_toy_model():
    from nef_pricing import baselines
    from nef_pricing.model import symmetric_model
    m = symmetric_model(3, 300.0, R=600.0)
    s = baselines.DemandSurface(m, n_p=8)
    x = np.array([1.0, 1.1, 0.9, 1.2])
    Q = {r: np.array([1.0, 1.0, 1.1, 1.2]) for r in cc.RULES}
    E = cc.econ(s, x, Q, [1, 2, 3])
    for r in cc.RULES:
        assert set(E[r]) >= {"profit", "rejected_pct", "harm_1e3", "dmos"}


def test_figures(tmp_path):
    C = {sc: {str(e): {r: {"overload": 0.05 + 0.01 * k, "ci": 0.01} for k, r in enumerate(cc.RULES)} for e in cc.EPS}
         for sc in cc.SCEN}
    E = {sc: {r: {k: 1.0 for k in ("profit", "rejected_pct", "harm_1e3", "dmos", "profit_ci", "rejected_pct_ci",
                                   "harm_1e3_ci", "dmos_ci")} for r in cc.RULES} for sc in cc.SCEN}
    cc.fig_coverage(C, str(tmp_path))
    cc.fig_cost(E, C, str(tmp_path))
    res, x, Q, n, shift = cc.coverage("surge", 0.05, 60, 600, 0.12, seed=3)
    cc.fig_surge(x, Q, n, shift, str(tmp_path))
    for f in ("figC1_coverage.png", "figC2_cost_of_safety.png", "figC3_surge_timeline.png"):
        assert (tmp_path / f).exists()
