from __future__ import annotations

import unittest
from pathlib import Path

from orchestrator.models import (
    BuildConfig,
    CandidateProposal,
    CandidateRationale,
    ConfigBundle,
    EvaluationWindow,
    LlmConfig,
    NoiseConfig,
    NonlinearConfig,
    PIDGains,
    PIDLimits,
    PlantCase,
    PlantModelConfig,
    PlantRuntime,
    RuntimeLimits,
    ScoreWeights,
    TargetSpec,
    TrialArtifacts,
    TrialRecord,
    TrialSettings,
    VectorXlRuntimeConfig,
)
from orchestrator.prompt_builder import PromptBuilder


def _make_bundle(*, language: str, user_instruction: str | None) -> ConfigBundle:
    return ConfigBundle(
        root_dir=Path("."),
        target_path=Path("configs/target_response.yaml"),
        plant_cases_path=Path("configs/plant_cases.yaml"),
        limits_path=Path("configs/limits.yaml"),
        name="fixture",
        user_instruction=user_instruction,
        target=TargetSpec(1.0, 1.0, 2.5, 5.0, 0.01, False, False, False),
        evaluation=EvaluationWindow(5.0, 0.01, 0.0, 5.0, 0.5, 0.02),
        weights=ScoreWeights(0.1, 0.2, 0.25, 0.1, 0.1, 0.1, 0.1, 0.05),
        trial=TrialSettings(10, 3),
        limits=PIDLimits(0.01, 10.0, 0.0, 5.0, 0.0, 2.0),
        initial_pid=PIDGains(0.25, 0.05, 0.0),
        build=BuildConfig("mock", ["msbuild"], Path(".")),
        llm=LlmConfig(
            "local_ovms",
            "OpenVINO/Qwen3-8B-int4-ov",
            "http://127.0.0.1:8000/v3/chat/completions",
            None,
            "pid_candidate_response",
            language,
            False,
        ),
        runtime_limits=RuntimeLimits(4242, 0.25, 2.0, VectorXlRuntimeConfig(0, 500000, 125, 50, 1000, 50)),
        plant_cases=[
            PlantCase(
                "fixture_case",
                True,
                PlantModelConfig("first_order", {"gain": 1.0, "tau": 0.8}),
                0.0,
                NoiseConfig("none", 0.0),
                NonlinearConfig("none", 0.0),
                PlantRuntime(5.0, 0.01, 999),
            )
        ],
    )


def _make_record(trial_index: int) -> TrialRecord:
    gains = PIDGains(0.25 * trial_index, 0.05 * trial_index, 0.01 * max(trial_index - 1, 0))
    artifacts = TrialArtifacts(
        Path("."),
        Path(f"trial_{trial_index:04d}.json"),
        Path("metrics.json"),
        Path("waveform.csv"),
        Path("pid_params.h"),
        Path("pid_params.diff"),
        Path("build_stdout.log"),
        Path("build_stderr.log"),
        Path("llm_prompt.txt"),
        Path("llm_response.json"),
    )
    return TrialRecord(
        trial_index=trial_index,
        candidate=gains,
        candidate_source=CandidateProposal(
            gains=gains,
            mode="fine",
            generator="local_ovms",
            expectation="history",
            explanation="history",
            rationale=CandidateRationale(
                observed_issue="history",
                parameter_actions={"Kp": "increase", "Ki": "increase", "Kd": "keep"},
                expected_tradeoff="history",
                risk="history",
            ),
            llm_context={},
            prompt_text="",
            response_text="{}",
        ),
        trial_seed=trial_index,
        status="completed",
        failure_type=None,
        build_status="success",
        build_exit_code=0,
        plant_case=_make_bundle(language="en", user_instruction=None).plant_cases[0],
        artifacts=artifacts,
        metrics={
            "rise_time": float(trial_index),
            "settling_time": float(trial_index),
            "overshoot": 0.0,
            "steady_state_error": 0.1,
            "oscillation": False,
            "divergence": False,
            "saturation": False,
            "score": float(trial_index),
        },
        metrics_detail={"summary": {"dominant_issue": "history"}},
        decision={},
    )


class PromptBuilderTest(unittest.TestCase):
    def test_system_prompt_contains_fixed_rules(self) -> None:
        bundle = _make_bundle(language="en", user_instruction=None)
        system_prompt = PromptBuilder().build_system_prompt(bundle)
        self.assertIn("fixed rules", system_prompt)
        self.assertIn("Return exactly one JSON object", system_prompt)
        self.assertIn("Never repeat any previously used PID candidate", system_prompt)
        self.assertIn("Operator Intent", system_prompt)
        self.assertIn("prefer Operator Intent", system_prompt)
        self.assertIn("run contract", system_prompt)
        self.assertIn("Do not silently weaken or dilute Operator Intent", system_prompt)

    def test_user_prompt_contains_dynamic_context_and_operator_instruction(self) -> None:
        bundle = _make_bundle(language="en", user_instruction="Prefer conservative updates.")
        record = _make_record(1)
        user_prompt = PromptBuilder().build_candidate_prompt(bundle, [record], record.candidate, 7.0, 2)
        self.assertIn("[Targets]", user_prompt)
        self.assertIn("[Current Best Candidate]", user_prompt)
        self.assertIn("[Used Candidates: Never Repeat]", user_prompt)
        self.assertIn("showing last 1 of 1 trials", user_prompt)
        self.assertIn("[Operator Intent]", user_prompt)
        self.assertIn("[Operator Intent Priority]", user_prompt)
        self.assertIn("Prefer conservative updates.", user_prompt)
        self.assertIn("highest-priority run-specific tuning direction", user_prompt)
        self.assertIn("prefer Operator Intent", user_prompt)
        self.assertIn("run contract", user_prompt)
        self.assertIn("Do not silently soften it into a conservative move.", user_prompt)
        self.assertIn("meaningfully stronger move", user_prompt)
        self.assertIn("- max_trials = 10", user_prompt)
        self.assertIn("- remaining_trials = 9", user_prompt)
        self.assertIn("explanation must briefly state how the candidate follows it", user_prompt)
        self.assertIn("prefer a meaningfully stronger move over a tiny safe adjustment", user_prompt)
        self.assertNotIn("Return exactly one JSON object", user_prompt)

    def test_user_prompt_keeps_last_ten_trials(self) -> None:
        bundle = _make_bundle(language="en", user_instruction=None)
        history = [_make_record(index) for index in range(1, 12)]
        user_prompt = PromptBuilder().build_candidate_prompt(bundle, history, history[-1].candidate, 1.0, 12)
        self.assertIn("showing last 10 of 11 trials", user_prompt)
        self.assertNotIn("Kp=0.2500 Ki=0.0500 Kd=0.0000", user_prompt)
        self.assertIn("Kp=0.5000 Ki=0.1000 Kd=0.0100", user_prompt)
        self.assertIn("Kp=2.7500 Ki=0.5500 Kd=0.1000", user_prompt)

    def test_japanese_prompts_are_supported(self) -> None:
        bundle = _make_bundle(language="ja", user_instruction="定常誤差を減らしつつオーバーシュートは抑える。")
        builder = PromptBuilder()
        system_prompt = builder.build_system_prompt(bundle)
        user_prompt = builder.build_candidate_prompt(bundle, [], bundle.initial_pid, 7.0, 2)
        self.assertIn("system prompt", system_prompt)
        self.assertIn("あなたは PID チューニング候補を返す自動エージェントです。", system_prompt)
        self.assertIn("[目標]", user_prompt)
        self.assertIn("[運用者の追加方針]", user_prompt)
        self.assertIn("定常誤差を減らしつつオーバーシュートは抑える。", user_prompt)
