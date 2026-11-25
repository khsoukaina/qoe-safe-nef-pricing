"""Network Exposure Function (NEF) with dynamic QoS pricing, in front of a real 5G core.

Northbound (AF-facing), 3GPP TS 29.122 clause 5.14 AsSessionWithQoS API:
  POST   /3gpp-as-session-with-qos/v1/{scsAsId}/subscriptions                 201 + Location
  GET    /3gpp-as-session-with-qos/v1/{scsAsId}/subscriptions                 200
  GET    /3gpp-as-session-with-qos/v1/{scsAsId}/subscriptions/{subscriptionId} 200 / 404
  PUT    /3gpp-as-session-with-qos/v1/{scsAsId}/subscriptions/{subscriptionId} 200
  PATCH  /3gpp-as-session-with-qos/v1/{scsAsId}/subscriptions/{subscriptionId} 200
  DELETE /3gpp-as-session-with-qos/v1/{scsAsId}/subscriptions/{subscriptionId} 204
Pricing extension (non-standard, proposed by this work):
  GET    /nef-pricing/v1/prices            posted price per qosReference for the current slot
  "x-pricing" in the subscription          request: {"maxPrice", "priority"}; response: {"price", "slot", "policy"}
Southbound (real core): NRF registration/discovery, BSF pcfBindings, PCF
Npcf_PolicyAuthorization Create/Update/Delete, PCF notifications on notifUri.
Lab control (testbed driver only): /lab/v1/config, /lab/v1/slot, /lab/v1/reset, /lab/v1/state.

Every northbound exchange is logged (results/nef/northbound.jsonl) for the conformance check;
every decision is logged with its latency breakdown (results/nef/decisions.jsonl).

Run:  python -m nef.app [--config config/lab.yaml] [--southbound core|none] [--enforcer tc|dry|off]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from labcommon import load_cfg, profile_table, rpath, scaling  # noqa: E402
from nef import mapping  # noqa: E402
from nef.admission import Admission  # noqa: E402
from nef.enforcer import Enforcer  # noqa: E402
from nef.pricing import PricingState  # noqa: E402
from nef.sbi import SbiClient, SbiError, new_id  # noqa: E402

API = "/3gpp-as-session-with-qos/v1"
PROBLEM = "application/problem+json"


class Nef:
    def __init__(self, cfg: dict, southbound: str = None, enforcer_mode: str = None, mock_transport=None):
        self.cfg = cfg
        nef = cfg["nef"]
        self.southbound = southbound or nef.get("southbound", "core")
        self.log_dir = nef["log_dir"] if os.path.isabs(nef["log_dir"]) else rpath(nef["log_dir"])
        os.makedirs(self.log_dir, exist_ok=True)
        self.prof = profile_table(cfg)
        self.sc = scaling(cfg)
        self.pricing = PricingState(cfg, self.prof, self.sc)
        self.adm = Admission(cfg["pricing"].get("batch_window_s", 1.5))
        ecfg = dict(cfg["enforcer"])
        if enforcer_mode:
            ecfg["mode"] = enforcer_mode
        self.enf = Enforcer(ecfg, self.sc.R_tb)
        self.sbi = None
        if self.southbound == "core":
            self.sbi = SbiClient(cfg["core"], nef, os.path.join(self.log_dir, "sbi_exchanges.jsonl"),
                                 mock_transport=mock_transport)
        self.subs = {}            # sid -> record
        self._nb = open(os.path.join(self.log_dir, "northbound.jsonl"), "a", buffering=1)
        self._dec = open(os.path.join(self.log_dir, "decisions.jsonl"), "a", buffering=1)
        self._ntf = open(os.path.join(self.log_dir, "pcf_notifications.jsonl"), "a", buffering=1)
        self.run_tag = None
        self.enf.log = self.dec_log           # activation delay of every guaranteed class

    # ------------------------------------------------------------------ logging
    def nb_log(self, method, path, req_body, status, headers, body):
        self._nb.write(json.dumps({"t": time.time(), "method": method, "path": path, "request": req_body,
                                   "status": status, "headers": headers, "response": body}) + "\n")

    def dec_log(self, rec):
        rec.update({"t": time.time(), "slot": self.pricing.slot, "policy": self.pricing.policy,
                    "rationing": self.pricing.rationing, "run": self.run_tag})
        self._dec.write(json.dumps(rec) + "\n")

    # ------------------------------------------------------------------ helpers
    def notif_uri(self, sid):
        return f"{self.cfg['nef']['callback_base'].rstrip('/')}/pcf-callbacks/{sid}"

    async def release(self, rec, reason="af_delete"):
        t = {}
        if self.sbi and rec.get("pcf_loc"):
            t0 = time.perf_counter()
            try:
                await self.sbi.app_session_delete(rec["pcf_loc"])
            except Exception as e:  # keep releasing local resources
                rec["delete_error"] = str(e)
            t["pcf_delete_ms"] = 1e3 * (time.perf_counter() - t0)
        t["enf_release_ms"] = self.enf.release(rec["sid"])
        await self.adm.release(rec["delta"])
        self.dec_log({"event": "release", "sid": rec["sid"], "reason": reason, **t})

    async def release_all(self):
        for sid in list(self.subs):
            rec = self.subs.pop(sid)
            await self.release(rec, "lab_reset")


def build_app(cfg: dict, southbound=None, enforcer_mode=None, mock_transport=None) -> FastAPI:
    nef = Nef(cfg, southbound, enforcer_mode, mock_transport)

    @asynccontextmanager
    async def lifespan(app):
        await nef.enf.setup()
        if nef.sbi and cfg["nef"].get("register_in_nrf", True):
            try:
                await nef.sbi.nrf_register()
            except Exception as e:
                print(f"[nef] NRF registration failed: {e}")
        yield
        await nef.release_all()
        if nef.sbi:
            await nef.sbi.close()

    app = FastAPI(title="NEF AsSessionWithQoS + dynamic pricing", lifespan=lifespan)
    app.state.nef = nef

    def problem(status, title, detail=None, cause=None, invalid=None, log=None):
        body = mapping.problem(status, title, detail, cause, invalid)
        if log:
            nef.nb_log(*log, status, {"content-type": PROBLEM}, body)
        return JSONResponse(body, status_code=status, media_type=PROBLEM)

    # ================================================================ northbound
    @app.post(API + "/{scs}/subscriptions")
    async def create(scs: str, request: Request):
        t0 = time.perf_counter()
        path = request.url.path
        try:
            body = await request.json()
        except Exception:
            return problem(400, "Bad Request", "body is not valid JSON", log=("POST", path, None))
        lg = ("POST", path, body)
        bad = mapping.check_subscription(body, nef.prof)
        if bad:
            return problem(400, "Bad Request", "invalid subscription", invalid=bad, log=lg)
        ref = body["qosReference"]
        prof = nef.prof[ref]
        xp = body.get("x-pricing") or {}
        price = nef.pricing.price(ref)
        tim = {}
        tim["price_ms"] = 1e3 * (time.perf_counter() - t0)
        dec = {"event": "create", "scs": scs, "ref": ref, "class": prof["class"], "ue": body["ueIpv4Addr"],
               "price": None if price == float("inf") else price, "priority": xp.get("priority")}
        if price == float("inf"):
            nef.dec_log({**dec, "admitted": False, "cause": "CLOSED_BY_PRICING", **tim})
            return problem(403, "Forbidden", "QoS closed on this slot by the QoE-safe pricing",
                           cause="QOS_NOT_AVAILABLE", log=lg)
        if xp.get("maxPrice") is not None and float(xp["maxPrice"]) < price:
            nef.dec_log({**dec, "admitted": False, "cause": "PRICE_ABOVE_MAX", **tim})
            return problem(403, "Forbidden", f"posted price {price:.6g} above maxPrice", cause="PRICE_ABOVE_MAX", log=lg)
        delta = prof["b_tb"] - prof["a_tb"]
        ok, tim["admission_ms"] = await nef.adm.request(delta, float(xp.get("priority") or 0.0),
                                                         float(nef.pricing.g_per_delta.get(prof["class"], 0.0)))
        if not ok:
            tim["total_ms"] = 1e3 * (time.perf_counter() - t0)
            nef.dec_log({**dec, "admitted": False, "cause": "CAPACITY", **tim})
            return problem(403, "Forbidden", "guaranteed capacity exhausted on this slot",
                           cause="QOS_NOT_AVAILABLE", log=lg)
        sid = new_id()
        pcf_loc = None
        if nef.sbi:
            try:
                t1 = time.perf_counter()
                pcf = await nef.sbi.pcf_for_ue(body["ueIpv4Addr"])
                tim["bsf_ms"] = 1e3 * (time.perf_counter() - t1)
                asc = mapping.to_app_session_context(body, prof, nef.notif_uri(sid), cfg["nef"], cfg["core"])
                t2 = time.perf_counter()
                pcf_loc, _ = await nef.sbi.app_session_create(pcf, asc)
                tim["pcf_ms"] = 1e3 * (time.perf_counter() - t2)
            except (SbiError, Exception) as e:
                await nef.adm.release(delta)
                st = getattr(e, "status", None)
                tim["total_ms"] = 1e3 * (time.perf_counter() - t0)
                nef.dec_log({**dec, "admitted": False, "cause": f"SOUTHBOUND:{getattr(e, 'step', 'error')}:{st}",
                             "error": str(getattr(e, "body", e))[:300], **tim})
                return problem(503, "Service Unavailable", f"core network refused or unreachable: {e}",
                               cause="SOUTHBOUND_FAILURE", log=lg)
        tim["enf_ms"] = nef.enf.grant(sid, body["ueIpv4Addr"], prof["b_tb"])   # enqueued, applied asynchronously
        loc = f"{str(request.base_url).rstrip('/')}{API}/{scs}/subscriptions/{sid}"
        out = dict(body)
        out["self"] = loc
        out["x-pricing"] = {"price": price, "slot": nef.pricing.slot, "policy": nef.pricing.policy}
        nef.subs[sid] = {"sid": sid, "scs": scs, "body": out, "ref": ref, "delta": delta, "pcf_loc": pcf_loc,
                         "ue": body["ueIpv4Addr"], "t": time.time()}
        tim["total_ms"] = 1e3 * (time.perf_counter() - t0)
        nef.dec_log({**dec, "admitted": True, "sid": sid, **tim})
        hdr = {"Location": loc, "X-Nef-Timing": ";".join(f"{k}={v:.3f}" for k, v in tim.items())}
        nef.nb_log("POST", path, body, 201, {"location": loc, "content-type": "application/json"}, out)
        return JSONResponse(out, status_code=201, headers=hdr)

    @app.get(API + "/{scs}/subscriptions")
    async def list_subs(scs: str, request: Request):
        items = [r["body"] for r in nef.subs.values() if r["scs"] == scs]
        nef.nb_log("GET", request.url.path, None, 200, {"content-type": "application/json"}, items)
        return JSONResponse(items)

    @app.get(API + "/{scs}/subscriptions/{sid}")
    async def get_sub(scs: str, sid: str, request: Request):
        r = nef.subs.get(sid)
        if not r or r["scs"] != scs:
            return problem(404, "Not Found", "unknown subscription", log=("GET", request.url.path, None))
        nef.nb_log("GET", request.url.path, None, 200, {"content-type": "application/json"}, r["body"])
        return JSONResponse(r["body"])

    async def modify(scs, sid, request, method):
        path = request.url.path
        r = nef.subs.get(sid)
        if not r or r["scs"] != scs:
            return problem(404, "Not Found", "unknown subscription", log=(method, path, None))
        try:
            body = await request.json()
        except Exception:
            return problem(400, "Bad Request", "body is not valid JSON", log=(method, path, None))
        lg = (method, path, body)
        new_ref = body.get("qosReference", r["ref"])
        if new_ref not in nef.prof:
            return problem(400, "Bad Request", invalid=[{"param": "qosReference", "reason": "unknown"}], log=lg)
        if new_ref != r["ref"]:
            prof = nef.prof[new_ref]
            if nef.pricing.price(new_ref) == float("inf"):
                return problem(403, "Forbidden", "QoS closed on this slot", cause="QOS_NOT_AVAILABLE", log=lg)
            new_delta = prof["b_tb"] - prof["a_tb"]
            await nef.adm.release(r["delta"])
            ok, _ = await nef.adm.request(new_delta)
            if not ok:
                await nef.adm.request(r["delta"])
                return problem(403, "Forbidden", "guaranteed capacity exhausted", cause="QOS_NOT_AVAILABLE", log=lg)
            if nef.sbi and r["pcf_loc"]:
                try:
                    await nef.sbi.app_session_update(r["pcf_loc"], mapping.update_patch(prof))
                except Exception as e:
                    await nef.adm.release(new_delta)
                    await nef.adm.request(r["delta"])
                    return problem(503, "Service Unavailable", str(e), cause="SOUTHBOUND_FAILURE", log=lg)
            nef.enf.release(sid)
            nef.enf.grant(sid, r["ue"], prof["b_tb"])
            r["ref"], r["delta"] = new_ref, new_delta
        if method == "PUT":
            keep = {"self": r["body"]["self"], "x-pricing": r["body"].get("x-pricing")}
            r["body"] = {**body, **keep}
        else:
            r["body"].update({k: v for k, v in body.items() if v is not None})
        nef.nb_log(method, path, body, 200, {"content-type": "application/json"}, r["body"])
        return JSONResponse(r["body"])

    @app.patch(API + "/{scs}/subscriptions/{sid}")
    async def patch_sub(scs: str, sid: str, request: Request):
        return await modify(scs, sid, request, "PATCH")

    @app.put(API + "/{scs}/subscriptions/{sid}")
    async def put_sub(scs: str, sid: str, request: Request):
        return await modify(scs, sid, request, "PUT")

    @app.delete(API + "/{scs}/subscriptions/{sid}")
    async def delete_sub(scs: str, sid: str, request: Request):
        r = nef.subs.get(sid)
        if not r or r["scs"] != scs:
            return problem(404, "Not Found", "unknown subscription", log=("DELETE", request.url.path, None))
        nef.subs.pop(sid)
        await nef.release(r)
        nef.nb_log("DELETE", request.url.path, None, 204, {}, None)
        return Response(status_code=204)

    # ================================================================ pricing extension
    @app.get("/nef-pricing/v1/prices")
    async def prices():
        return JSONResponse(nef.pricing.public())

    # ================================================================ PCF callbacks
    @app.post("/pcf-callbacks/{sid}/notify")
    @app.post("/pcf-callbacks/{sid}/terminate")
    async def pcf_cb(sid: str, request: Request):
        try:
            body = await request.json()
        except Exception:
            body = None
        nef._ntf.write(json.dumps({"t": time.time(), "sid": sid, "path": request.url.path, "body": body}) + "\n")
        if request.url.path.endswith("/terminate") and sid in nef.subs:
            rec = nef.subs.pop(sid)
            rec["pcf_loc"] = None          # the PCF already removed the context
            await nef.release(rec, "pcf_terminate")
        return Response(status_code=204)

    # ================================================================ lab control
    @app.post("/lab/v1/config")
    async def lab_config(request: Request):
        b = await request.json()
        await nef.release_all()
        nef.pricing.load()                          # picks up a freshly pre-computed prices.json
        try:
            nef.pricing.configure(b.get("policy"), b.get("rationing"))
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        nef.run_tag = b.get("run")
        await nef.enf.setup()
        return JSONResponse(nef.pricing.public())

    @app.post("/lab/v1/slot")
    async def lab_slot(request: Request):
        b = await request.json()
        await nef.release_all()
        nef.pricing.set_slot(int(b["slot"]), float(b.get("x_forecast", 1.0)))
        if b.get("open_price") is not None:          # calibration / latency runs only
            nef.pricing.prices = {r: float(b["open_price"]) for r in nef.prof}
        budget = nef.pricing.m2_capacity() if b.get("budget_mbps") is None else float(b["budget_mbps"])
        nef.adm.reset(budget, nef.pricing.rationing)
        return JSONResponse({**nef.pricing.public(), "m2_capacity_mbps": budget,
                             "base_load_forecast_mbps": nef.pricing.base_load_tb, "R_tb": nef.sc.R_tb})

    @app.post("/lab/v1/reset")
    async def lab_reset():
        await nef.release_all()
        return JSONResponse({"active": len(nef.subs)})

    @app.get("/lab/v1/state")
    async def lab_state():
        return JSONResponse({**nef.pricing.public(), "active": len(nef.subs), "admission_used": nef.adm.used,
                             "admission_budget": nef.adm.budget, "gbr_total_mbps": nef.enf.gbr_total(),
                             "R_tb": nef.sc.R_tb, "southbound": nef.southbound, "enforcer": nef.enf.mode,
                             "bsf": getattr(nef.sbi, "bsf_uri", None), "bsf_source": getattr(nef.sbi, "bsf_source", None),
                             "nrf_registered": getattr(nef.sbi, "registered", False)})

    @app.get("/lab/v1/tc")
    async def lab_tc():
        a = sorted(nef.enf.apply_ms)
        q = (lambda f: a[min(int(f * len(a)), len(a) - 1)] if a else None)
        return JSONResponse({"stats": await nef.enf.stats(), "last_cmds": nef.enf.cmd_log[-20:],
                             "activation_ms": {"n": len(a), "p50": q(0.5), "p95": q(0.95), "p99": q(0.99)},
                             "tc_errors": nef.enf.errors})

    @app.post("/lab/v1/enforcer/flush")
    async def lab_flush():
        t0 = time.perf_counter()
        await nef.enf.flush()
        return JSONResponse({"flushed_in_ms": 1e3 * (time.perf_counter() - t0), "gbr_total_mbps": nef.enf.gbr_total()})

    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--southbound", choices=["core", "none"], default=None)
    ap.add_argument("--enforcer", choices=["tc", "dry", "off"], default=None)
    args = ap.parse_args()
    cfg = load_cfg(args.config)
    app = build_app(cfg, args.southbound, args.enforcer)
    from hypercorn.asyncio import serve
    from hypercorn.config import Config
    hc = Config()
    hc.bind = [f"{cfg['nef']['listen_host']}:{cfg['nef']['listen_port']}"]
    hc.accesslog = None
    hc.keep_alive_timeout = 30
    print(f"[nef] listening on {hc.bind[0]} (HTTP/1.1 + h2c), southbound={app.state.nef.southbound}, "
          f"enforcer={app.state.nef.enf.mode}, R_tb={app.state.nef.sc.R_tb:.2f} Mbit/s")
    asyncio.run(serve(app, hc))


if __name__ == "__main__":
    main()
