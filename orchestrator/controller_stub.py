from __future__ import annotations

from dataclasses import dataclass

from orchestrator.can_codec import (
    CanCodecError,
    pack_control_output,
    pack_heartbeat,
    pack_status,
    unpack_measurement,
    unpack_setpoint,
)
from orchestrator.can_if import VirtualCanHandle
from orchestrator.can_map import (
    CAN_ID_HEARTBEAT,
    CAN_ID_MEASUREMENT_FB,
    CAN_ID_SETPOINT_CMD,
    CAN_NODE_ID_CONTROLLER,
    CAN_STATE_RUNNING,
)
from orchestrator.models import PIDGains


@dataclass
class ControllerStepResult:
    control_output: float
    saturated: bool


class PIDControllerStub:
    def __init__(self, gains: PIDGains, dt_sec: float, control_limit: float) -> None:
        self.gains = gains
        self.dt_sec = dt_sec
        self.control_limit = control_limit
        self.setpoint = 0.0
        self.integral = 0.0
        self.prev_error = 0.0
        self.last_output = 0.0
        self.alive_counter = 0

    def step(self, handle: VirtualCanHandle, timestamp_ms: int) -> ControllerStepResult:
        measurement = None
        for frame in handle.drain():
            try:
                if frame.id == CAN_ID_SETPOINT_CMD:
                    self.setpoint = unpack_setpoint(frame)
                elif frame.id == CAN_ID_MEASUREMENT_FB:
                    measurement = unpack_measurement(frame)
                elif frame.id == CAN_ID_HEARTBEAT:
                    continue
            except CanCodecError:
                continue

        if measurement is None:
            measurement = 0.0
        error = self.setpoint - measurement
        self.integral += error * self.dt_sec
        derivative = (error - self.prev_error) / self.dt_sec
        output = (
            self.gains.kp * error
            + self.gains.ki * self.integral
            + self.gains.kd * derivative
        )
        saturated = False
        if output > self.control_limit:
            output = self.control_limit
            saturated = True
        if output < -self.control_limit:
            output = -self.control_limit
            saturated = True
        self.prev_error = error
        self.last_output = output
        handle.send(pack_control_output(output))
        handle.send(pack_status(CAN_STATE_RUNNING, 0, 1, timestamp_ms))
        handle.send(pack_heartbeat(CAN_NODE_ID_CONTROLLER, self.alive_counter))
        self.alive_counter = (self.alive_counter + 1) % 256
        return ControllerStepResult(control_output=output, saturated=saturated)

