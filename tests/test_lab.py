"""Unit tests of the testbed code with a MOCKED core (no Open5GS, no traffic, no experiment).

  pytest -q tests/                     (the conformance tests are skipped without the specs:
                                        run conformance/fetch_specs.sh first)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx
import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import labcommon  # noqa: E402
from nef_pricing.calibration import load_params, slot_models  # noqa: E402

SPECS = os.environ.get("NEF_SPECS", os.path.join(ROOT, "conformance", "specs", "5GC_APIs"))


# ----------------------------------------------------------------- fixtures
@pytest.fixture(scope="session", autouse=True)
def fake_bundle():
    """Model built from params.yaml with a fixed toy calibration (no calibration sweep is run)."""
    P = load_params()
    cal = {"classes": [{"name": c["name"], "q2": 4.3, "delta": -0.5, "kappa": 1.2, "floor": 0.05,
                        "curve_L": [0, 4500], "curve_D": [0.05, 0.7], "D_at_R": 0.7} for c in P["classes"]]}
    labcommon._MODEL_CACHE["b"] = (P, cal, slot_models(P, cal))
    yield


@pytest.fixture
def cfg(tmp_path):
    c = labcommon.load_cfg()
    c["nef"]["log_dir"] = str(tmp_path / "nef")
    c["enforcer"]["mode"] = "dry"
    prof = labcommon.profile_table(c)
    prices = {"meta": {"policies": ["open", "closed"]}, "slots": {}, "prices": {"random": {}, "priority": {}}}
    for t in range(24):
        prices["slots"][str(t)] = {"g_per_delta": {"video": -0.1, "gaming": -0.2, "voice": 0.5, "web": -0.05}}
        for rat in ("random", "priority"):
            prices["prices"][rat].setdefault("open", {})[str(t)] = {r: 0.01 for r in prof}
            prices["prices"][rat].setdefault("closed", {})[str(t)] = {r: "inf" for r in prof}
    p = tmp_path / "prices.json"
    p.write_text(json.dumps(prices))
    c["pricing"]["prices_file"] = str(p)
    c["pricing"]["default_policy"] = "open"
    return c


class FakeCore:
    """Mock NRF / BSF / PCF answering like Open5GS."""

    def __init__(self):
        self.calls = []
        self.sessions = {}

    def __call__(self, request: httpx.Request):
        path = request.url.path
        self.calls.append((request.method, path))
        if path.startswith("/nnrf-nfm/v1/nf-instances/"):
            return httpx.Response(201, json=json.loads(request.content))
        if path == "/nnrf-disc/v1/nf-instances":
            return httpx.Response(200, json={"validityPeriod": 3600, "nfInstances": [{
                "nfInstanceId": "bsf-1", "nfType": "BSF", "nfStatus": "REGISTERED", "ipv4Addresses": ["127.0.0.15"],
                "nfServices": [{"serviceInstanceId": "0", "serviceName": "nbsf-management",
                                "versions": [{"apiVersionInUri": "v1", "apiFullVersion": "1.0.0"}], "scheme": "http",
                                "nfServiceStatus": "REGISTERED", "ipEndPoints": [{"ipv4Address": "127.0.0.15", "port": 7777}]}]}]})
        if path == "/nbsf-management/v1/pcfBindings":
            ip = request.url.params["ipv4Addr"]
            if ip == "10.45.9.9":
                return httpx.Response(204)
            return httpx.Response(200, json={"ipv4Addr": ip, "dnn": "internet", "snssai": {"sst": 1},
                                             "pcfIpEndPoints": [{"ipv4Address": "127.0.0.13", "port": 7777}]})
        if path == "/npcf-policyauthorization/v1/app-sessions" and request.method == "POST":
            body = json.loads(request.content)
            sid = str(len(self.sessions) + 1)
            self.sessions[sid] = body
            return httpx.Response(201, json=body,
                                  headers={"Location": f"http://127.0.0.13:7777/npcf-policyauthorization/v1/app-sessions/{sid}"})
        if path.endswith("/delete"):
            return httpx.Response(204)
        if request.method == "PATCH":
            return httpx.Response(200, json=json.loads(request.content))
        return httpx.Response(404, json={"title": "not found", "status": 404})


def make_client(cfg, core=None):
    from fastapi.testclient import TestClient
    from nef.app import build_app
    core = core or FakeCore()
    app = build_app(cfg, "core", "dry", mock_transport=httpx.MockTransport(core))
    return TestClient(app), app, core


def sub(ue="10.45.0.2", ref="QOD_VIDEO"):
    return {"notificationDestination": "http://af.example/notify", "ueIpv4Addr": ue, "qosReference": ref,
            "supportedFeatures": "0",
            "flowInfo": [{"flowId": 1, "flowDescriptions": [f"permit out ip from 10.60.0.1 to {ue}",
                                                            f"permit in ip from {ue} to 10.60.0.1"]}]}


# ----------------------------------------------------------------- tests
def test_mapping_bitrate():
    from nef.mapping import BITRATE_RE, bitrate
    for v in (0.06, 0.12, 1.0, 2.5, 5.0):
        assert BITRATE_RE.match(bitrate(v)), bitrate(v)
    assert bitrate(0.12) == "120 Kbps" and bitrate(2.5) == "2.5 Mbps"


def test_full_lifecycle_with_mock_core(cfg):
    c, app, core = make_client(cfg)
    with c:
        assert c.post("/lab/v1/slot", json={"slot": 20}).status_code == 200
        r = c.post("/3gpp-as-session-with-qos/v1/af1/subscriptions", json=sub())
        assert r.status_code == 201, r.text
        loc = r.headers["location"]
        assert r.json()["self"] == loc and "total_ms" in r.headers["x-nef-timing"]
        steps = [p for _, p in core.calls]
        assert "/nbsf-management/v1/pcfBindings" in steps and "/npcf-policyauthorization/v1/app-sessions" in steps
        asc = list(core.sessions.values())[0]["ascReqData"]
        assert asc["ueIpv4"] == "10.45.0.2" and asc["medComponents"]["1"]["medType"] == "VIDEO"
        assert c.post("/lab/v1/enforcer/flush").status_code == 200      # class applied asynchronously
        cmds = app.state.nef.enf.cmd_log
        assert app.state.nef.enf.apply_ms, "activation delay not recorded"
        assert any("filter add" in x and "10.45.0.2/32" in x for x in cmds)
        assert c.get(loc).status_code == 200
        r = c.patch(loc, json={"qosReference": "QOD_WEB"})
        assert r.status_code == 200 and r.json()["qosReference"] == "QOD_WEB"
        assert c.delete(loc).status_code == 204
        assert any(p.endswith("/delete") for _, p in core.calls)
        assert c.get(loc).status_code == 404
        assert app.state.nef.adm.used == pytest.approx(0.0)


def test_rejections(cfg):
    c, app, core = make_client(cfg)
    with c:
        c.post("/lab/v1/slot", json={"slot": 20, "budget_mbps": 1.5})     # room for one video (Δ = 1.0)
        assert c.post("/3gpp-as-session-with-qos/v1/af1/subscriptions", json=sub("10.45.0.2")).status_code == 201
        r = c.post("/3gpp-as-session-with-qos/v1/af1/subscriptions", json=sub("10.45.0.3"))
        assert r.status_code == 403 and r.json()["cause"] == "QOS_NOT_AVAILABLE"
        r = c.post("/3gpp-as-session-with-qos/v1/af1/subscriptions", json=sub("10.45.9.9", "QOD_VOICE"))
        assert r.status_code == 503                                         # no PCF binding (UE not attached)
        assert app.state.nef.adm.used == pytest.approx(1.0)                 # failed request released its budget
        bad = sub()
        bad.pop("notificationDestination")
        r = c.post("/3gpp-as-session-with-qos/v1/af1/subscriptions", json=bad)
        assert r.status_code == 400 and r.headers["content-type"].startswith("application/problem+json")
        c.post("/lab/v1/config", json={"policy": "closed"})
        c.post("/lab/v1/slot", json={"slot": 20})
        r = c.post("/3gpp-as-session-with-qos/v1/af1/subscriptions", json=sub())
        assert r.status_code == 403
        assert c.get("/nef-pricing/v1/prices").json()["closed"]


def test_priority_and_qoe_gain_rationing():
    from nef.admission import Admission

    async def run(mode):
        a = Admission(window_s=0.05)
        a.reset(budget=2.0, mode=mode)
        # (delta, priority, g): the best two must win
        reqs = [(1.0, 0.1, 0.5), (1.0, 0.9, -0.1), (1.0, 0.5, 0.4), (1.0, 0.2, -0.3)]
        res = await asyncio.gather(*[a.request(d, p, g) for d, p, g in reqs])
        return [ok for ok, _ in res]
    assert asyncio.run(run("priority")) == [False, True, True, False]
    assert asyncio.run(run("qoe_gain")) == [True, False, True, False]


def test_qoe_functions():
    from traffic import qoe
    tc = labcommon.load_cfg()["traffic"]
    good = {"n_sent": 100, "n_recv": 100, "owd_dl_ms": [12.0] * 100, "owd_ul_ms": [2.0] * 100}
    bad = {"n_sent": 100, "n_recv": 80, "owd_dl_ms": list(np.linspace(12, 200, 80)), "owd_ul_ms": [2.0] * 80}
    g, b = qoe.voice_mos(good, tc["voice"]), qoe.voice_mos(bad, tc["voice"])
    assert g["mos"] > 4.0 > b["mos"] and g["gob"] > b["gob"]
    assert qoe.gaming_mos({"rtt_ms": [15] * 10, "stream_loss": 0})["mos"] > qoe.gaming_mos({"rtt_ms": [120] * 10, "stream_loss": 0.05})["mos"]
    v1 = qoe.video_mos({"segments": [{"kbps_real": 5000}] * 20, "stall_s": 0, "measured_s": 40})
    v2 = qoe.video_mos({"segments": [{"kbps_real": 400}] * 20, "stall_s": 8, "measured_s": 40})
    assert v1["mos"] > v2["mos"]
    assert qoe.web_mos({"plt_s": [0.8, 1.2]})["mos"] > qoe.web_mos({"plt_s": [8, 10]})["mos"]


@pytest.mark.skipif(not os.path.isdir(SPECS), reason="3GPP specs not fetched")
def test_conformance_of_logged_exchanges(cfg):
    c, app, core = make_client(cfg)
    with c:
        c.post("/lab/v1/slot", json={"slot": 20})
        r = c.post("/3gpp-as-session-with-qos/v1/af1/subscriptions", json=sub())
        c.get("/3gpp-as-session-with-qos/v1/af1/subscriptions")
        c.patch(r.headers["location"], json={"qosReference": "QOD_GAMING"})
        c.delete(r.headers["location"])
        c.post("/3gpp-as-session-with-qos/v1/af1/subscriptions", json={**sub(), "ueIpv4Addr": "300.0.0.1"})
    from conformance.validate import Checker, read_jsonl
    ck = Checker(SPECS)
    ck.run_northbound(read_jsonl(os.path.join(cfg["nef"]["log_dir"], "northbound.jsonl")))
    ck.run_southbound(read_jsonl(os.path.join(cfg["nef"]["log_dir"], "sbi_exchanges.jsonl")))
    ours = {k: v for k, v in ck.stats.items() if "informative" not in k}
    failures = {k: v["errors"] for k, v in ours.items() if v["fail"]}
    assert not failures, json.dumps(failures, indent=1)
    assert sum(v["pass"] for v in ours.values()) >= 8


def test_enforcer_batches_and_is_off_the_decision_path():
    from nef.enforcer import Enforcer

    async def run():
        e = Enforcer({"mode": "dry", "dev": "ogstun", "base_delay_ms": 10}, 56.25)
        await e.setup()
        n0 = len(e.cmd_log)
        t = [e.grant(f"s{i}", f"10.45.0.{i + 2}", 2.0) for i in range(50)]
        assert max(t) < 5.0                                   # enqueue only: no tc on the critical path
        await e.flush()
        assert abs(e.gbr_total() - 110.0) < 1e-9 and len(e.apply_ms) == 50   # 1.1 header headroom
        for i in range(50):
            e.release(f"s{i}")
        await e.flush()
        assert e.gbr_total() == 0 and len(e.cmd_log) > n0
        assert any("netem delay 10ms" in x for x in e.cmd_log)
    asyncio.run(run())
