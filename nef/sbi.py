"""Southbound SBI client of the NEF (HTTP/2 cleartext with prior knowledge, as Open5GS).

Procedures (TS 29.522 clause 4.4.13 / TS 29.513 clause 8.5):
  1. NRF  Nnrf_NFManagement  : NEF registration + heartbeat              (TS 29.510)
  2. NRF  Nnrf_NFDiscovery   : discover the BSF                           (TS 29.510)
  3. BSF  Nbsf_Management    : GET pcfBindings?ipv4Addr=<UE IP> -> PCF   (TS 29.521)
  4. PCF  Npcf_PolicyAuthorization Create / Update / Delete              (TS 29.514)
Every exchange is timed and appended to sbi_exchanges.jsonl (used by the conformance
checker and by the latency breakdown).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

import httpx


class SbiError(Exception):
    def __init__(self, step, status, body=None):
        super().__init__(f"{step}: HTTP {status}")
        self.step, self.status, self.body = step, status, body


class SbiClient:
    def __init__(self, core_cfg: dict, nef_cfg: dict, log_path: str, mock_transport=None):
        self.core, self.nef = core_cfg, nef_cfg
        kw = {"timeout": float(nef_cfg.get("sbi_timeout_s", 5.0))}
        if mock_transport is not None:
            kw["transport"] = mock_transport
        else:
            kw.update(http1=False, http2=True)   # h2c prior knowledge (Open5GS SBI)
        self.http = httpx.AsyncClient(**kw)
        self.log_path = log_path
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        self._log = open(log_path, "a", buffering=1)
        self.bsf_uri = None
        self.bsf_source = None
        self.binding_cache = {}
        self.registered = False
        self._hb_task = None

    # ------------------------------------------------------------------ logging
    def _record(self, step, method, url, req_json, resp, dt_ms, extra=None):
        rec = {"t": time.time(), "step": step, "method": method, "url": str(url), "request": req_json,
               "status": resp.status_code if resp is not None else None,
               "response_headers": {k: v for k, v in (resp.headers.items() if resp is not None else [])
                                    if k.lower() in ("location", "content-type")},
               "response": _safe_json(resp), "ms": dt_ms}
        if extra:
            rec.update(extra)
        self._log.write(json.dumps(rec) + "\n")

    async def _req(self, step, method, url, **kw):
        t0 = time.perf_counter()
        resp = None
        try:
            resp = await self.http.request(method, url, **kw)
            return resp
        finally:
            dt = 1e3 * (time.perf_counter() - t0)
            body = kw.get("json")
            if body is None and kw.get("content"):
                try:
                    body = json.loads(kw["content"])
                except Exception:
                    body = None
            full = httpx.URL(url, params=kw.get("params")) if kw.get("params") else url
            self._record(step, method, full, body, resp, dt,
                         {"request_content_type": (kw.get("headers") or {}).get("content-type",
                                                   "application/json" if body is not None else None)})

    # ------------------------------------------------------------------ NRF
    def nf_profile(self) -> dict:
        host = self.nef.get("callback_base", "http://127.0.0.1:8080").split("//")[1].split(":")[0]
        return {"nfInstanceId": self.nef["nf_instance_id"], "nfType": "NEF", "nfStatus": "REGISTERED",
                "ipv4Addresses": [host], "heartBeatTimer": 10,
                "plmnList": [{"mcc": self.core["plmn"]["mcc"], "mnc": self.core["plmn"]["mnc"]}],
                "sNssais": [self.core["snssai"]],
                "nfServices": [{"serviceInstanceId": "nnef-afsessionwithqos-0",
                                "serviceName": "nnef-afsessionwithqos",
                                "versions": [{"apiVersionInUri": "v1", "apiFullVersion": "1.0.0"}],
                                "scheme": "http", "nfServiceStatus": "REGISTERED",
                                "ipEndPoints": [{"ipv4Address": host, "port": int(self.nef.get("listen_port", 8080))}]}]}

    async def nrf_register(self):
        url = f"{self.core['nrf_uri']}/nnrf-nfm/v1/nf-instances/{self.nef['nf_instance_id']}"
        r = await self._req("nrf_register", "PUT", url, json=self.nf_profile())
        self.registered = r.status_code in (200, 201)
        if self.registered and self._hb_task is None:
            self._hb_task = asyncio.create_task(self._heartbeat(url))
        return r.status_code

    async def _heartbeat(self, url):
        patch = [{"op": "replace", "path": "/nfStatus", "value": "REGISTERED"}]
        while True:
            await asyncio.sleep(8)
            try:
                await self.http.patch(url, content=json.dumps(patch),
                                      headers={"content-type": "application/json-patch+json"})
            except Exception:
                pass

    async def nrf_deregister(self):
        if self._hb_task:
            self._hb_task.cancel()
        if self.registered:
            url = f"{self.core['nrf_uri']}/nnrf-nfm/v1/nf-instances/{self.nef['nf_instance_id']}"
            try:
                await self._req("nrf_deregister", "DELETE", url)
            except Exception:
                pass

    async def discover_bsf(self) -> str:
        if self.bsf_uri:
            return self.bsf_uri
        for req_type in ("NEF", "AF", "PCF"):
            url = f"{self.core['nrf_uri']}/nnrf-disc/v1/nf-instances"
            try:
                r = await self._req("nrf_discover_bsf", "GET", url,
                                    params={"target-nf-type": "BSF", "requester-nf-type": req_type})
            except httpx.HTTPError:
                break
            if r.status_code == 200:
                ep = _first_endpoint(r.json(), "nbsf-management")
                if ep:
                    self.bsf_uri, self.bsf_source = ep, f"nrf(requester={req_type})"
                    return ep
        self.bsf_uri, self.bsf_source = self.core["bsf_uri"], "static"
        return self.bsf_uri

    # ------------------------------------------------------------------ BSF
    async def pcf_for_ue(self, ue_ip: str) -> str:
        if self.nef.get("cache_bsf") and ue_ip in self.binding_cache:
            return self.binding_cache[ue_ip]
        bsf = await self.discover_bsf()
        r = await self._req("bsf_discovery", "GET", f"{bsf}/nbsf-management/v1/pcfBindings",
                            params={"ipv4Addr": ue_ip})
        if r.status_code == 204:
            raise SbiError("bsf_discovery", 404, {"detail": f"no PCF binding for {ue_ip} (UE not attached?)"})
        if r.status_code != 200:
            raise SbiError("bsf_discovery", r.status_code, _safe_json(r))
        b = r.json()
        pcf = None
        for ep in b.get("pcfIpEndPoints") or []:
            if ep.get("ipv4Address"):
                pcf = f"http://{ep['ipv4Address']}:{ep.get('port', 7777)}"
                break
        if pcf is None:
            pcf = self.core["pcf_uri"]
        self.binding_cache[ue_ip] = pcf
        return pcf

    # ------------------------------------------------------------------ PCF
    async def app_session_create(self, pcf: str, asc: dict):
        r = await self._req("pcf_create", "POST", f"{pcf}/npcf-policyauthorization/v1/app-sessions", json=asc)
        if r.status_code not in (201, 200):
            raise SbiError("pcf_create", r.status_code, _safe_json(r))
        loc = r.headers.get("location")
        if not loc:
            raise SbiError("pcf_create", 500, {"detail": "PCF answered without Location header"})
        if loc.startswith("/"):
            loc = pcf + loc
        return loc, _safe_json(r)

    async def app_session_update(self, loc: str, patch: dict):
        r = await self._req("pcf_update", "PATCH", loc, content=json.dumps(patch),
                            headers={"content-type": "application/merge-patch+json"})
        if r.status_code not in (200, 204):
            raise SbiError("pcf_update", r.status_code, _safe_json(r))

    async def app_session_delete(self, loc: str):
        r = await self._req("pcf_delete", "POST", f"{loc}/delete")
        if r.status_code not in (200, 204):
            raise SbiError("pcf_delete", r.status_code, _safe_json(r))

    async def close(self):
        await self.nrf_deregister()
        await self.http.aclose()
        self._log.close()


def _safe_json(resp):
    if resp is None:
        return None
    try:
        return resp.json()
    except Exception:
        return resp.text[:500] if resp.text else None


def _first_endpoint(search_result: dict, service: str):
    for inst in search_result.get("nfInstances", []):
        services = inst.get("nfServices") or []
        if not services and isinstance(inst.get("nfServiceList"), dict):
            services = list(inst["nfServiceList"].values())
        for svc in services:
            if svc.get("serviceName") != service:
                continue
            scheme = svc.get("scheme", "http")
            for ep in svc.get("ipEndPoints", []):
                if ep.get("ipv4Address"):
                    return f"{scheme}://{ep['ipv4Address']}:{ep.get('port', 80 if scheme == 'http' else 443)}"
        for ip in inst.get("ipv4Addresses", []):
            return f"http://{ip}:7777"
    return None


def new_id() -> str:
    return uuid.uuid4().hex[:16]
