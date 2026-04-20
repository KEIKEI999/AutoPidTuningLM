from __future__ import annotations

import math


class NonlinearTransform:
    def __init__(self, kind: str, gain: float) -> None:
        self.kind = kind
        self.gain = gain

    def apply(self, value: float) -> float:
        if self.kind == "tanh":
            gain = self.gain if self.gain != 0 else 1.0
            return math.tanh(gain * value)
        return value

