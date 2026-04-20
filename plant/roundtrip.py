from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from orchestrator.can_codec import (
    CanCodecError,
    pack_control_output,
    pack_heartbeat,
    pack_measurement,
    pack_setpoint,
    unpack_control_output,
    unpack_heartbeat,
    unpack_measurement,
    unpack_setpoint,
)
from orchestrator.can_if import CanFrame, CanIfStatus
from orchestrator.can_map import CAN_ID_CONTROL_OUTPUT, CAN_ID_HEARTBEAT, CAN_ID_MEASUREMENT_FB, CAN_ID_SETPOINT_CMD, CAN_NODE_ID_ORCH, CAN_NODE_ID_PLANT
from orchestrator.models import PlantCase, TargetSpec
from orchestrator.runtime import PlantSystem


class CanHandleLike(Protocol):
    def send(self, frame: CanFrame) -> CanIfStatus:
        ...

    def receive(self, timeout_ms: int | None = None) -> tuple[CanIfStatus, CanFrame | None]:
        ...


class PlantRoundtripError(RuntimeError):
    pass


@dataclass
class PlantRoundtripResult:
    waveform_path: Path
    summary_path: Path
    measurement_count: int
    heartbeat_count: int
    last_measurement: float
    elapsed_ms: int


@dataclass
class _PlantFeedback:
    measurement: float | None
    heartbeat_seen: bool


class PlantNode:
    def __init__(self, case: PlantCase, seed: int) -> None:
        self.case = case
        self.system = PlantSystem(case, seed)
        self.last_setpoint = 0.0
        self.last_control_output = 0.0
        self.alive_counter = 0

    def service(self, handle: CanHandleLike, *, timeout_ms: int, timestamp_ms: int) -> float:
        saw_relevant_frame = False
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        while True:
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            status, frame = handle.receive(remaining_ms)
            if status == CanIfStatus.TIMEOUT or frame is None:
                break
            if status != CanIfStatus.OK:
                raise PlantRoundtripError(f"plant receive failed with status={int(status)}")
            try:
                if frame.id == CAN_ID_SETPOINT_CMD:
                    self.last_setpoint = unpack_setpoint(frame)
                    saw_relevant_frame = True
                elif frame.id == CAN_ID_CONTROL_OUTPUT:
                    self.last_control_output = unpack_control_output(frame)
                    saw_relevant_frame = True
                elif frame.id == CAN_ID_HEARTBEAT:
                    unpack_heartbeat(frame)
                    saw_relevant_frame = True
            except CanCodecError as exc:
                raise PlantRoundtripError(f"plant decode failed for CAN ID 0x{frame.id:03X}: {exc}") from exc
            if time.monotonic() >= deadline:
                break
        if not saw_relevant_frame:
            raise PlantRoundtripError("plant did not receive setpoint/control_output/heartbeat within timeout")
        measurement = self.system.step(self.last_control_output, self.case.runtime.dt_sec)
        if handle.send(pack_measurement(measurement)) != CanIfStatus.OK:
            raise PlantRoundtripError("plant failed to send measurement")
        if handle.send(pack_heartbeat(CAN_NODE_ID_PLANT, self.alive_counter)) != CanIfStatus.OK:
            raise PlantRoundtripError("plant failed to send heartbeat")
        self.alive_counter = (self.alive_counter + 1) % 256
        del timestamp_ms
        return measurement


def _receive_host_feedback(handle: CanHandleLike, *, timeout_ms: int) -> _PlantFeedback:
    measurement = None
    heartbeat_seen = False
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    while time.monotonic() <= deadline:
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        status, frame = handle.receive(remaining_ms)
        if status == CanIfStatus.TIMEOUT or frame is None:
            continue
        if status != CanIfStatus.OK:
            raise PlantRoundtripError(f"host receive failed with status={int(status)}")
        try:
            if frame.id == CAN_ID_MEASUREMENT_FB:
                measurement = unpack_measurement(frame)
            elif frame.id == CAN_ID_HEARTBEAT:
                heartbeat = unpack_heartbeat(frame)
                if heartbeat["node_id"] == CAN_NODE_ID_PLANT:
                    heartbeat_seen = True
        except CanCodecError as exc:
            raise PlantRoundtripError(f"host decode failed for CAN ID 0x{frame.id:03X}: {exc}") from exc
        if measurement is not None and heartbeat_seen:
            break
    return _PlantFeedback(measurement=measurement, heartbeat_seen=heartbeat_seen)


def run_plant_roundtrip(
    output_dir: Path,
    host_handle: CanHandleLike,
    plant_handle: CanHandleLike,
    case: PlantCase,
    target: TargetSpec,
    *,
    seed: int,
    steps: int,
    control_output: float,
    timeout_ms: int = 50,
) -> PlantRoundtripResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    waveform_path = output_dir / "waveform.csv"
    summary_path = output_dir / "summary.json"
    node = PlantNode(case, seed)
    rows: list[dict[str, float | int]] = []
    measurement_count = 0
    heartbeat_count = 0
    started = time.monotonic()

    for step in range(steps):
        timestamp_ms = int(round(step * case.runtime.dt_sec * 1000.0))
        if host_handle.send(pack_setpoint(target.setpoint)) != CanIfStatus.OK:
            raise PlantRoundtripError("host failed to send setpoint")
        if host_handle.send(pack_control_output(control_output)) != CanIfStatus.OK:
            raise PlantRoundtripError("host failed to send control_output")
        if host_handle.send(pack_heartbeat(CAN_NODE_ID_ORCH, step % 256)) != CanIfStatus.OK:
            raise PlantRoundtripError("host failed to send heartbeat")

        expected_measurement = node.service(plant_handle, timeout_ms=timeout_ms, timestamp_ms=timestamp_ms)
        feedback = _receive_host_feedback(host_handle, timeout_ms=timeout_ms)
        if feedback.measurement is None:
            raise PlantRoundtripError("host did not receive measurement from plant")
        if not feedback.heartbeat_seen:
            raise PlantRoundtripError("host did not receive plant heartbeat")
        measurement_count += 1
        heartbeat_count += 1
        rows.append(
            {
                "time_sec": round(step * case.runtime.dt_sec, 6),
                "setpoint": target.setpoint,
                "measurement": feedback.measurement,
                "control_output": control_output,
                "error": target.setpoint - feedback.measurement,
                "saturated": 0,
                "expected_measurement": round(expected_measurement, 9),
            }
        )

    with waveform_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["time_sec", "setpoint", "measurement", "control_output", "error", "saturated", "expected_measurement"],
        )
        writer.writeheader()
        writer.writerows(rows)

    elapsed_ms = int((time.monotonic() - started) * 1000)
    summary = {
        "status": "success",
        "steps": steps,
        "measurement_count": measurement_count,
        "heartbeat_count": heartbeat_count,
        "last_measurement": rows[-1]["measurement"] if rows else 0.0,
        "setpoint": target.setpoint,
        "control_output": control_output,
        "timeout_ms": timeout_ms,
        "waveform_csv": str(waveform_path),
        "elapsed_ms": elapsed_ms,
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return PlantRoundtripResult(
        waveform_path=waveform_path,
        summary_path=summary_path,
        measurement_count=measurement_count,
        heartbeat_count=heartbeat_count,
        last_measurement=float(summary["last_measurement"]),
        elapsed_ms=elapsed_ms,
    )
