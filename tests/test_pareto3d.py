"""Unit tests of sim/exp_pareto3d_choco.py on random tables (no model, no simulation)."""
import argparse
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sim import exp_pareto3d_choco as p3  # noqa: E402


def rand_G(seed, T=5, K=4):
    rng = np.random.default_rng(seed)
    return rng.integers(-50, 50, size=(3, T, K)).astype(np.int64)


@pytest.mark.parametrize("seed", [0, 1, 2])
@pytest.mark.parametrize("ms", [None, 1, 2])
def test_dp_equals_bruteforce(seed, ms):
    G = rand_G(seed)
    S, P = p3.dp_front(G, ms)
    bf = p3.brute_force(G, ms)
    ref = {tuple(r) for r in bf[p3.nondominated(bf)]}
    assert {tuple(r) for r in S} == ref
    T = G.shape[1]
    for s, p in zip(S, P):  # each stored plan really produces its objective vector and respects the constraint
        assert np.array_equal(G[:, np.arange(T), p].sum(axis=1), s)
        if ms is not None:
            assert np.sum(p[1:] != p[:-1]) <= ms


def test_nondominated_simple():
    S = np.array([[1, 1, 1], [2, 2, 2], [2, 2, 2], [3, 0, 0], [0, 3, 0], [1, 1, 3]])
    keep = {tuple(S[i]) for i in p3.nondominated(S)}
    assert keep == {(2, 2, 2), (3, 0, 0), (0, 3, 0), (1, 1, 3)}


@pytest.mark.skipif(not p3.HAS_CHOCO, reason="pychoco not installed")
@pytest.mark.parametrize("ms", [None, 1])
def test_choco_exact(ms):
    G = rand_G(7)
    S, _ = p3.dp_front(G, ms)
    bf = p3.brute_force(G, ms)
    Sc, Pc, log = p3.choco_front(G, 6, 4, ms, "5s")
    assert len(log) > 0
    for ex, ey, z in log:
        assert p3.eps_optimum(S, ex, ey) == z == p3.eps_optimum(bf, ex, ey)
    exact = {tuple(r) for r in S}
    assert all(tuple(r) in exact for r in Sc)  # every Choco point is exactly on the front
    lo, hi = S.min(axis=0), S.max(axis=0)
    assert 0 < p3.hypervolume(Sc, lo, hi) <= p3.hypervolume(S, lo, hi) + 1e-12


def test_hypervolume_box():
    # one point at the ideal corner -> normalised box (1.1)^3
    S = np.array([[1.0, 1.0, 1.0], [0.0, 0.0, 0.0]])
    assert np.isclose(p3.hypervolume(S, np.zeros(3), np.ones(3)), 1.1 ** 3)


def mock_tables(T=4, K=5, seed=3):
    rng = np.random.default_rng(seed)
    price = np.linspace(0, 1, K)[None, :] * np.ones((T, 1))
    load = np.linspace(0.6, 1.2, T)[:, None]
    tb = {"profit": (price * (1 - price) * 8 * load).tolist(),
          "welfare": (5 * load * (1 - price) + rng.random((T, K))).tolist(),
          "reject": (10 * (1 - price) * load).tolist(),
          "dmos": (0.3 * (1 - price) * load).tolist(),
          "dissat": (5 * price * load + 0.1).tolist(),
          "dissat_base": (5 * load * np.ones((T, K))).tolist(),
          "harm": (0.004 * (1 - price) * load).tolist(),
          "buyers_share": (0.4 * (1 - price)).tolist(),
          "p5": (4 - price).tolist(), "p5_base": (3 * np.ones((T, K))).tolist(),
          "sessions": (100 * load * np.ones((T, K))).tolist()}
    tb["profit"][0][0] = 0.0
    return tb


def args(tmp, **kw):
    d = dict(axes=["profit", "harm", "dissat"], color="dmos", max_switch=None, no_choco=False, grid=[6, 4],
             time_limit="5s", bf_max=3e6, rationing="random", rows=6, view=[24, -52], note=None)
    d.update(kw)
    return argparse.Namespace(**d)


@pytest.mark.parametrize("ms", [None, 1])
def test_end_to_end_plots(tmp_path, ms):
    levels = ["no_nef", "safe_mean", "quantile", "mid", "revenue_max"]
    slots = [19, 20, 21, 22]
    a = args(str(tmp_path), max_switch=ms)
    R, info, _ = p3.analyse(mock_tables(), levels, slots, a)
    assert info["dp_equals_bruteforce"]
    if p3.HAS_CHOCO:
        assert info["max_gap_vs_dp"] == 0 and info["max_gap_vs_bruteforce"] == 0
    p3.write_outputs(R, info, levels, slots, a, str(tmp_path))
    p3.make_figures(R, levels, slots, a, str(tmp_path))
    for f in ("figP3_pareto3d_choco.png", "figP3a_pareto3d.png", "tableP3_pareto3d.md", "p3d_front_exact.csv"):
        assert (tmp_path / f).exists()
