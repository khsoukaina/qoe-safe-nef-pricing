"""Summarise the core signalling capture: proof that each NEF grant really travelled
NEF -> BSF -> PCF (N5) -> SMF (N7 policy update) -> UPF (PFCP modification with Create QER).

Usage (core container):  python3 conformance/core_evidence.py results/core_logs/core_signalling.pcap
Output: results/conformance/core_evidence.md (+ printed)
"""
import collections
import os
import subprocess
import sys

PFCP_MSG = {1: "Heartbeat Request", 2: "Heartbeat Response", 5: "Association Setup Request",
            6: "Association Setup Response", 50: "Session Establishment Request", 51: "Session Establishment Response",
            52: "Session Modification Request", 53: "Session Modification Response",
            54: "Session Deletion Request", 55: "Session Deletion Response"}
PFCP_IE = {1: "Create PDR", 3: "Create FAR", 7: "Create QER", 9: "Update PDR", 13: "Update QER",
           15: "Remove PDR", 17: "Remove QER"}


def tshark(pcap, flt, fields):
    cmd = ["tshark", "-r", pcap, "-d", "tcp.port==7777,http2", "-Y", flt, "-T", "fields", "-E", "separator=|"]
    for f in fields:
        cmd += ["-e", f]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        print(out.stderr[:500])
    return [line.split("|") for line in out.stdout.splitlines() if line.strip()]


def main():
    pcap = sys.argv[1] if len(sys.argv) > 1 else "/lab/results/core_logs/core_signalling.pcap"
    lines = ["# Core signalling evidence", f"capture: `{pcap}`", ""]
    rows = tshark(pcap, "http2.header.name == \":path\"", ["frame.time_relative", "http2.headers.method", "http2.headers.path"])
    paths = collections.Counter()
    for r in rows:
        if len(r) >= 3 and r[2]:
            meth = r[1].split(",")[0] if r[1] else ""
            p = r[2].split(",")[0].split("?")[0]
            parts = p.strip("/").split("/")
            key = "/".join(parts[:3]) + ("/{id}" if len(parts) > 3 else "") + ("/" + parts[-1] if parts[-1] in ("delete", "notify", "update") else "")
            paths[f"{meth} {key}".strip()] += 1
    lines += ["## SBI (HTTP/2) requests", "| request | count |", "|---|---|"]
    lines += [f"| `{k}` | {v} |" for k, v in paths.most_common()]
    rows = tshark(pcap, "pfcp", ["pfcp.msg_type", "pfcp.ie_type"])
    msgs, ies = collections.Counter(), collections.Counter()
    for r in rows:
        mt = int(r[0].split(",")[0]) if r[0] else -1
        msgs[PFCP_MSG.get(mt, f"type {mt}")] += 1
        if mt == 52 and len(r) > 1:
            for ie in set(r[1].split(",")):
                if ie and int(ie) in PFCP_IE:
                    ies[PFCP_IE[int(ie)]] += 1
    lines += ["", "## PFCP (SMF -> UPF)", "| message | count |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in msgs.most_common()]
    lines += ["", "IEs inside Session Modification Requests:", "| IE | messages containing it |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in ies.most_common()]
    rows = tshark(pcap, "ngap", ["ngap.procedureCode"])
    proc = collections.Counter(r[0].split(",")[0] for r in rows if r and r[0])
    names = {"29": "PDUSessionResourceSetup", "26": "PDUSessionResourceModify", "28": "PDUSessionResourceRelease",
             "4": "DownlinkNASTransport", "46": "UplinkNASTransport", "14": "InitialContextSetup", "15": "InitialUEMessage"}
    lines += ["", "## NGAP (AMF <-> gNB)", "| procedure | messages |", "|---|---|"]
    lines += [f"| {names.get(k, k)} | {v} |" for k, v in proc.most_common()]
    os.makedirs("/lab/results/conformance", exist_ok=True)
    with open("/lab/results/conformance/core_evidence.md", "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
