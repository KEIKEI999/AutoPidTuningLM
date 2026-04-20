from __future__ import annotations

import json
import subprocess
import sys
import unittest
import shutil
from pathlib import Path
from unittest.mock import patch

from orchestrator.config import load_config_bundle
from orchestrator.tuner import Tuner
from orchestrator.runtime import TrialRuntimeError
from tests.test_support import REPO_ROOT, make_config_dir, workspace_temp_dir


class OrchestratorIntegrationTest(unittest.TestCase):
    CASE_NAME = "fixture_case"

    def _make_test_config(self, tmp: Path) -> Path:
        config_dir = make_config_dir(
            tmp,
            target="config_valid_target.yaml",
            cases="config_valid_cases.yaml",
            limits="config_valid_limits.yaml",
        )
        controller_include = tmp / "controller" / "include"
        controller_include.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            REPO_ROOT / "controller" / "include" / "pid_params.h",
            controller_include / "pid_params.h",
        )
        return config_dir / "target_response.yaml"

    def test_ranking_prioritizes_constraint_compliance_before_raw_score(self) -> None:
        with workspace_temp_dir() as tmp:
            bundle = load_config_bundle(self._make_test_config(tmp), case_name=self.CASE_NAME, max_trials=1)
        tuner = Tuner(bundle, REPO_ROOT / ".tmp_tests" / "ranking_sort_test")
        items = [
            {
                "trial_index": 4,
                "score": 4.8,
                "overall_pass": False,
                "failure_count": 4,
                "failed_constraints": ["rise_time", "settling_time", "steady_state_error", "saturation"],
            },
            {
                "trial_index": 3,
                "score": 6.5,
                "overall_pass": False,
                "failure_count": 3,
                "failed_constraints": ["rise_time", "settling_time", "steady_state_error"],
            },
        ]
        items.sort(key=tuner._ranking_sort_key)
        self.assertEqual(items[0]["trial_index"], 3)

    def test_normal_state_transition_reaches_finished(self) -> None:
        with workspace_temp_dir() as tmp:
            bundle = load_config_bundle(self._make_test_config(tmp), case_name=self.CASE_NAME, max_trials=3)
            tuner = Tuner(bundle, tmp)
            result = tuner.run()
            self.assertEqual(result["status"], "FINISHED")
            self.assertTrue((tmp / "waveform_overlay.png").exists())
            trial = json.loads((tmp / "trial_0001.json").read_text(encoding="utf-8"))
            self.assertIn("INIT", trial["states"])
            self.assertIn("EVALUATE", trial["states"])
            self.assertEqual(trial["states"][-1], "SAVE_RESULT")
            self.assertIn("rationale", trial["candidate_source"])
            self.assertIn("llm_context", trial["candidate_source"])
            self.assertEqual(set(trial["candidate_source"]["rationale"]["parameter_actions"].keys()), {"Kp", "Ki", "Kd"})

    def test_build_failure_transitions_to_save_result(self) -> None:
        with workspace_temp_dir() as tmp:
            bundle = load_config_bundle(self._make_test_config(tmp), case_name=self.CASE_NAME, max_trials=1)
            tuner = Tuner(bundle, tmp)
            tuner.build_runner.should_fail = True
            tuner.run()
            trial = json.loads((tmp / "trial_0001.json").read_text(encoding="utf-8"))
            self.assertEqual(trial["status"], "build_failed")
            self.assertIn("BUILD", trial["states"])
            self.assertEqual(trial["states"][-1], "SAVE_RESULT")

    def test_runtime_failure_logs_are_saved(self) -> None:
        with workspace_temp_dir() as tmp:
            bundle = load_config_bundle(self._make_test_config(tmp), case_name=self.CASE_NAME, max_trials=1)
            tuner = Tuner(bundle, tmp)
            with patch(
                "orchestrator.tuner.run_closed_loop_trial",
                side_effect=TrialRuntimeError(
                    "vector runtime failed",
                    logs={
                        "runtime_backend": "c_controller_vector_xl",
                        "failure_stage": "controller_protocol",
                        "controller_stdout_log": str(tmp / "trial_0001" / "controller_stdout.log"),
                    },
                ),
            ):
                tuner.run()
            trial = json.loads((tmp / "trial_0001.json").read_text(encoding="utf-8"))
            self.assertEqual(trial["runtime"]["runtime_backend"], "c_controller_vector_xl")
            self.assertEqual(trial["runtime"]["failure_stage"], "controller_protocol")
            self.assertEqual(trial["runtime"]["controller_stdout_log"], r"trial_0001\controller_stdout.log")
            self.assertEqual(trial["runtime"]["runtime_error_message"], "vector runtime failed")

    def test_cli_dry_run(self) -> None:
        with workspace_temp_dir() as tmp:
            config_path = self._make_test_config(tmp)
            proc = subprocess.run(
                [
                    sys.executable,
                    "orchestrator/main.py",
                    "--config",
                    str(config_path),
                    "--dry-run",
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=REPO_ROOT,
            )
            self.assertEqual(proc.returncode, 0)
            self.assertIn(self.CASE_NAME, proc.stdout)

    def test_cli_emits_progress_lines_during_run(self) -> None:
        with workspace_temp_dir() as tmp:
            config_path = self._make_test_config(tmp)
            proc = subprocess.run(
                [
                    sys.executable,
                    "orchestrator/main.py",
                    "--config",
                    str(config_path),
                    "--case",
                    self.CASE_NAME,
                    "--max-trials",
                    "2",
                    "--output-dir",
                    tmp,
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=REPO_ROOT,
            )
            self.assertEqual(proc.returncode, 0)
            self.assertIn("[progress][run][START]", proc.stdout)
            self.assertIn("[progress][trial 1/2][CANDIDATE]", proc.stdout)
            self.assertIn("[progress][trial 1/2][BUILD]", proc.stdout)
            self.assertIn("[progress][trial 1/2][SAVE_RESULT]", proc.stdout)
            self.assertIn("[progress][run][FINISHED]", proc.stdout)

    def test_short_search_and_same_seed_are_reproducible(self) -> None:
        with workspace_temp_dir() as tmp1, workspace_temp_dir() as tmp2:
            config_path1 = self._make_test_config(tmp1)
            config_path2 = self._make_test_config(tmp2)
            proc1 = subprocess.run(
                [
                    sys.executable,
                    "orchestrator/main.py",
                    "--config",
                    str(config_path1),
                    "--case",
                    self.CASE_NAME,
                    "--max-trials",
                    "4",
                    "--output-dir",
                    tmp1,
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=REPO_ROOT,
            )
            proc2 = subprocess.run(
                [
                    sys.executable,
                    "orchestrator/main.py",
                    "--config",
                    str(config_path2),
                    "--case",
                    self.CASE_NAME,
                    "--max-trials",
                    "4",
                    "--output-dir",
                    tmp2,
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=REPO_ROOT,
            )
            self.assertEqual(proc1.returncode, 0)
            self.assertEqual(proc2.returncode, 0)

            ranking1 = json.loads((tmp1 / "ranking.json").read_text(encoding="utf-8"))
            ranking2 = json.loads((tmp2 / "ranking.json").read_text(encoding="utf-8"))
            self.assertEqual(ranking1["best_trial_index"], ranking2["best_trial_index"])
            self.assertEqual(ranking1["ranking"], ranking2["ranking"])
            self.assertTrue((tmp1 / "waveform_overlay.png").exists())
            self.assertTrue((tmp2 / "waveform_overlay.png").exists())
            self.assertIn("candidate", ranking1["ranking"][0])
            self.assertEqual(set(ranking1["ranking"][0]["candidate"].keys()), {"Kp", "Ki", "Kd"})
            self.assertIn("oscillation", ranking1["ranking"][0])
            self.assertIn("divergence", ranking1["ranking"][0])
            self.assertIn("saturation", ranking1["ranking"][0])
            self.assertIn("reason_summary", ranking1["ranking"][0])
            self.assertIn("expected_tradeoff", ranking1["ranking"][0])
            self.assertIn("overall_pass", ranking1["ranking"][0])
            self.assertIn("failure_count", ranking1["ranking"][0])
            self.assertIn("failed_constraints", ranking1["ranking"][0])
            self.assertIn("dominant_issue", ranking1["ranking"][0])
            self.assertIn("acceptable_candidate_found", ranking1)
            self.assertIn("best_acceptable_trial_index", ranking1)
            self.assertIn("best_acceptable_score", ranking1)

            summary1 = json.loads((tmp1 / "trial_0004.json").read_text(encoding="utf-8"))
            self.assertEqual(ranking1["best_trial_index"], ranking1["ranking"][0]["trial_index"])
            self.assertEqual(summary1["ranking"]["acceptable_candidate_found"], ranking1["acceptable_candidate_found"])
            self.assertIn("llm_context", summary1["candidate_source"])
