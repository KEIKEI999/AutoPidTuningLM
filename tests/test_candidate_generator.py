from __future__ import annotations

import unittest

from orchestrator.candidate_generator import CandidateGenerator
from orchestrator.config import load_config_bundle
from tests.test_support import make_config_dir, workspace_temp_dir


class _FakeClient:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text

    def generate(self, system_prompt, user_prompt, best, history, bundle) -> str:  # noqa: ANN001
        del system_prompt, user_prompt, best, history, bundle
        return self.response_text

    def last_metadata(self) -> dict[str, object]:
        return {
            "provider": "local_ovms",
            "configured_use_conversation_state": True,
            "conversation_mode": "system_side_conversation",
            "session_message_count": 4,
        }


class CandidateGeneratorTest(unittest.TestCase):
    def test_bootstrap_starts_with_initial_pid_then_falls_back_to_next_candidates(self) -> None:
        with workspace_temp_dir() as tmp:
            config_dir = make_config_dir(
                tmp,
                target="config_valid_target.yaml",
                cases="config_valid_cases.yaml",
                limits="config_valid_limits.yaml",
            )
            bundle = load_config_bundle(config_dir / "target_response.yaml")
            generator = CandidateGenerator(bundle)

            first = generator.propose([], trial_index=1)
            second = generator.propose([], trial_index=2)
            third = generator.propose([], trial_index=3)

            self.assertEqual(first.gains.as_dict(), bundle.initial_pid.as_dict())
            self.assertEqual(second.gains.as_dict(), {"Kp": 4.0, "Ki": 4.0, "Kd": 0.0})
            self.assertEqual(third.gains.as_dict(), {"Kp": 0.5, "Ki": 0.1, "Kd": 0.0})

    def test_local_ovms_response_is_used_after_bootstrap(self) -> None:
        with workspace_temp_dir() as tmp:
            config_dir = make_config_dir(
                tmp,
                target="config_valid_target.yaml",
                cases="config_valid_cases.yaml",
                limits="config_valid_limits.yaml",
            )
            bundle = load_config_bundle(config_dir / "target_response.yaml")
            generator = CandidateGenerator(bundle)
            generator.external_client = _FakeClient(
                """
                <think>internal</think>
                {
                  "mode": "fine",
                  "next_candidate": {
                    "Kp": 0.61,
                    "Ki": 0.09,
                    "Kd": 0.02
                  },
                  "expectation": "rise_too_slow",
                  "explanation": "Use a slightly faster candidate.",
                  "rationale": {
                    "observed_issue": "rise_too_slow",
                    "parameter_actions": {"Kp": "increase", "Ki": "increase", "Kd": "keep"},
                    "expected_tradeoff": "Faster response with some overshoot risk.",
                    "risk": "overshoot_or_saturation"
                  }
                }
                """
            )
            proposal = generator.propose([], trial_index=2)
            self.assertEqual(proposal.generator, "local_ovms")
            self.assertEqual(proposal.gains.as_dict(), {"Kp": 0.61, "Ki": 0.09, "Kd": 0.02})
            self.assertEqual(proposal.expectation, "rise_too_slow")
            self.assertEqual(proposal.rationale.parameter_actions["Kp"], "increase")
            self.assertEqual(proposal.rationale.risk, "overshoot_or_saturation")
            self.assertEqual(proposal.llm_context["conversation_mode"], "system_side_conversation")

    def test_string_next_candidate_is_coerced(self) -> None:
        with workspace_temp_dir() as tmp:
            config_dir = make_config_dir(
                tmp,
                target="config_valid_target.yaml",
                cases="config_valid_cases.yaml",
                limits="config_valid_limits.yaml",
            )
            bundle = load_config_bundle(config_dir / "target_response.yaml")
            generator = CandidateGenerator(bundle)
            generator.external_client = _FakeClient(
                """
                {
                  "mode": "fine",
                  "next_candidate": "Kp=0.63 Ki=0.11 Kd=0.03",
                  "expectation": "rise_too_slow",
                  "explanation": "Use a different candidate."
                }
                """
            )
            proposal = generator.propose([], trial_index=2)
            self.assertEqual(proposal.gains.as_dict(), {"Kp": 0.63, "Ki": 0.11, "Kd": 0.03})
            self.assertEqual(proposal.generator, "local_ovms")
            self.assertEqual(proposal.rationale.observed_issue, "rise_too_slow")
            self.assertEqual(set(proposal.rationale.parameter_actions.keys()), {"Kp", "Ki", "Kd"})

    def test_invalid_external_response_falls_back_safely(self) -> None:
        with workspace_temp_dir() as tmp:
            config_dir = make_config_dir(
                tmp,
                target="config_valid_target.yaml",
                cases="config_valid_cases.yaml",
                limits="config_valid_limits.yaml",
            )
            bundle = load_config_bundle(config_dir / "target_response.yaml")
            generator = CandidateGenerator(bundle)
            generator.external_client = _FakeClient("not-json")
            proposal = generator.propose([], trial_index=2)
            self.assertTrue(proposal.generator.endswith("_fallback"))
            self.assertEqual(proposal.expectation, "fallback_neighbor")
            self.assertEqual(proposal.rationale.observed_issue, "fallback_neighbor")
            self.assertIn(proposal.rationale.risk, {"overshoot_or_saturation", "slow_response", "noise_sensitivity", "limited_improvement"})
