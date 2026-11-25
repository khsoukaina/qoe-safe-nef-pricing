"""Per slot x class diagnostic of a testbed run (sessions, grants, MOS, failures, raw metrics).

Usage:  python3 analysis/diagnose.py results/testbed/<tag>
"""
import collections
import json
import statistics as st
import sys

rows = [json.loads(l) for l in open(sys.argv[1].rstrip("/") + "/sessions.jsonl")]
for pol in sorted({r["policy"] for r in rows}, key=lambda p: (p != "no_nef", p)):
    print("=====", pol)
    g = collections.defaultdict(list)
    for r in rows:
        if r["policy"] == pol:
            g[(r["seed"], r["slot"], r["class"])].append(r)
    for (sd, t, c), rs in sorted(g.items()):
        q = [r["qoe"] for r in rs]
        m = [x["mos"] for x in q if x.get("mos") is not None]
        mg = [r["qoe"]["mos"] for r in rs if r["granted"] and r["qoe"].get("mos") is not None]
        mb = [r["qoe"]["mos"] for r in rs if not r["granted"] and r["qoe"].get("mos") is not None]
        err = sum(1 for x in q if x.get("error") or x.get("mos") is None)
        ex = {}
        for k in ("delay_ms", "loss", "rtt_ms", "mean_kbps", "stall_s", "plt_s"):
            vals = [x.get(k) for x in q if x.get(k) is not None]
            if vals:
                ex[k] = round(st.mean(vals), 3)
        print(f"s{sd} {t}h {c:6s} n={len(rs):2d} granted={len(mg):2d} MOS={st.mean(m) if m else float('nan'):.2f} "
              f"(granted {st.mean(mg) if mg else float('nan'):.2f} / best-effort {st.mean(mb) if mb else float('nan'):.2f}) "
              f"errors={err} {ex}")
