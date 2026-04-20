from __future__ import annotations

import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from orchestrator.build_runner import create_build_runner
from orchestrator.candidate_generator import CandidateGenerator
from orchestrator.config import ConfigBundle
from orchestrator.evaluator import evaluate_waveform_file
from orchestrator.models import TrialArtifacts, TrialRecord
from orchestrator.pid_params import update_pid_params_file
from orchestrator.plotter import generate_waveform_overlay_plot
from orchestrator.runtime import HeartbeatTimeoutError, TrialRuntimeError, run_closed_loop_trial


class Tuner:
    def __init__(self, bundle: ConfigBundle, output_dir: Path, progress_stream: TextIO | None = None) -> None:
        self.bundle = bundle
        self.output_dir = output_dir
        self.generator = CandidateGenerator(bundle)
        self.build_runner = create_build_runner(bundle.build)
        self.pid_params_path = bundle.root_dir / "controller" / "include" / "pid_params.h"
        self.history: list[TrialRecord] = []
        self.consecutive_failures = 0
        self.ranking: list[dict[str, Any]] = []
        self.progress_stream = sys.stdout if progress_stream is None else progress_stream

    def _failed_constraints(self, metrics_json: dict[str, Any]) -> list[str]:
        pass_fail = metrics_json.get("pass_fail", {})
        if not isinstance(pass_fail, dict):
            return []
        return [
            name
            for name, passed in pass_fail.items()
            if name != "overall" and passed is False
        ]

    def _ranking_sort_key(self, item: dict[str, Any]) -> tuple[Any, ...]:
        failed = item.get("failed_constraints", [])
        if not isinstance(failed, list):
            failed = []
        return (
            0 if item.get("overall_pass") else 1,
            int(item.get("failure_count", len(failed))),
            1 if "saturation" in failed else 0,
            1 if "oscillation" in failed else 0,
            float(item["score"]),
            int(item["trial_index"]),
        )

    def _best_acceptable(self) -> dict[str, Any] | None:
        for item in self.ranking:
            if item.get("overall_pass"):
                return item
        return None

    def prepare_output_dirs(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _trial_artifacts(self, trial_index: int) -> TrialArtifacts:
        trial_dir = self.output_dir / f"trial_{trial_index:04d}"
        trial_dir.mkdir(parents=True, exist_ok=True)
        logs_dir = trial_dir / "logs"
        logs_dir.mkdir(exist_ok=True)
        return TrialArtifacts(
            trial_dir=trial_dir,
            summary_json=self.output_dir / f"trial_{trial_index:04d}.json",
            metrics_json=trial_dir / "metrics.json",
            waveform_csv=trial_dir / "waveform.csv",
            pid_params_file=trial_dir / "pid_params.h",
            pid_params_diff=trial_dir / "pid_params.diff",
            build_stdout=logs_dir / "build_stdout.log",
            build_stderr=logs_dir / "build_stderr.log",
            llm_prompt=logs_dir / "llm_prompt.txt",
            llm_response=logs_dir / "llm_response.json",
        )

    def _trial_seed(self, trial_index: int, case_seed: int) -> int:
        return self.bundle.runtime_limits.seed + case_seed + trial_index

    def _relative(self, path: Path) -> str:
        return str(path.relative_to(self.output_dir))

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _emit_progress(
        self,
        scope: str,
        state: str,
        trial_index: int | None = None,
        **fields: object,
    ) -> None:
        parts = ["[progress]"]
        if trial_index is not None:
            parts.append(f"[trial {trial_index}/{self.bundle.trial.max_trials}]")
        else:
            parts.append(f"[{scope}]")
        parts.append(f"[{state}]")
        normalized_fields: list[str] = []
        for key, value in fields.items():
            if value is None:
                continue
            if isinstance(value, float):
                normalized_fields.append(f"{key}={value:.6f}")
            else:
                normalized_fields.append(f"{key}={value}")
        line = "".join(parts)
        if normalized_fields:
            line = f"{line} {' '.join(normalized_fields)}"
        print(line, file=self.progress_stream, flush=True)

    def _merge_runtime_logs(self, runtime_info: dict[str, Any], logs: dict[str, object]) -> None:
        for key, value in logs.items():
            if isinstance(value, str):
                try:
                    path_value = Path(value)
                    if path_value.is_absolute() and (path_value == self.output_dir or self.output_dir in path_value.parents):
                        runtime_info[key] = self._relative(path_value)
                    else:
                        runtime_info[key] = value
                except Exception:
                    runtime_info[key] = value
            else:
                runtime_info[key] = value

    def run(self) -> dict[str, Any]:
        self.prepare_output_dirs()
        aborted = False
        self._emit_progress(
            "run",
            "START",
            total_trials=self.bundle.trial.max_trials,
            output_dir=self.output_dir,
            cases=",".join(case.name for case in self.bundle.plant_cases),
        )

        for trial_index in range(1, self.bundle.trial.max_trials + 1):
            case = self.bundle.plant_cases[(trial_index - 1) % len(self.bundle.plant_cases)]
            trial_seed = self._trial_seed(trial_index, case.runtime.seed)
            states = ["INIT"] if trial_index == 1 else []
            artifacts = self._trial_artifacts(trial_index)
            self._emit_progress(
                "trial",
                "PREPARE_TRIAL",
                trial_index=trial_index,
                case=case.name,
                seed=trial_seed,
            )
            proposal = self.generator.propose(self.history, trial_index)
            artifacts.llm_prompt.write_text(proposal.prompt_text, encoding="utf-8")
            artifacts.llm_response.write_text(proposal.response_text, encoding="utf-8")
            self._emit_progress(
                "trial",
                "CANDIDATE",
                trial_index=trial_index,
                case=case.name,
                source=proposal.generator,
                mode=proposal.mode,
                Kp=proposal.gains.kp,
                Ki=proposal.gains.ki,
                Kd=proposal.gains.kd,
            )

            states.extend(["PREPARE_TRIAL", "UPDATE_PARAMS"])
            original_pid_text = self.pid_params_path.read_text(encoding="utf-8")
            update_result = update_pid_params_file(self.pid_params_path, proposal.gains)
            artifacts.pid_params_file.write_text(update_result.updated_text, encoding="utf-8")
            artifacts.pid_params_diff.write_text(update_result.diff_text, encoding="utf-8")
            self._emit_progress("trial", "UPDATE_PARAMS", trial_index=trial_index, status="success")

            states.append("BUILD")
            build_result = self.build_runner.build(self.pid_params_path)
            artifacts.build_stdout.write_text(build_result.stdout_text, encoding="utf-8")
            artifacts.build_stderr.write_text(build_result.stderr_text, encoding="utf-8")
            self._emit_progress(
                "trial",
                "BUILD",
                trial_index=trial_index,
                status=build_result.status,
                exit_code=build_result.exit_code,
                duration_ms=build_result.duration_ms,
            )

            failure_type = None
            status = "completed"
            runtime_info = {
                "plant_launch": "success",
                "controller_launch": "success",
                "trial_duration_ms": 0,
                "timeout": False,
                "controller_exit_code": 0,
                "plant_exit_code": 0,
            }

            try:
                if build_result.status != "success":
                    status = "build_failed"
                    failure_type = "build_failure"
                    raise TrialRuntimeError("Build failed")

                states.extend(["LAUNCH_PLANT", "LAUNCH_CONTROLLER", "RUN_TRIAL", "COLLECT_LOGS"])
                self._emit_progress(
                    "trial",
                    "RUN_TRIAL",
                    trial_index=trial_index,
                    case=case.name,
                    build_mode=self.bundle.build.mode,
                )
                session = run_closed_loop_trial(
                    artifacts.trial_dir,
                    proposal.gains,
                    case,
                    self.bundle.target,
                    self.bundle.runtime_limits,
                    trial_seed,
                    build_config=self.bundle.build,
                )
                runtime_info.update(
                    {
                        "trial_duration_ms": session.duration_ms,
                        "timeout": session.timeout,
                        "controller_exit_code": session.controller_exit_code,
                        "plant_exit_code": session.plant_exit_code,
                    }
                )
                self._merge_runtime_logs(runtime_info, session.logs)

                states.append("EVALUATE")
                metrics_json = evaluate_waveform_file(
                    session.waveform_path,
                    self.bundle.target,
                    self.bundle.evaluation,
                    self.bundle.weights,
                    self.bundle.runtime_limits.control_output_limit,
                    trial_index=trial_index,
                )
                self._write_json(artifacts.metrics_json, metrics_json)
                metrics_summary = {
                    "rise_time": metrics_json["metrics"]["rise_time"],
                    "settling_time": metrics_json["metrics"]["settling_time"],
                    "overshoot": metrics_json["metrics"]["overshoot"],
                    "steady_state_error": metrics_json["metrics"]["steady_state_error"],
                    "iae": metrics_json["metrics"]["iae"],
                    "ise": metrics_json["metrics"]["ise"],
                    "itae": metrics_json["metrics"]["itae"],
                    "control_variation": metrics_json["metrics"]["control_variation"],
                    "oscillation": metrics_json["metrics"]["oscillation"],
                    "divergence": metrics_json["metrics"]["divergence"],
                    "saturation": metrics_json["metrics"]["saturation"],
                    "score": metrics_json["cost_breakdown"]["total_score"],
                }
                if metrics_json["metrics"]["divergence"]:
                    status = "diverged"
                    failure_type = "divergence"
                self._emit_progress(
                    "trial",
                    "EVALUATE",
                    trial_index=trial_index,
                    score=metrics_json["cost_breakdown"]["total_score"],
                    overall_pass=metrics_json.get("pass_fail", {}).get("overall", False),
                    dominant_issue=metrics_json["summary"]["dominant_issue"],
                )

                states.append("DECIDE_NEXT")
                decision = {
                    "dominant_issue": metrics_json["summary"]["dominant_issue"],
                    "next_action": [proposal.expectation],
                    "next_mode": proposal.mode,
                }
            except HeartbeatTimeoutError as exc:
                status = "timeout"
                failure_type = "heartbeat_timeout"
                runtime_info["timeout"] = True
                runtime_info["runtime_error_message"] = str(exc)
                self._merge_runtime_logs(runtime_info, exc.logs)
                self._emit_progress(
                    "trial",
                    "TIMEOUT",
                    trial_index=trial_index,
                    reason="heartbeat_timeout",
                )
                metrics_json = self._failure_metrics(trial_index, artifacts.waveform_csv)
                metrics_summary = deepcopy(metrics_json["metrics"])
                metrics_summary["score"] = metrics_json["cost_breakdown"]["total_score"]
                decision = {
                    "dominant_issue": "heartbeat_timeout",
                    "next_action": ["change_direction"],
                    "next_mode": "fine",
                }
                self._write_json(artifacts.metrics_json, metrics_json)
            except TrialRuntimeError as exc:
                runtime_info["runtime_error_message"] = str(exc)
                self._merge_runtime_logs(runtime_info, exc.logs)
                progress_status = status if status != "completed" else "runtime_failed"
                self._emit_progress(
                    "trial",
                    "ERROR",
                    trial_index=trial_index,
                    status=progress_status,
                    reason=failure_type or "runtime_failure",
                )
                if not artifacts.waveform_csv.exists():
                    artifacts.waveform_csv.write_text(
                        "time_sec,setpoint,measurement,control_output,error,saturated\n",
                        encoding="utf-8",
                    )
                metrics_json = self._failure_metrics(trial_index, artifacts.waveform_csv)
                metrics_summary = deepcopy(metrics_json["metrics"])
                metrics_summary["score"] = metrics_json["cost_breakdown"]["total_score"]
                decision = {
                    "dominant_issue": failure_type or status,
                    "next_action": ["fallback"],
                    "next_mode": "coarse",
                }
                self._write_json(artifacts.metrics_json, metrics_json)
            finally:
                self.pid_params_path.write_text(original_pid_text, encoding="utf-8")

            states.append("SAVE_RESULT")
            score = float(metrics_summary["score"])
            if status == "completed":
                self.consecutive_failures = 0
            else:
                self.consecutive_failures += 1

            failed_constraints = self._failed_constraints(metrics_json)
            overall_pass = bool(metrics_json.get("pass_fail", {}).get("overall", False))

            self.ranking.append(
                {
                    "trial_index": trial_index,
                    "score": score,
                    "status": status,
                    "case": case.name,
                    "candidate": proposal.gains.as_dict(),
                    "reason_summary": proposal.explanation,
                    "expected_tradeoff": proposal.rationale.expected_tradeoff,
                    "oscillation": bool(metrics_summary.get("oscillation", False)),
                    "divergence": bool(metrics_summary.get("divergence", False)),
                    "saturation": bool(metrics_summary.get("saturation", False)),
                    "overall_pass": overall_pass,
                    "failure_count": len(failed_constraints),
                    "failed_constraints": failed_constraints,
                    "dominant_issue": metrics_json.get("summary", {}).get("dominant_issue"),
                }
            )
            self.ranking.sort(key=self._ranking_sort_key)
            best = self.ranking[0]
            best_acceptable = self._best_acceptable()
            self._emit_progress(
                "trial",
                "SAVE_RESULT",
                trial_index=trial_index,
                status=status,
                score=score,
                overall_pass=overall_pass,
                best_trial=best["trial_index"],
                best_score=best["score"],
                acceptable_found=best_acceptable is not None,
                best_acceptable_trial=None if best_acceptable is None else best_acceptable["trial_index"],
            )

            summary_payload = {
                "trial_index": trial_index,
                "timestamp_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "status": status,
                "failure_type": failure_type,
                "candidate": proposal.gains.as_dict(),
                "candidate_source": {
                    "mode": proposal.mode,
                    "generator": proposal.generator,
                    "reason_summary": proposal.explanation,
                    "rationale": proposal.rationale.as_dict(),
                    "llm_context": proposal.llm_context,
                },
                "constraints": self.bundle.limits.as_dict(),
                "plant_config": {
                    "case_name": case.name,
                    "plant": case.plant.type,
                    **case.plant.params,
                    "deadtime": case.deadtime_sec,
                    "noise": f"{case.noise.type}:{case.noise.stddev}",
                    "nonlinear": f"{case.nonlinear.type}:{case.nonlinear.gain}",
                    "seed": trial_seed,
                },
                "build": {
                    "status": build_result.status,
                    "command": build_result.command_text,
                    "exit_code": build_result.exit_code,
                    "duration_ms": build_result.duration_ms,
                    "stdout_log": self._relative(artifacts.build_stdout),
                    "stderr_log": self._relative(artifacts.build_stderr),
                },
                "runtime": runtime_info,
                "artifacts": {
                    "pid_params_file": self._relative(artifacts.pid_params_file),
                    "pid_params_diff": self._relative(artifacts.pid_params_diff),
                    "waveform_csv": self._relative(artifacts.waveform_csv),
                    "metrics_json": self._relative(artifacts.metrics_json),
                    "llm_prompt": self._relative(artifacts.llm_prompt),
                    "llm_response": self._relative(artifacts.llm_response),
                },
                "metrics": metrics_summary,
                "decision": decision,
                "ranking": {
                    "best_score_so_far": best["score"],
                    "best_trial_index": best["trial_index"],
                    "is_current_best": best["trial_index"] == trial_index,
                    "acceptable_candidate_found": best_acceptable is not None,
                    "best_acceptable_trial_index": None if best_acceptable is None else best_acceptable["trial_index"],
                    "best_acceptable_score": None if best_acceptable is None else best_acceptable["score"],
                },
                "states": states,
            }
            self._write_json(artifacts.summary_json, summary_payload)

            self.history.append(
                TrialRecord(
                    trial_index=trial_index,
                    candidate=proposal.gains,
                    candidate_source=proposal,
                    trial_seed=trial_seed,
                    status=status,
                    failure_type=failure_type,
                    build_status=build_result.status,
                    build_exit_code=build_result.exit_code,
                    plant_case=case,
                    artifacts=artifacts,
                    metrics=metrics_summary,
                    metrics_detail=metrics_json,
                    decision=decision,
                    states=states,
                )
            )

            if self.consecutive_failures >= self.bundle.trial.abort_on_consecutive_failures:
                aborted = True
                self._emit_progress(
                    "run",
                    "ABORTED",
                    consecutive_failures=self.consecutive_failures,
                    threshold=self.bundle.trial.abort_on_consecutive_failures,
                )
                break

        ranking_path = self.output_dir / "ranking.json"
        overlay_plot_path = self.output_dir / "waveform_overlay.png"
        plot_error: str | None = None
        try:
            generate_waveform_overlay_plot(
                self.history,
                overlay_plot_path,
                selected_trial_index=self.ranking[0]["trial_index"],
            )
        except Exception as exc:
            plot_error = str(exc)
        self._write_json(
            ranking_path,
            {
                "best_trial_index": self.ranking[0]["trial_index"],
                "best_score": self.ranking[0]["score"],
                "acceptable_candidate_found": self._best_acceptable() is not None,
                "best_acceptable_trial_index": None if self._best_acceptable() is None else self._best_acceptable()["trial_index"],
                "best_acceptable_score": None if self._best_acceptable() is None else self._best_acceptable()["score"],
                "ranking": self.ranking,
                "waveform_overlay_png": str(overlay_plot_path) if overlay_plot_path.exists() else None,
                "plot_error": plot_error,
            },
        )
        result = {
            "status": "ABORTED" if aborted else "FINISHED",
            "best_trial_index": self.ranking[0]["trial_index"],
            "best_score": self.ranking[0]["score"],
            "acceptable_candidate_found": self._best_acceptable() is not None,
            "best_acceptable_trial_index": None if self._best_acceptable() is None else self._best_acceptable()["trial_index"],
            "best_acceptable_score": None if self._best_acceptable() is None else self._best_acceptable()["score"],
            "ranking_path": str(ranking_path),
            "trials": len(self.history),
        }
        if overlay_plot_path.exists():
            result["waveform_overlay_png"] = str(overlay_plot_path)
        if plot_error is not None:
            result["plot_error"] = plot_error
        self._emit_progress(
            "run",
            result["status"],
            trials=result["trials"],
            best_trial=result["best_trial_index"],
            best_score=result["best_score"],
            acceptable_found=result["acceptable_candidate_found"],
            best_acceptable_trial=result["best_acceptable_trial_index"],
        )
        return result

    def _failure_metrics(self, trial_index: int, waveform_csv: Path) -> dict[str, Any]:
        return {
            "trial_index": trial_index,
            "waveform_ref": str(waveform_csv),
            "sampling": {
                "dt_sec": self.bundle.evaluation.sampling_dt_sec,
                "duration_sec": self.bundle.evaluation.duration_sec,
                "num_samples": 0,
            },
            "target": {
                "setpoint": self.bundle.target.setpoint,
                "rise_time_max": self.bundle.target.rise_time_max,
                "settling_time_max": self.bundle.target.settling_time_max,
                "overshoot_max": self.bundle.target.overshoot_max,
                "steady_state_error_max": self.bundle.target.steady_state_error_max,
                "allow_oscillation": self.bundle.target.allow_oscillation,
                "allow_divergence": self.bundle.target.allow_divergence,
                "allow_saturation": self.bundle.target.allow_saturation,
            },
            "window": {
                "evaluation_start_sec": self.bundle.evaluation.evaluation_start_sec,
                "evaluation_end_sec": self.bundle.evaluation.evaluation_end_sec,
                "steady_state_window_sec": self.bundle.evaluation.steady_state_window_sec,
                "settling_band_ratio": self.bundle.evaluation.settling_band_ratio,
            },
            "metrics": {
                "rise_time": self.bundle.evaluation.duration_sec,
                "peak_time": self.bundle.evaluation.duration_sec,
                "settling_time": self.bundle.evaluation.duration_sec,
                "overshoot": 100.0,
                "steady_state_error": abs(self.bundle.target.setpoint),
                "iae": self.bundle.evaluation.duration_sec,
                "ise": self.bundle.evaluation.duration_sec,
                "itae": self.bundle.evaluation.duration_sec,
                "max_abs_error": abs(self.bundle.target.setpoint),
                "control_variation": 0.0,
                "control_energy": 0.0,
                "oscillation": False,
                "divergence": True,
                "saturation": False,
            },
            "cost_breakdown": {
                "rise_time_cost": self.bundle.weights.rise_time,
                "settling_time_cost": self.bundle.weights.settling_time,
                "overshoot_cost": self.bundle.weights.overshoot,
                "steady_state_error_cost": self.bundle.weights.steady_state_error,
                "iae_cost": self.bundle.weights.iae,
                "control_variation_cost": self.bundle.weights.control_variation,
                "oscillation_penalty": 0.0,
                "divergence_penalty": self.bundle.weights.divergence_penalty,
                "total_score": 10.0,
            },
            "weights": self.bundle.weights.as_dict(),
            "pass_fail": {
                "rise_time": False,
                "settling_time": False,
                "overshoot": False,
                "steady_state_error": False,
                "oscillation": True,
                "divergence": False,
                "saturation": True,
                "overall": False,
            },
            "summary": {
                "dominant_issue": "trial_failure",
                "evaluation_result": "failed",
                "comment": "runtime or build failure",
            },
        }
