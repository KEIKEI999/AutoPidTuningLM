from __future__ import annotations

import unittest
from unittest.mock import patch
from pathlib import Path

from orchestrator.can_codec import pack_control_output, pack_heartbeat, pack_status
from orchestrator.can_if import CanIfStatus
from orchestrator.can_map import CAN_NODE_ID_CONTROLLER, CAN_STATE_RUNNING
from orchestrator.config import load_config_bundle
from orchestrator.models import BuildConfig, PIDGains
from orchestrator.runtime import (
    HeartbeatTimeoutError,
    RuntimeSessionResult,
    TrialRuntimeError,
    _receive_controller_outputs,
    run_closed_loop_trial,
    run_external_controller_trial,
)
from tests.test_support import REPO_ROOT, workspace_temp_dir


class _FakeLibrary:
    def __init__(self, sdk_dir: Path | None = None) -> None:
        self.sdk_dir = sdk_dir or Path(r"C:\VectorSDK")


class _FakeHandle:
    def __init__(self, *, open_status: CanIfStatus = CanIfStatus.OK, last_error: int = 0) -> None:
        self.open_status = open_status
        self.last_error = last_error
        self.deinited = False

    def open(self) -> CanIfStatus:
        return self.open_status

    def deinit(self) -> CanIfStatus:
        self.deinited = True
        return CanIfStatus.OK

    def get_last_error(self) -> int:
        return self.last_error


class _FakeProcess:
    def __init__(self) -> None:
        self._returncode: int | None = None
        self.killed = False

    def poll(self) -> int | None:
        return self._returncode

    def wait(self, timeout: int | None = None) -> int:
        del timeout
        if self._returncode is None:
            self._returncode = 0
        return self._returncode

    def kill(self) -> None:
        self.killed = True
        self._returncode = -9


class _ReceiveSequenceHandle:
    def __init__(self, frames: list[object]) -> None:
        self.frames = list(frames)

    def receive(self, timeout_ms: int | None = None) -> tuple[CanIfStatus, object | None]:
        del timeout_ms
        if not self.frames:
            return CanIfStatus.TIMEOUT, None
        frame = self.frames.pop(0)
        if frame is None:
            return CanIfStatus.TIMEOUT, None
        return CanIfStatus.OK, frame


class RuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = load_config_bundle(REPO_ROOT / "configs" / "target_response.yaml", case_name="first_order_nominal")
        cls.build_config = BuildConfig(
            mode="msbuild",
            command=["controller.sln", "/p:Configuration=Release", "/p:CanAdapter=vector_xl"],
            working_dir=REPO_ROOT / "controller" / "vs2017",
        )

    def test_heartbeat_timeout_is_detected(self) -> None:
        with workspace_temp_dir() as tmp:
            with self.assertRaises(HeartbeatTimeoutError):
                run_closed_loop_trial(
                    tmp,
                    PIDGains(0.25, 0.05, 0.0),
                    self.bundle.plant_cases[0],
                    self.bundle.target,
                    self.bundle.runtime_limits,
                    seed=123,
                    inject_heartbeat_timeout=True,
                )

    def test_msbuild_vector_xl_selects_external_runtime(self) -> None:
        sentinel = RuntimeSessionResult(
            waveform_path=REPO_ROOT / "tests" / "fixtures" / "good_response.csv",
            duration_ms=123,
            controller_exit_code=0,
            plant_exit_code=0,
            timeout=False,
            logs={"runtime_backend": "c_controller_vector_xl"},
        )
        with workspace_temp_dir() as tmp:
            with patch("orchestrator.runtime.run_external_controller_trial", return_value=sentinel) as external_mock:
                with patch("orchestrator.runtime._run_virtual_closed_loop_trial") as virtual_mock:
                    result = run_closed_loop_trial(
                        tmp,
                        PIDGains(0.25, 0.05, 0.0),
                        self.bundle.plant_cases[0],
                        self.bundle.target,
                        self.bundle.runtime_limits,
                        seed=123,
                        build_config=self.build_config,
                    )
        self.assertIs(result, sentinel)
        external_mock.assert_called_once()
        virtual_mock.assert_not_called()

    def test_non_vector_runtime_keeps_virtual_backend(self) -> None:
        build_config = BuildConfig(
            mode="msbuild",
            command=["controller.sln", "/p:Configuration=Release", "/p:CanAdapter=stub"],
            working_dir=REPO_ROOT / "controller" / "vs2017",
        )
        sentinel = RuntimeSessionResult(
            waveform_path=REPO_ROOT / "tests" / "fixtures" / "good_response.csv",
            duration_ms=456,
            controller_exit_code=0,
            plant_exit_code=0,
            timeout=False,
            logs={"runtime_backend": "virtual_stub"},
        )
        with workspace_temp_dir() as tmp:
            with patch("orchestrator.runtime._run_virtual_closed_loop_trial", return_value=sentinel) as virtual_mock:
                with patch("orchestrator.runtime.run_external_controller_trial") as external_mock:
                    result = run_closed_loop_trial(
                        tmp,
                        PIDGains(0.25, 0.05, 0.0),
                        self.bundle.plant_cases[0],
                        self.bundle.target,
                        self.bundle.runtime_limits,
                        seed=123,
                        build_config=build_config,
                    )
        self.assertIs(result, sentinel)
        virtual_mock.assert_called_once()
        external_mock.assert_not_called()

    def test_external_runtime_reports_vector_open_failure(self) -> None:
        fake_handle = _FakeHandle(open_status=CanIfStatus.HW_ERROR, last_error=77)
        with workspace_temp_dir() as tmp:
            controller_exe = tmp / "controller.exe"
            controller_exe.write_text("fake", encoding="utf-8")
            with patch("orchestrator.runtime.VectorXlLibrary", return_value=_FakeLibrary()):
                with patch("orchestrator.runtime.can_if_init_vector_xl", return_value=fake_handle):
                    with self.assertRaises(TrialRuntimeError) as cm:
                        run_external_controller_trial(
                            tmp,
                            PIDGains(0.25, 0.05, 0.0),
                            self.bundle.plant_cases[0],
                            self.bundle.target,
                            self.bundle.runtime_limits,
                            seed=123,
                            build_config=self.build_config,
                            controller_executable=controller_exe,
                        )
        self.assertEqual(cm.exception.logs["failure_stage"], "vector_open")
        self.assertEqual(cm.exception.logs["vector_last_error"], 77)
        self.assertTrue(fake_handle.deinited)

    def test_external_runtime_reports_protocol_error(self) -> None:
        fake_handle = _FakeHandle()
        fake_process = _FakeProcess()
        with workspace_temp_dir() as tmp:
            controller_exe = tmp / "controller.exe"
            controller_exe.write_text("fake", encoding="utf-8")
            with patch("orchestrator.runtime.VectorXlLibrary", return_value=_FakeLibrary()):
                with patch("orchestrator.runtime.can_if_init_vector_xl", return_value=fake_handle):
                    with patch("orchestrator.runtime.subprocess.Popen", return_value=fake_process):
                        with patch(
                            "orchestrator.runtime._exchange_controller_step",
                            return_value=(0.1, {"node_id": 99, "alive_counter": 1}, {"state_code": 1}),
                        ):
                            with self.assertRaises(TrialRuntimeError) as cm:
                                run_external_controller_trial(
                                    tmp,
                                    PIDGains(0.25, 0.05, 0.0),
                                    self.bundle.plant_cases[0],
                                    self.bundle.target,
                                    self.bundle.runtime_limits,
                                    seed=123,
                                    build_config=self.build_config,
                                    controller_executable=controller_exe,
                                )
        self.assertEqual(cm.exception.logs["failure_stage"], "controller_protocol")
        self.assertEqual(cm.exception.logs["unexpected_controller_node_id"], 99)
        self.assertTrue(fake_process.killed)

    def test_receive_controller_outputs_discards_stale_alive_counter_group(self) -> None:
        stale_group = [
            pack_control_output(0.4),
            pack_status(CAN_STATE_RUNNING, 0, 1, 10),
            pack_heartbeat(CAN_NODE_ID_CONTROLLER, 4),
        ]
        fresh_group = [
            pack_control_output(0.7),
            pack_status(CAN_STATE_RUNNING, 0, 1, 20),
            pack_heartbeat(CAN_NODE_ID_CONTROLLER, 5),
        ]
        handle = _ReceiveSequenceHandle(stale_group + fresh_group)

        control_output, heartbeat, status_payload = _receive_controller_outputs(
            handle,
            10,
            expected_alive_counter=5,
        )

        self.assertAlmostEqual(control_output, 0.7, places=6)
        self.assertEqual(heartbeat["alive_counter"], 5)
        self.assertEqual(status_payload["state_code"], CAN_STATE_RUNNING)
