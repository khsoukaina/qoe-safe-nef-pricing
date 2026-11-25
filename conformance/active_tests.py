"""Active conformance tests against the running NEF (positive and negative cases).

Every exchange is logged by the NEF and then validated by conformance/validate.py against the
3GPP OpenAPI files; this script additionally asserts the expected behaviour (status codes,
Location header, ProblemDetails, life cycle). Run it with UEs attached (southbound=core):
the UE address given must have a PDU session, otherwise the PCF part cannot succeed.

Usage:  python conformance/active_tests.py --nef http://192.168.250.10:8080 --ue 10.45.0.2
"""
from __future__ import annotations

import argparse
import json
import sys

import httpx

API = "/3gpp-as-session-with-qos/v1"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nef", default="http://127.0.0.1:8080")
    ap.add_argument("--ue", required=True, help="IPv4 address of an attached UE")
    ap.add_argument("--dn", default="10.60.0.1")
    ap.add_argument("--slot", type=int, default=17, help="slot used for the test (QoS must be open)")
    ap.add_argument("--policy", default="quantile")
    args = ap.parse_args()
    c = httpx.Client(base_url=args.nef, timeout=10)
    results = []

    def expect(name, cond, info=""):
        results.append((name, bool(cond), info))
        print(f"[{'PASS' if cond else 'FAIL'}] {name} {info}")

    c.post("/lab/v1/config", json={"policy": args.policy, "rationing": "random", "run": "conformance"})
    st = c.post("/lab/v1/slot", json={"slot": args.slot}).json()
    expect("prices posted for the slot", st.get("prices") is not None, json.dumps(st.get("prices")))

    sub = {"notificationDestination": "http://127.0.0.1:9999/af-notify",
           "ueIpv4Addr": args.ue, "qosReference": "QOD_VOICE",
           "flowInfo": [{"flowId": 1, "flowDescriptions": [f"permit out 17 from {args.dn} 5000 to {args.ue} 41000",
                                                            f"permit in 17 from {args.ue} 41000 to {args.dn} 5000"]}],
           "supportedFeatures": "0"}
    r = c.post(f"{API}/af-conf/subscriptions", json=sub)
    expect("POST valid subscription -> 201", r.status_code == 201, f"got {r.status_code} {r.text[:200]}")
    loc = r.headers.get("location", "")
    expect("Location header present", loc.startswith("http"), loc)
    expect("body.self == Location", r.status_code == 201 and r.json().get("self") == loc)
    expect("X-Nef-Timing header (decision latency)", "total_ms" in r.headers.get("x-nef-timing", ""),
           r.headers.get("x-nef-timing", ""))
    sid = loc.rstrip("/").split("/")[-1] if loc else "none"
    r = c.get(f"{API}/af-conf/subscriptions/{sid}")
    expect("GET individual -> 200", r.status_code == 200)
    r = c.get(f"{API}/af-conf/subscriptions")
    expect("GET collection -> 200 and contains it", r.status_code == 200 and any(x.get("self") == loc for x in r.json()))
    r = c.patch(f"{API}/af-conf/subscriptions/{sid}", json={"qosReference": "QOD_WEB"},
                headers={"content-type": "application/merge-patch+json"})
    expect("PATCH qosReference -> 200 (PCF update)", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
    r = c.delete(f"{API}/af-conf/subscriptions/{sid}")
    expect("DELETE -> 204 (PCF delete, GBR class released)", r.status_code == 204, str(r.status_code))
    r = c.get(f"{API}/af-conf/subscriptions/{sid}")
    expect("GET after DELETE -> 404 ProblemDetails", r.status_code == 404 and "status" in r.json())

    neg = [("missing notificationDestination", {k: v for k, v in sub.items() if k != "notificationDestination"}),
           ("invalid UE address", {**sub, "ueIpv4Addr": "300.1.1.1"}),
           ("unknown qosReference", {**sub, "qosReference": "QOS_UNKNOWN"}),
           ("missing flowInfo", {k: v for k, v in sub.items() if k != "flowInfo"})]
    for name, body in neg:
        r = c.post(f"{API}/af-conf/subscriptions", json=body)
        ok = r.status_code == 400 and r.headers.get("content-type", "").startswith("application/problem+json") \
            and r.json().get("invalidParams")
        expect(f"POST {name} -> 400 ProblemDetails+invalidParams", ok, f"{r.status_code}")
    r = c.post(f"{API}/af-conf/subscriptions", content=b"{not json", headers={"content-type": "application/json"})
    expect("POST malformed JSON -> 400", r.status_code == 400)
    r = c.delete(f"{API}/af-conf/subscriptions/doesnotexist")
    expect("DELETE unknown -> 404", r.status_code == 404)
    r = c.get("/nef-pricing/v1/prices")
    expect("GET /nef-pricing/v1/prices -> 200", r.status_code == 200)

    n_ok = sum(ok for _, ok, _ in results)
    print(f"\n{n_ok}/{len(results)} behavioural checks passed. Now run: python conformance/validate.py")
    sys.exit(0 if n_ok == len(results) else 1)


if __name__ == "__main__":
    main()
