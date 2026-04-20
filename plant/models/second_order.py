from __future__ import annotations


class SecondOrderPlant:
    def __init__(self, wn: float, zeta: float, gain: float = 1.0) -> None:
        self.wn = wn
        self.zeta = zeta
        self.gain = gain
        self.position = 0.0
        self.velocity = 0.0

    def step(self, control_input: float, dt_sec: float) -> float:
        acceleration = (self.wn ** 2) * ((self.gain * control_input) - self.position)
        acceleration -= 2.0 * self.zeta * self.wn * self.velocity
        self.velocity += dt_sec * acceleration
        self.position += dt_sec * self.velocity
        return self.position

