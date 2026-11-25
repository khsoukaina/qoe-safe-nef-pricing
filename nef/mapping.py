"""Northbound -> southbound mapping (3GPP TS 29.522 / TS 29.122 -> TS 29.514).

An AsSessionWithQoSSubscription (TS 29.122 clause 5.14) received from the AF is turned into
an AppSessionContext (TS 29.514, Npcf_PolicyAuthorization_Create):
    ueIpv4Addr                         -> ascReqData.ueIpv4
    flowInfo[k].flowDescriptions       -> medComponents["1"].medSubComps[flowId].fDescs
    qosReference (operator profile)    -> medComponents["1"].medType + marBw/mirBw (DL/UL)
    dnn / snssai                       -> ascReqData.dnn / ascReqData.sliceInfo
    (NEF-generated) callback           -> ascReqData.notifUri
TS 29.522 lets the NEF forward qosReference as-is; Open5GS's PCF does not resolve QoS
references, so this NEF resolves the profile itself and sends explicit bit rates (an
implementation choice that is allowed and that keeps the PCF configuration minimal).
"""
from __future__ import annotations

import re

BITRATE_RE = re.compile(r"^\d+(\.\d+)? (bps|Kbps|Mbps|Gbps|Tbps)$")
IPV4_RE = re.compile(r"^(([0-9]|[1-9][0-9]|1[0-9][0-9]|2[0-4][0-9]|25[0-5])\.){3}"
                     r"([0-9]|[1-9][0-9]|1[0-9][0-9]|2[0-4][0-9]|25[0-5])$")


def bitrate(mbps: float) -> str:
    """BitRate string of TS 29.571 (e.g. '2.5 Mbps'); always uses Kbps for sub-Mbps rates."""
    if mbps < 1.0:
        return f"{round(mbps * 1000.0, 3):g} Kbps"
    return f"{round(mbps, 3):g} Mbps"


def check_subscription(body: dict, profiles: dict) -> list:
    """Semantic checks of a creation request. Returns a list of InvalidParam dicts."""
    bad = []
    if not isinstance(body, dict):
        return [{"param": "body", "reason": "JSON object expected"}]
    if not body.get("notificationDestination"):
        bad.append({"param": "notificationDestination", "reason": "mandatory attribute missing"})
    ip = body.get("ueIpv4Addr")
    if not ip:
        bad.append({"param": "ueIpv4Addr", "reason": "this NEF supports IPv4 UE addresses only"})
    elif not IPV4_RE.match(str(ip)):
        bad.append({"param": "ueIpv4Addr", "reason": "invalid IPv4 address"})
    ref = body.get("qosReference")
    if not ref:
        bad.append({"param": "qosReference", "reason": "mandatory for this NEF"})
    elif ref not in profiles:
        bad.append({"param": "qosReference", "reason": f"unknown QoS reference {ref}"})
    fi = body.get("flowInfo")
    if not fi or not isinstance(fi, list):
        bad.append({"param": "flowInfo", "reason": "at least one IP flow is required"})
    else:
        for k, f in enumerate(fi):
            if "flowId" not in f:
                bad.append({"param": f"flowInfo[{k}].flowId", "reason": "missing"})
            fd = f.get("flowDescriptions") or []
            if not (1 <= len(fd) <= 2) or not all(str(d).startswith("permit") for d in fd):
                bad.append({"param": f"flowInfo[{k}].flowDescriptions",
                            "reason": "1 or 2 IPFilterRule strings ('permit out|in ...') expected"})
    return bad


def to_app_session_context(sub: dict, prof: dict, notif_uri: str, nef_cfg: dict, core_cfg: dict) -> dict:
    med_sub = {}
    for f in sub["flowInfo"]:
        fid = int(f["flowId"])
        med_sub[str(fid)] = {"fNum": fid, "fDescs": list(f["flowDescriptions"]), "fStatus": "ENABLED",
                             "flowUsage": "NO_INFO"}
    med = {"medCompN": 1, "medType": prof["medType"], "fStatus": "ENABLED",
           "marBwDl": bitrate(prof["b_tb"]), "marBwUl": bitrate(prof["ul_mbps"]),
           "mirBwDl": bitrate(prof["b_tb"]), "mirBwUl": bitrate(prof["ul_mbps"]),
           "medSubComps": med_sub}
    req = {"afAppId": nef_cfg.get("af_app_id", "nef"),
           "notifUri": notif_uri,
           "suppFeat": str(nef_cfg.get("supp_feat", "0")),
           "ueIpv4": sub["ueIpv4Addr"],
           "dnn": sub.get("dnn") or core_cfg["dnn"],
           "sliceInfo": sub.get("snssai") or core_cfg["snssai"],
           "medComponents": {"1": med}}
    return {"ascReqData": req}


def update_patch(prof: dict) -> dict:
    """AppSessionContextUpdateDataPatch for a qosReference change (Npcf_PolicyAuthorization_Update)."""
    return {"ascReqData": {"medComponents": {"1": {
        "medCompN": 1, "medType": prof["medType"],
        "marBwDl": bitrate(prof["b_tb"]), "marBwUl": bitrate(prof["ul_mbps"]),
        "mirBwDl": bitrate(prof["b_tb"]), "mirBwUl": bitrate(prof["ul_mbps"])}}}}


def problem(status: int, title: str, detail: str = None, cause: str = None, invalid=None) -> dict:
    """ProblemDetails (TS 29.122 clause 5.2.1.2.12 / TS 29.571)."""
    d = {"title": title, "status": status}
    if detail:
        d["detail"] = detail
    if cause:
        d["cause"] = cause
    if invalid:
        d["invalidParams"] = invalid
    return d
