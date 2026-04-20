from __future__ import annotations

import random


class NoiseModel:
    def __init__(self, noise_type: str, stddev: float, rng: random.Random) -> None:
        self.noise_type = noise_type
        self.stddev = stddev
        self.rng = rng

    def apply(self, value: float) -> float:
        if self.noise_type == "gaussian" and self.stddev > 0:
            return value + self.rng.gauss(0.0, self.stddev)
        return value

