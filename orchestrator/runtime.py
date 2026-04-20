from __future__ import annotations

import csv
import os
import random
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from orchestrator.can_codec import (
    CanCodecError,
    CanCodecStatus,
    pack_heartbeat,
    pack_measurement,
    pack_setpoint,
    unpack_control_output,
    unpack_heartbeat,
    unpack_status,
)
from orchestrator.can_if import CanFrame, CanIfConfig, CanIfStatus, VirtualCanBus, can_if_init
from orchestrator.can_map import (
    CAN_ID_CONTROL_OUTPUT,
    CAN_ID_HEARTBEAT,
    CAN_ID_STATUS,
    CAN_NODE_ID_CONTROLLER,
    CAN_NODE_ID_ORCH,
    CAN_NODE_ID_PLANT,
)
from orchestrator.controller_stub import PIDControllerStub
from orchestrator.models import BuildConfig, PIDGains, PlantCase, RuntimeLimits, TargetSpec
from plant.can_io import VectorXlLibrary, can_if_init_vector_xl
from plant.models.deadtime import DeadtimeBuffer
from plant.models.first_order import FirstOrderPlant
from plant.models.nonlinear import NonlinearTransform
from plant.models.noise import NoiseModel
from plant.models.second_order import SecondOrderPlant


class TrialRuntimeError(RuntimeError):
    def __init__(self, message: str, *, logs: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.logs = logs or {}


class HeartbeatTimeoutError(TrialRuntimeError):
    pass


@dataclass
class RuntimeSessionResult:
    waveform_path: Path
    duration_ms: int
    controller_exit_code: int
    plant_exit_code: int
    timeout: bool
    logs: dict[str, object]


class PlantSystem:
    def __init__(self, case: PlantCase, seed: int) -> None:
        if case.plant.type == "first_order":
            self.model = FirstOrderPlant(
                gain=case.plant.params["gain"],
                tau=case.plant.params["tau"],
            )
        elif case.plant.type == "second_order":
            self.model = SecondOrderPlant(
                wn=case.plant.params["wn"],
                zeta=case.plant.params["zeta"],
                gain=case.plant.params.get("gain", 1.0),
            )
        else:
            raise TrialRuntimeError(f"Unsupported plant type: {case.plant.type}")
        self.deadtime = DeadtimeBuffer(case.deadtime_sec, case.runtime.dt_sec)
        self.nonlinear = NonlinearTransform(case.nonlinear.type, case.nonlinear.gain)
        self.noise = NoiseModel(case.noise.type, case.noise.stddev, random.Random(seed))
        self.measurement = 0.0
        self.alive_counter = 0

    def step(self, control_output: float, dt_sec: float) -> float:
        delayed = self.deadtime.push(control_output)
        effective = self.nonlinear.apply(delayed)
        clean = self.model.step(effective, dt_sec)
        self.measurement = self.noise.apply(clean)
        return self.measurement


def _open_handle(bus: VirtualCanBus):
    handle = can_if_init(bus, CanIfConfig())
    status = handle.open()
    if status != CanIfStatus.OK:
        raise TrialRuntimeError("Failed to open virtual CAN handle")
    return handle


def _extract_msbuild_property(command: list[str], property_name: str) -> str | None:
    prefix = "/p:"
    property_name_lower = property_name.lower()
    for item in command:
        if not item.lower().startswith(prefix):
            continue
        body = item[len(prefix) :]
        if "=" not in body:
            continue
        key, value = body.split("=", 1)
        if key.lower() == property_name_lower:
            return value
    return None


def _should_use_external_controller(build_config: BuildConfig | None) -> bool:
    if build_config is None or build_config.mode != "msbuild":
        return False
    return (_extract_msbuild_property(build_config.command, "CanAdapter") or "").lower() == "vector_xl"


def _resolve_controller_executable(build_config: BuildConfig) -> Path:
    configuration = _extract_msbuild_property(build_config.command, "Configuration") or "Release"
    return (build_config.working_dir.parent / "build" / configuration / "controller.exe").resolve()


def _vector_runtime_config(runtime_limits: RuntimeLimits) -> CanIfConfig:
    return CanIfConfig(
        channel_index=runtime_limits.vector_xl.channel_index,
        bitrate=runtime_limits.vector_xl.bitrate,
        rx_timeout_ms=runtime_limits.vector_xl.rx_timeout_ms,
    )


def _base_external_runtime_logs(
    trial_dir: Path,
    controller_executable: Path,
    can_config: CanIfConfig,
    runtime_limits: RuntimeLimits,
) -> dict[str, object]:
    return {
        "runtime_backend": "c_controller_vector_xl",
        "heartbeat_timeout": False,
        "controller_stdout_log": str(trial_dir / "controller_stdout.log"),
        "controller_stderr_log": str(trial_dir / "controller_stderr.log"),
        "controller_executable": str(controller_executable),
        "vector_channel_index": can_config.channel_index,
        "vector_bitrate": can_config.bitrate,
        "vector_rx_timeout_ms": can_config.rx_timeout_ms,
        "vector_startup_wait_ms": runtime_limits.vector_xl.startup_wait_ms,
        "vector_exchange_timeout_ms": runtime_limits.vector_xl.exchange_timeout_ms,
        "vector_resend_interval_ms": runtime_limits.vector_xl.resend_interval_ms,
    }


def _send_required_frames(handle, setpoint: float, measurement: float, alive_counter: int) -> None:
    for frame in (
        pack_setpoint(setpoint),
        pack_measurement(measurement),
        pack_heartbeat(CAN_NODE_ID_ORCH, alive_counter),
        pack_heartbeat(CAN_NODE_ID_PLANT, alive_counter),
    ):
        status = handle.send(frame)
        if status != CanIfStatus.OK:
            raise TrialRuntimeError(f"failed to send frame 0x{frame.id:03X}, status={int(status)}")


def _drain_pending_controller_frames(handle) -> int:
    drained = 0
    while True:
        rx_status, frame = handle.receive(0)
        if rx_status != CanIfStatus.OK or frame is None:
            return drained
        drained += 1


def _receive_controller_outputs(
    handle,
    timeout_ms: int,
    *,
    expected_alive_counter: int,
) -> tuple[float, dict[str, int], dict[str, int]]:
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    pending_control_output = None
    pending_status = None
    while time.monotonic() <= deadline:
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        rx_status, frame = handle.receive(remaining_ms)
        if rx_status == CanIfStatus.TIMEOUT or frame is None:
            continue
        if rx_status != CanIfStatus.OK:
            raise TrialRuntimeError(f"controller receive failed with status={int(rx_status)}")
        try:
            if frame.id == CAN_ID_CONTROL_OUTPUT:
                pending_control_output = unpack_control_output(frame)
            elif frame.id == CAN_ID_HEARTBEAT:
                candidate_heartbeat = unpack_heartbeat(frame)
                if candidate_heartbeat["node_id"] in {CAN_NODE_ID_ORCH, CAN_NODE_ID_PLANT}:
                    continue
                if candidate_heartbeat["node_id"] != CAN_NODE_ID_CONTROLLER:
                    pending_control_output = None
                    pending_status = None
                    continue
                if (
                    candidate_heartbeat["alive_counter"] == expected_alive_counter
                    and pending_control_output is not None
                    and pending_status is not None
                ):
                    return pending_control_output, candidate_heartbeat, pending_status
                pending_control_output = None
                pending_status = None
            elif frame.id == CAN_ID_STATUS:
                pending_status = unpack_status(frame)
        except CanCodecError as exc:
            raise TrialRuntimeError(f"decode failed for CAN ID 0x{frame.id:03X}: {exc}") from exc
    raise HeartbeatTimeoutError("controller did not return control_output/status/heartbeat within timeout")


def _exchange_controller_step(
    handle,
    *,
    setpoint: float,
    measurement: float,
    alive_counter: int,
    timeout_ms: int,
    resend_interval_ms: int = 50,
) -> tuple[float, dict[str, int], dict[str, int]]:
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    resend_interval_sec = max(0.01, resend_interval_ms / 1000.0)
    last_send_at = 0.0
    while time.monotonic() <= deadline:
        now = time.monotonic()
        if (last_send_at == 0.0) or ((now - last_send_at) >= resend_interval_sec):
            if last_send_at == 0.0:
                _drain_pending_controller_frames(handle)
            _send_required_frames(handle, setpoint, measurement, alive_counter)
            last_send_at = now
        remaining_ms = max(1, int((deadline - now) * 1000))
        try:
            return _receive_controller_outputs(
                handle,
                min(remaining_ms, resend_interval_ms),
                expected_alive_counter=alive_counter,
            )
        except HeartbeatTimeoutError:
            continue
    raise HeartbeatTimeoutError("controller step exchange timed out")


def _run_virtual_closed_loop_trial(
    trial_dir: Path,
    candidate: PIDGains,
    case: PlantCase,
    target: TargetSpec,
    runtime_limits: RuntimeLimits,
    seed: int,
    *,
    inject_heartbeat_timeout: bool = False,
) -> RuntimeSessionResult:
    waveform_path = trial_dir / "waveform.csv"
    bus = VirtualCanBus()
    orch_handle = _open_handle(bus)
    controller_handle = _open_handle(bus)
    plant_handle = _open_handle(bus)
    controller = PIDControllerStub(candidate, case.runtime.dt_sec, runtime_limits.control_output_limit)
    plant = PlantSystem(case, seed)

    last_heartbeat = {
        CAN_NODE_ID_ORCH: 0,
        CAN_NODE_ID_PLANT: 0,
    }
    control_output = 0.0
    logs: dict[str, object] = {
        "runtime_backend": "virtual_stub",
        "unknown_ids": [],
        "dlc_errors": 0,
        "heartbeat_timeout": False,
    }
    rows: list[dict[str, float | int]] = []

    total_steps = int(round(case.runtime.duration_sec / case.runtime.dt_sec))
    for step in range(total_steps + 1):
        time_sec = step * case.runtime.dt_sec
        timestamp_ms = int(round(time_sec * 1000))
        orch_handle.send(pack_setpoint(target.setpoint))
        if not inject_heartbeat_timeout:
            orch_handle.send(pack_heartbeat(CAN_NODE_ID_ORCH, step % 256))
            last_heartbeat[CAN_NODE_ID_ORCH] = timestamp_ms

        plant_handle.send(pack_measurement(plant.measurement))
        plant_handle.send(pack_heartbeat(CAN_NODE_ID_PLANT, plant.alive_counter))
        plant.alive_counter = (plant.alive_counter + 1) % 256
        last_heartbeat[CAN_NODE_ID_PLANT] = timestamp_ms

        control_result = controller.step(controller_handle, timestamp_ms)
        for frame in plant_handle.drain():
            try:
                if frame.id == CAN_ID_CONTROL_OUTPUT:
                    control_output = unpack_control_output(frame)
                elif frame.id == CAN_ID_HEARTBEAT:
                    heartbeat = unpack_heartbeat(frame)
                    last_heartbeat[heartbeat["node_id"]] = timestamp_ms
                else:
                    logs["unknown_ids"].append(frame.id)
            except CanCodecError as exc:
                if exc.status == CanCodecStatus.INVALID_DLC:
                    logs["dlc_errors"] = int(logs["dlc_errors"]) + 1

        if (
            timestamp_ms - last_heartbeat.get(CAN_NODE_ID_ORCH, 0)
            > int(runtime_limits.heartbeat_timeout_sec * 1000)
        ):
            logs["heartbeat_timeout"] = True
            raise HeartbeatTimeoutError("orchestrator heartbeat timeout")

        measurement = plant.step(control_output, case.runtime.dt_sec)
        rows.append(
            {
                "time_sec": round(time_sec, 6),
                "setpoint": target.setpoint,
                "measurement": measurement,
                "control_output": control_output,
                "error": target.setpoint - measurement,
                "saturated": int(control_result.saturated),
            }
        )

    with waveform_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["time_sec", "setpoint", "measurement", "control_output", "error", "saturated"],
        )
        writer.writeheader()
        writer.writerows(rows)

    for handle in (orch_handle, controller_handle, plant_handle):
        handle.close()
        handle.deinit()

    return RuntimeSessionResult(
        waveform_path=waveform_path,
        duration_ms=int(case.runtime.duration_sec * 1000),
        controller_exit_code=0,
        plant_exit_code=0,
        timeout=False,
        logs=logs,
    )


def run_external_controller_trial(
    trial_dir: Path,
    candidate: PIDGains,
    case: PlantCase,
    target: TargetSpec,
    runtime_limits: RuntimeLimits,
    seed: int,
    *,
    build_config: BuildConfig,
    controller_executable: Path | None = None,
    startup_wait_ms: int | None = None,
) -> RuntimeSessionResult:
    del candidate
    trial_dir.mkdir(parents=True, exist_ok=True)
    waveform_path = trial_dir / "waveform.csv"
    stdout_path = trial_dir / "controller_stdout.log"
    stderr_path = trial_dir / "controller_stderr.log"
    can_config = _vector_runtime_config(runtime_limits)
    controller_executable = (controller_executable or _resolve_controller_executable(build_config)).resolve()
    if not controller_executable.exists():
        raise TrialRuntimeError(f"controller executable not found: {controller_executable}")
    startup_wait_ms = runtime_limits.vector_xl.startup_wait_ms if startup_wait_ms is None else startup_wait_ms
    runtime_logs = _base_external_runtime_logs(trial_dir, controller_executable, can_config, runtime_limits)

    controller_handle = None
    process = None
    try:
        library = VectorXlLibrary()
        controller_handle = can_if_init_vector_xl(
            can_config,
            app_name="AutoTuningLMOrchestrator",
            library=library,
        )
        if controller_handle.open() != CanIfStatus.OK:
            runtime_logs["failure_stage"] = "vector_open"
            runtime_logs["vector_last_error"] = controller_handle.get_last_error()
            raise TrialRuntimeError(
                f"Vector XL open failed: last_error={controller_handle.get_last_error()}",
                logs=runtime_logs,
            )

        env = os.environ.copy()
        env["VECTOR_XL_SDK_DIR"] = str(library.sdk_dir)
        env["PATH"] = str((library.sdk_dir / "bin").resolve()) + os.pathsep + env.get("PATH", "")
        env["ATLM_CHANNEL_INDEX"] = str(can_config.channel_index)
        env["ATLM_BITRATE"] = str(can_config.bitrate)
        env["ATLM_RX_TIMEOUT_MS"] = str(can_config.rx_timeout_ms)
        env["ATLM_STEPS"] = str(int(round(case.runtime.duration_sec / case.runtime.dt_sec)) + 1)
        env["ATLM_DT_SEC"] = str(case.runtime.dt_sec)
        env["ATLM_CONTROL_LIMIT"] = str(runtime_limits.control_output_limit)
        runtime_logs["vector_sdk_dir"] = str(library.sdk_dir)

        with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open("w", encoding="utf-8") as stderr_handle:
            try:
                process = subprocess.Popen(
                    [str(controller_executable)],
                    cwd=str(controller_executable.parent),
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    env=env,
                )
            except OSError as exc:
                runtime_logs["failure_stage"] = "controller_launch"
                raise TrialRuntimeError(f"controller launch failed: {exc}", logs=runtime_logs) from exc
            time.sleep(max(0.0, startup_wait_ms / 1000.0))

            plant = PlantSystem(case, seed)
            rows: list[dict[str, float | int]] = []
            total_steps = int(round(case.runtime.duration_sec / case.runtime.dt_sec))
            last_status = None
            started = time.monotonic()

            for step in range(total_steps + 1):
                try:
                    control_output, heartbeat, status_payload = _exchange_controller_step(
                        controller_handle,
                        setpoint=target.setpoint,
                        measurement=plant.measurement,
                        alive_counter=step % 256,
                        timeout_ms=runtime_limits.vector_xl.exchange_timeout_ms,
                        resend_interval_ms=runtime_limits.vector_xl.resend_interval_ms,
                    )
                except HeartbeatTimeoutError as exc:
                    runtime_logs["failure_stage"] = "controller_exchange"
                    runtime_logs["heartbeat_timeout"] = True
                    raise HeartbeatTimeoutError(str(exc), logs=runtime_logs) from exc
                if heartbeat["node_id"] != 1:
                    runtime_logs["failure_stage"] = "controller_protocol"
                    runtime_logs["unexpected_controller_node_id"] = heartbeat["node_id"]
                    raise TrialRuntimeError(
                        f"unexpected controller heartbeat node_id={heartbeat['node_id']}",
                        logs=runtime_logs,
                    )
                measurement = plant.step(control_output, case.runtime.dt_sec)
                last_status = status_payload
                rows.append(
                    {
                        "time_sec": round(step * case.runtime.dt_sec, 6),
                        "setpoint": target.setpoint,
                        "measurement": measurement,
                        "control_output": control_output,
                        "error": target.setpoint - measurement,
                        "saturated": int(abs(control_output) >= runtime_limits.control_output_limit),
                    }
                )

            controller_exit_code = process.wait(timeout=10)
            if controller_exit_code != 0:
                runtime_logs["failure_stage"] = "controller_exit"
                runtime_logs["controller_exit_code"] = controller_exit_code
                raise TrialRuntimeError(
                    f"controller exited with code {controller_exit_code}",
                    logs=runtime_logs,
                )

        with waveform_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["time_sec", "setpoint", "measurement", "control_output", "error", "saturated"],
            )
            writer.writeheader()
            writer.writerows(rows)

        duration_ms = int((time.monotonic() - started) * 1000)
        return RuntimeSessionResult(
            waveform_path=waveform_path,
            duration_ms=duration_ms,
            controller_exit_code=controller_exit_code,
            plant_exit_code=0,
            timeout=False,
            logs={**runtime_logs, "last_controller_state": None if last_status is None else last_status["state_code"]},
        )
    except subprocess.TimeoutExpired as exc:
        runtime_logs["failure_stage"] = "controller_wait"
        raise TrialRuntimeError(f"controller wait timed out: {exc}", logs=runtime_logs) from exc
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if controller_handle is not None:
            controller_handle.deinit()


def run_closed_loop_trial(
    trial_dir: Path,
    candidate: PIDGains,
    case: PlantCase,
    target: TargetSpec,
    runtime_limits: RuntimeLimits,
    seed: int,
    *,
    build_config: BuildConfig | None = None,
    inject_heartbeat_timeout: bool = False,
) -> RuntimeSessionResult:
    if not inject_heartbeat_timeout and _should_use_external_controller(build_config):
        return run_external_controller_trial(
            trial_dir,
            candidate,
            case,
            target,
            runtime_limits,
            seed,
            build_config=build_config if build_config is not None else BuildConfig(mode="mock", command=[], working_dir=trial_dir),
        )
    return _run_virtual_closed_loop_trial(
        trial_dir,
        candidate,
        case,
        target,
        runtime_limits,
        seed,
        inject_heartbeat_timeout=inject_heartbeat_timeout,
    )
