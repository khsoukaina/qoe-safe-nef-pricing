"""Measured network performance -> MOS, with the SAME QoE models as the simulation
(vendor/nef_pricing/qoe_models.py: ITU-T G.107 E-model, ITU-T P.1203 mode 0 when installed,
IQX gaming form, logarithmic web form).

Additional standard indicators reported because a population mean hides who is affected:
  * G.107 Annex B: %GoB (good or better) and %PoW (poor or worse) from the R factor (voice);
  * dissatisfied = MOS < 3.0 (all classes), and the 5th-percentile MOS.
"""
from __future__ import annotations

import math

import numpy as np

from labcommon import VENDOR  # noqa: F401  (puts the vendored package on sys.path)
from nef_pricing import qoe_models

DISSATISFIED = 3.0


def _phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def r_factor(delay_ms, loss, Ie=0.0, Bpl=25.1):
    d = float(delay_ms)
    Id = 0.024 * d + 0.11 * (d - 177.3) * (d > 177.3)
    Ppl = 100.0 * float(loss)
    return 93.2 - Id - (Ie + (95 - Ie) * Ppl / (Ppl + Bpl))


def voice_mos(raw, vcfg):
    if raw.get("n_sent", 0) == 0:
        return None
    dl = np.asarray(raw["owd_dl_ms"], float)
    ul = np.asarray(raw["owd_ul_ms"], float)
    if len(dl) == 0:
        return {"mos": 1.0, "loss": 1.0, "delay_ms": None, "R": 0.0, "gob": 0.0, "pow": 100.0}
    jb = float(vcfg["jitter_buffer_ms"])
    base = float(np.percentile(dl, 5))
    late = float(np.mean(dl > base + jb))
    lost = 1.0 - raw["n_recv"] / raw["n_sent"]
    loss = min(1.0, lost + (1 - lost) * late)
    # mouth-to-ear: codec + jitter buffer + mean one-way network delay (uplink + downlink legs / 2)
    owd = float(np.mean(dl)) if len(ul) == 0 else 0.5 * (float(np.mean(dl)) + float(np.mean(ul)))
    T = float(vcfg["codec_delay_ms"]) + jb + owd
    R = r_factor(T, loss)
    return {"mos": float(qoe_models.emodel_mos(T, loss)), "loss": loss, "delay_ms": T, "R": R,
            "gob": 100 * _phi((R - 60) / 16), "pow": 100 * _phi((45 - R) / 16),
            "jitter_ms": float(np.std(dl))}


def gaming_mos(raw):
    rtt = np.asarray(raw.get("rtt_ms", []), float)
    if len(rtt) == 0:
        return {"mos": 1.0, "rtt_ms": None, "loss": 1.0}
    loss = float(raw.get("stream_loss", 0.0))
    r = float(np.mean(rtt))
    return {"mos": float(qoe_models.gaming_mos(r, loss)), "rtt_ms": r, "rtt_p95_ms": float(np.percentile(rtt, 95)),
            "loss": loss}


def video_mos(raw):
    segs = raw.get("segments") or []
    if not segs:
        return {"mos": 1.0, "stall_s": raw.get("stall_s"), "mean_kbps": 0.0}
    stall = float(raw.get("stall_s", 0.0))
    kb = [s["kbps_real"] for s in segs]
    if qoe_models.HAS_P1203:
        res = dict(qoe_models.LADDER_KBPS)
        seg_d = 2
        segments = [{"bitrate": k, "codec": "h264", "duration": seg_d, "fps": 30, "resolution": res[k],
                     "start": seg_d * i} for i, k in enumerate(kb)]
        stalls = [] if stall <= 0.05 else [[0, round(stall, 2)]]
        inp = {"I11": {"segments": [{"bitrate": 128, "codec": "aaclc", "duration": seg_d * len(kb), "start": 0}],
                       "streamId": 1},
               "I13": {"segments": segments, "streamId": 1}, "I23": {"stalling": stalls, "streamId": 1},
               "IGen": {"device": "mobile", "displaySize": "1920x1080", "viewingDistance": "150cm"}}
        mos = float(qoe_models.P1203Standalone(inp, quiet=True).calculate_complete()["O46"])
        model = "P.1203"
    else:
        q = np.mean([1 + 3.5 * np.log(1 + k / 400.0) / np.log(1 + 5000 / 400.0) for k in kb])
        stall_60 = 60.0 * stall / max(raw.get("measured_s", 60.0), 1.0)
        mos = float(1.0 + (q - 1.0) * np.exp(-0.15 * stall_60))
        model = "fallback"
    return {"mos": mos, "stall_s": stall, "mean_kbps": float(np.mean(kb)), "model": model,
            "switches": int(np.sum(np.diff(kb) != 0))}


def web_mos(raw):
    plt = np.asarray(raw.get("plt_s", []), float)
    if len(plt) == 0:
        return {"mos": 1.0, "plt_s": None}
    return {"mos": float(np.mean(qoe_models.web_mos(plt))), "plt_s": float(np.median(plt)),
            "plt_p90_s": float(np.percentile(plt, 90))}


def session_qoe(rec, tcfg):
    raw = rec.get("raw") or {}
    if "error" in raw:
        return {"mos": None, "error": raw["error"]}
    c = rec["class"]
    if c == "voice":
        return voice_mos(raw, tcfg["voice"]) or {"mos": None}
    if c == "gaming":
        return gaming_mos(raw)
    if c == "video":
        return video_mos(raw)
    if c == "web":
        return web_mos(raw)
    raise ValueError(c)
