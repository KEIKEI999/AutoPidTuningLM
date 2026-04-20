from __future__ import annotations


class FirstOrderPlant:
    def __init__(self, gain: float, tau: float) -> None:
        self.gain = gain
        self.tau = tau
        self.state = 0.0

    def step(self, control_input: float, dt_sec: float) -> float:
        self.state += dt_sec * ((-self.state + (self.gain * control_input)) / self.tau)
        return self.state

