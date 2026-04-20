from __future__ import annotations

from collections import deque


class DeadtimeBuffer:
    def __init__(self, deadtime_sec: float, dt_sec: float) -> None:
        self.steps = max(0, int(round(deadtime_sec / dt_sec)))
        self._queue = deque([0.0] * self.steps, maxlen=self.steps or 1)

    def push(self, value: float) -> float:
        if self.steps == 0:
            return value
        self._queue.append(value)
        return self._queue.popleft()

