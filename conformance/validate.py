"""API conformance check against the official 3GPP OpenAPI files (Release 18).

Validates, message by message, everything that was really exchanged during a run:
  northbound  AF <-> NEF   TS 29.122 AsSessionWithQoS     (requests the NEF accepted + all responses)
  southbound  NEF -> core  TS 29.514 Npcf_PolicyAuthorization, TS 29.521 Nbsf_Management,
                           TS 29.510 Nnrf_NFManagement / Nnrf_NFDiscovery
              (requests sent by our NEF = our conformance; responses from Open5GS = reported
               separately, informative: they measure the core, not our NEF)

Inputs : results/nef/northbound.jsonl, results/nef/sbi_exchanges.jsonl
Outputs: results/conformance/report.json, results/conformance/report.md

Usage:  python conformance/validate.py [--log-dir results/nef] [--specs conformance/specs/5GC_APIs]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from urllib.parse import parse_qsl, urlsplit

from openapi_core import Config, OpenAPI
from openapi_core.testing import MockRequest, MockResponse

# 3GPP APIs use JSON-based media types that openapi-core does not deserialize by default
JSON_TYPES = {t: json.loads for t in ("application/problem+json", "application/merge-patch+json",
                                       "application/json-patch+json")}

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SPECS = {
    "as_session_with_qos": ("TS29122_AsSessionWithQoS.yaml", "/3gpp-as-session-with-qos/v1"),
    "npcf_policyauthorization": ("TS29514_Npcf_PolicyAuthorization.yaml", "/npcf-policyauthorization/v1"),
    "nbsf_management": ("TS29521_Nbsf_Management.yaml", "/nbsf-management/v1"),
    "nnrf_nfm": ("TS29510_Nnrf_NFManagement.yaml", "/nnrf-nfm/v1"),
    "nnrf_disc": ("TS29510_Nnrf_NFDiscovery.yaml", "/nnrf-disc/v1"),
}
HOST = "https://example.com"     # apiRoot default of every 3GPP file


def api_for_path(path):
    for name, (_, prefix) in SPECS.items():
        if path.startswith(prefix):
            return name
    return None


def short(e):
    c = getattr(e, "__cause__", None)
    msg = f"{type(e).__name__}: {e}"
    if c is not None:
        sub = getattr(c, "schema_errors", None)
        if sub:
            msg += " | " + "; ".join(str(getattr(s, "message", s))[:200] for s in list(sub)[:3])
        else:
            msg += " | " + str(c)[:300]
    return msg[:600]


def mk_request(method, path, query, body, content_type=None):
    data = json.dumps(body).encode() if body is not None else None
    if body is not None and content_type is None:
        content_type = "application/merge-patch+json" if method.upper() == "PATCH" else "application/json"
    return MockRequest(HOST, method.lower(), path, args=dict(query or {}), data=data,
                       content_type=content_type if body is not None else None)


def mk_response(status, body, headers):
    ct = (headers or {}).get("content-type") or ("application/json" if body is not None else None)
    hdr = {}
    for k, v in (headers or {}).items():
        if k.lower() == "location":
            hdr["Location"] = v
    data = json.dumps(body).encode() if body is not None else b""
    return MockResponse(data, status_code=status, content_type=ct or "application/json", headers=hdr)


class Checker:
    def __init__(self, spec_dir):
        self.apis = {}
        for name, (fn, _) in SPECS.items():
            p = os.path.join(spec_dir, fn)
            if os.path.exists(p):
                self.apis[name] = OpenAPI.from_file_path(p, config=Config(extra_media_type_deserializers=JSON_TYPES))
        self.stats = collections.defaultdict(lambda: {"pass": 0, "fail": 0, "errors": []})

    def check(self, iface, direction, op, fn):
        key = f"{iface} | {op} | {direction}"
        try:
            fn()
            self.stats[key]["pass"] += 1
        except Exception as e:
            st = self.stats[key]
            st["fail"] += 1
            if len(st["errors"]) < 5:
                st["errors"].append(short(e))

    def run_northbound(self, recs):
        api = self.apis["as_session_with_qos"]
        for r in recs:
            path = r["path"]
            op = f"{r['method']} {_template(path)}"
            req = mk_request(r["method"], path, None, r.get("request"))
            if r["status"] < 400:   # requests the NEF accepted must be valid
                self.check("northbound TS29122", "AF request accepted by NEF", op, lambda: api.validate_request(req))
            else:                   # requests the NEF rejected: record whether the spec also rejects them
                try:
                    api.validate_request(req)
                    spec_view = "valid per OpenAPI (rejected by NEF semantics)"
                except Exception:
                    spec_view = "invalid per OpenAPI"
                self.stats[f"northbound TS29122 | {op} | rejected request ({r['status']}): {spec_view}"]["pass"] += 1
            resp = mk_response(r["status"], r.get("response"), r.get("headers"))
            self.check("northbound TS29122", "NEF response", op, lambda: api.validate_response(req, resp))

    def run_southbound(self, recs):
        for r in recs:
            u = urlsplit(r["url"])
            name = api_for_path(u.path)
            if name is None or name not in self.apis:
                continue
            api = self.apis[name]
            q = dict(parse_qsl(u.query))
            body = r.get("request")
            if isinstance(body, str):
                body = None
            op = f"{r['method']} {_template(u.path)}"
            req = mk_request(r["method"], u.path, q, body, r.get("request_content_type"))
            self.check(f"southbound {SPECS[name][0].split('_')[0]}", "request sent by NEF", op,
                       lambda: api.validate_request(req))
            if r.get("status") is not None:
                resp = mk_response(r["status"], r.get("response") if not isinstance(r.get("response"), str) else None,
                                   r.get("response_headers"))
                self.check(f"southbound {SPECS[name][0].split('_')[0]}", "core response (Open5GS, informative)", op,
                           lambda: api.validate_response(req, resp))


def _template(path):
    parts = path.strip("/").split("/")
    out = []
    for i, p in enumerate(parts):
        prev = parts[i - 1] if i else ""
        if prev in ("subscriptions", "app-sessions", "nf-instances", "pcfBindings") and p not in ("delete",):
            out.append("{id}")
        elif i == 2 and parts[0] == "3gpp-as-session-with-qos":
            out.append("{scsAsId}")
        else:
            out.append(p)
    return "/" + "/".join(out)


def read_jsonl(p):
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return [json.loads(line) for line in f if line.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", default=os.path.join(ROOT, "results", "nef"))
    ap.add_argument("--specs", default=os.path.join(HERE, "specs", "5GC_APIs"))
    ap.add_argument("--out", default=os.path.join(ROOT, "results", "conformance"))
    ap.add_argument("--since", type=float, default=None,
                    help="only messages logged after this UNIX time (the NEF logs are cumulative: without it, "
                         "every message of every past run is validated, which can take hours)")
    ap.add_argument("--max", type=int, default=3000, help="at most this many messages per direction (most recent)")
    args = ap.parse_args()
    if not os.path.isdir(args.specs):
        sys.exit("specs missing: run conformance/fetch_specs.sh first")
    ck = Checker(args.specs)
    nb = read_jsonl(os.path.join(args.log_dir, "northbound.jsonl"))
    sb = read_jsonl(os.path.join(args.log_dir, "sbi_exchanges.jsonl"))
    if args.since is not None:
        nb = [r for r in nb if r.get("t", 0) >= args.since]
        sb = [r for r in sb if r.get("t", 0) >= args.since]
    nb, sb = nb[-args.max:], sb[-args.max:]
    print(f"validating {len(nb)} northbound and {len(sb)} southbound messages", flush=True)
    ck.run_northbound(nb)
    ck.run_southbound(sb)
    os.makedirs(args.out, exist_ok=True)
    commit = ""
    cpath = os.path.join(os.path.dirname(args.specs), "COMMIT")
    if os.path.exists(cpath):
        commit = open(cpath).read().strip()
    rep = {"specs": "jdegre/5GC_APIs Rel-18", "commit": commit, "n_northbound": len(nb), "n_southbound": len(sb),
           "checks": dict(ck.stats)}
    with open(os.path.join(args.out, "report.json"), "w") as f:
        json.dump(rep, f, indent=2)
    lines = [f"# API conformance report\n", f"Specifications: 3GPP Rel-18 OpenAPI (jdegre/5GC_APIs, commit `{commit[:10]}`)  ",
             f"Messages: {len(nb)} northbound, {len(sb)} southbound\n",
             "| interface | operation | direction | pass | fail |", "|---|---|---|---|---|"]
    ours_fail = 0
    for key in sorted(ck.stats):
        st = ck.stats[key]
        parts = [p.strip() for p in key.split("|")]
        while len(parts) < 3:
            parts.append("")
        lines.append(f"| {parts[0]} | `{parts[1]}` | {parts[2]} | {st['pass']} | {st['fail']} |")
        if "informative" not in key:
            ours_fail += st["fail"]
    lines.append("")
    for key in sorted(ck.stats):
        if ck.stats[key]["errors"]:
            lines.append(f"**{key}**")
            lines += [f"- `{e}`" for e in ck.stats[key]["errors"]]
    lines.append(f"\n**NEF-side failures (requests sent + responses produced by our NEF): {ours_fail}**")
    with open(os.path.join(args.out, "report.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    sys.exit(1 if ours_fail else 0)


if __name__ == "__main__":
    main()
