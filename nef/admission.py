"""Capacity admission of the NEF (same rule as nef_pricing.admission, request by request).

Budget: sum of Delta_tb = b_tb - a_tb of the admitted sessions <= m2 capacity of the slot.
Rationing when the demand exceeds the budget:
  random   : first come, first served (the AFs send their requests in shuffled order)
  priority : requests collected during a short window, admitted by decreasing priority
             (sensitivity per unit of extra resource, signalled by the AF; same order as the
             simulation's 'priority' rule, enforced in 5G through ARP)
  qoe_gain : NEW variant: same window, admitted by decreasing class marginal QoE gain per
             unit of extra resource (g_i / Delta_i of the marginal-gain test), then priority.
"""
from __future__ import annotations

import asyncio
import time


class Admission:
    def __init__(self, window_s: float = 1.5):
        self.window = float(window_s)
        self.used = 0.0
        self.budget = 0.0
        self.mode = "random"
        self.lock = asyncio.Lock()
        self._batch = []
        self._timer = None

    def reset(self, budget: float, mode: str):
        self.budget, self.mode, self.used = float(budget), mode, 0.0

    async def request(self, delta: float, priority: float = 0.0, g_score: float = 0.0):
        """Returns (admitted, queue_ms)."""
        t0 = time.perf_counter()
        if self.mode == "random":
            async with self.lock:
                ok = self.used + delta <= self.budget + 1e-9
                if ok:
                    self.used += delta
            return ok, 1e3 * (time.perf_counter() - t0)
        fut = asyncio.get_running_loop().create_future()
        self._batch.append((delta, priority, g_score, fut))
        if self._timer is None:
            self._timer = asyncio.create_task(self._close_batch())
        ok = await fut
        return ok, 1e3 * (time.perf_counter() - t0)

    async def _close_batch(self):
        await asyncio.sleep(self.window)
        batch, self._batch, self._timer = self._batch, [], None
        if self.mode == "qoe_gain":
            batch.sort(key=lambda r: (-r[2], -r[1]))
        else:
            batch.sort(key=lambda r: -r[1])
        async with self.lock:
            for delta, _, _, fut in batch:
                ok = self.used + delta <= self.budget + 1e-9
                if ok:
                    self.used += delta
                fut.set_result(ok)

    async def release(self, delta: float):
        async with self.lock:
            self.used = max(self.used - delta, 0.0)
