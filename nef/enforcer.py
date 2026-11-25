"""User-plane enforcement of granted sessions (emulated radio scheduler).

Downlink bottleneck of capacity R_tb on the UPF tunnel device (ogstun egress = traffic going
to the UEs):

    1:   htb root (default -> 1:999)
    1:1  parent class, rate = ceil = R_tb
    1:999 best effort, rate = max(R_tb - sum(GBR), 0.1) ceil = R_tb, queue of be_limit_pkts
    1:k  one class per granted session, rate = 1.1 b_tb (GBR + header headroom), ceil = R_tb
         (borrows idle capacity), prio 0 (served first),
         u32 filter on the UE IPv4 address

Leaf queues are netem (fixed base delay = RAN + transport, base_delay_ms, applied to every
class) or pfifo when base_delay_ms = 0. Queueing delay and losses then come only from the
congestion of the best-effort class, as in the simulation.

v2 (fix of the latency knee): installing a class is NOT on the NEF decision path any more.
In a 5G system the NEF answers the AF once the PCF has accepted the policy; programming the
radio scheduler happens afterwards (SMF -> gNB). The NEF therefore only *enqueues* the
operation; a single worker applies all pending operations in ONE `tc -batch` call (instead of
4-5 process spawns per request, serialised under a lock, which capped the NEF at ~15 req/s).
The activation delay (enqueue -> applied) is measured and logged separately
(event "enforced", field enf_apply_ms). POST /lab/v1/enforcer/flush waits until everything
queued is applied (used by the scenario before starting traffic).

Modes: "tc" (real), "dry" (commands logged, nothing executed), "off" (no shaping at all).
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time


class Enforcer:
    def __init__(self, cfg: dict, capacity_mbps: float, log=None):
        self.mode = cfg.get("mode", "tc")
        self.dev = cfg.get("dev", "ogstun")
        self.R = float(capacity_mbps)
        self.be_limit = int(cfg.get("be_limit_pkts", 300))
        self.gbr_limit = int(cfg.get("gbr_limit_pkts", 100))
        self.base_delay = float(cfg.get("base_delay_ms", 0.0))
        # v3: a guaranteed flow gets AT LEAST b (x headroom for IP/UDP/TCP headers) and may borrow
        # idle capacity (ceil = R, served first: prio 0), like a GBR flow on a radio scheduler.
        # v2 capped it at exactly b (ceil = b): a 5 Mbit/s payload stream needs ~5.1 Mbit/s on the
        # wire, so the queue grew to its limit (RTT ~200 ms, MOS 1.3 for "guaranteed" gaming) and
        # guaranteed web pages were slower than best-effort ones at low load (negative D).
        self.headroom = float(cfg.get("gbr_headroom", 1.10))
        self.borrow = bool(cfg.get("gbr_borrow", True))
        self.classes = {}           # sub_id -> (minor, rate)
        self.free = list(range(10, 9990))
        self.cmd_log = []
        self.apply_ms = []          # activation delays (enqueue -> applied)
        self.log = log              # callable(dict) for the decision log
        self.q = None
        self.worker = None
        self.errors = 0
        if self.mode == "tc" and shutil.which("tc") is None:
            raise RuntimeError("enforcer mode 'tc' needs iproute2 (tc) and NET_ADMIN")

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _rate(mbps: float) -> str:
        return f"{max(int(round(mbps * 1000)), 8)}kbit"

    def _leaf(self, limit):
        if self.base_delay > 0:
            return f"netem delay {self.base_delay:g}ms limit {limit}"
        return f"pfifo limit {limit}"

    def gbr_total(self) -> float:
        return sum(r for _, r in self.classes.values()) * self.headroom

    async def _batch(self, lines):
        """Run tc commands in one process (tc -force -batch -). Returns (rc, stderr)."""
        self.cmd_log.extend("tc " + ln for ln in lines)
        if len(self.cmd_log) > 2000:
            self.cmd_log = self.cmd_log[-1000:]
        if self.mode != "tc" or not lines:
            return 0, ""
        p = await asyncio.create_subprocess_exec("tc", "-force", "-batch", "-", stdin=asyncio.subprocess.PIPE,
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, err = await p.communicate(("\n".join(lines) + "\n").encode())
        return p.returncode, err.decode().strip()

    # ------------------------------------------------------------------ setup
    async def setup(self):
        if self.mode == "off":
            return
        if self.q is not None:
            await self.flush()
        d = self.dev
        await self._batch([f"qdisc del dev {d} root"])           # may fail if absent: ignored (-force)
        rc, err = await self._batch([
            f"qdisc add dev {d} root handle 1: htb default 999 r2q 10",
            f"class add dev {d} parent 1: classid 1:1 htb rate {self._rate(self.R)} ceil {self._rate(self.R)}",
            f"class add dev {d} parent 1:1 classid 1:999 htb rate {self._rate(self.R)} ceil {self._rate(self.R)} prio 1",
            f"qdisc add dev {d} parent 1:999 handle 999: {self._leaf(self.be_limit)}"])
        if rc != 0:
            raise RuntimeError(f"tc setup failed: {err}")
        self.classes.clear()
        self.free = list(range(10, 9990))
        if self.q is None:
            self.q = asyncio.Queue()
            self.worker = asyncio.create_task(self._work())

    # ------------------------------------------------------------------ public API (non-blocking)
    def grant(self, sub_id: str, ue_ip: str, rate_mbps: float) -> float:
        """Enqueue the installation of the guaranteed class; returns the (tiny) enqueue time in ms."""
        if self.mode == "off" or self.q is None:
            return 0.0
        t0 = time.perf_counter()
        minor = self.free.pop(0)
        self.classes[sub_id] = (minor, float(rate_mbps))
        self.q.put_nowait(("grant", sub_id, minor, ue_ip, float(rate_mbps), t0))
        return 1e3 * (time.perf_counter() - t0)

    def release(self, sub_id: str) -> float:
        if self.mode == "off" or self.q is None or sub_id not in self.classes:
            return 0.0
        t0 = time.perf_counter()
        minor, _ = self.classes.pop(sub_id)
        self.free.append(minor)        # reuse is safe: the batch keeps the queue order (del before add)
        self.q.put_nowait(("release", sub_id, minor, None, 0.0, t0))
        return 1e3 * (time.perf_counter() - t0)

    async def flush(self, timeout: float = 30.0):
        if self.q is not None:
            await asyncio.wait_for(self.q.join(), timeout)

    # ------------------------------------------------------------------ worker
    async def _work(self):
        d = self.dev
        while True:
            ops = [await self.q.get()]
            while not self.q.empty():
                ops.append(self.q.get_nowait())
            lines = []
            for kind, sid, minor, ip, rate, _ in ops:
                if kind == "grant":
                    r = self._rate(rate * self.headroom)
                    ceil = self._rate(self.R) if self.borrow else r
                    lines += [f"class add dev {d} parent 1:1 classid 1:{minor} htb rate {r} ceil {ceil} prio 0",
                              f"qdisc add dev {d} parent 1:{minor} handle {minor}: {self._leaf(self.gbr_limit)}",
                              f"filter add dev {d} parent 1: protocol ip prio {minor} u32 match ip dst {ip}/32 flowid 1:{minor}"]
                else:
                    lines += [f"filter del dev {d} parent 1: protocol ip prio {minor}",
                              f"qdisc del dev {d} parent 1:{minor}",
                              f"class del dev {d} classid 1:{minor}"]
            be = max(self.R - self.gbr_total(), 0.1)
            lines.append(f"class change dev {d} parent 1:1 classid 1:999 htb rate {self._rate(be)} ceil {self._rate(self.R)} prio 1")
            try:
                rc, err = await self._batch(lines)
                if rc != 0:
                    self.errors += 1
                    print(f"[enforcer] tc batch rc={rc}: {err[:300]}", flush=True)
            except Exception as e:  # never kill the worker
                self.errors += 1
                print(f"[enforcer] {e}", flush=True)
            now = time.perf_counter()
            for kind, sid, *_rest, t0 in ops:
                ms = 1e3 * (now - t0)
                if kind == "grant":
                    self.apply_ms.append(ms)
                if self.log:
                    self.log({"event": "enforced" if kind == "grant" else "unenforced", "sid": sid,
                              "enf_apply_ms": ms, "batch_size": len(ops)})
            for _ in ops:
                self.q.task_done()

    # ------------------------------------------------------------------ stats
    async def stats(self) -> dict:
        """Bytes / drops / backlog per class (tc -s -j), {} when unavailable."""
        if self.mode != "tc":
            return {}
        p = await asyncio.create_subprocess_exec("tc", "-s", "-j", "class", "show", "dev", self.dev,
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await p.communicate()
        try:
            res = {}
            for c in json.loads(out.decode() or "[]"):
                st = c.get("stats", {})
                res[c.get("handle")] = {"bytes": st.get("bytes", 0), "packets": st.get("packets", 0),
                                        "drops": st.get("drops", 0), "backlog": st.get("backlog", 0)}
            return res
        except Exception:
            return {}
