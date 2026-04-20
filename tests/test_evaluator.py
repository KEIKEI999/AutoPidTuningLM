from __future__ import annotations

import unittest

from orchestrator.config import load_config_bundle
from orchestrator.evaluator import evaluate_waveform_file
from tests.test_support import FIXTURES_DIR, REPO_ROOT


class EvaluatorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = load_config_bundle(REPO_ROOT / "configs" / "target_response.yaml", case_name="first_order_nominal")

    def test_good_waveform_passes(self) -> None:
        result = evaluate_waveform_file(
            FIXTURES_DIR / "good_response.csv",
            self.bundle.target,
            self.bundle.evaluation,
            self.bundle.weights,
            self.bundle.runtime_limits.control_output_limit,
        )
        self.assertTrue(result["pass_fail"]["overall"])

    def test_divergence_waveform_flags_divergence(self) -> None:
        result = evaluate_waveform_file(
            FIXTURES_DIR / "divergence_response.csv",
            self.bundle.target,
            self.bundle.evaluation,
            self.bundle.weights,
            self.bundle.runtime_limits.control_output_limit,
        )
        self.assertTrue(result["metrics"]["divergence"])

    def test_oscillation_waveform_flags_oscillation(self) -> None:
        result = evaluate_waveform_file(
            FIXTURES_DIR / "oscillation_response.csv",
            self.bundle.target,
            self.bundle.evaluation,
            self.bundle.weights,
            self.bundle.runtime_limits.control_output_limit,
        )
        self.assertTrue(result["metrics"]["oscillation"])

    def test_saturation_waveform_flags_saturation(self) -> None:
        result = evaluate_waveform_file(
            FIXTURES_DIR / "saturation_response.csv",
            self.bundle.target,
            self.bundle.evaluation,
            self.bundle.weights,
            self.bundle.runtime_limits.control_output_limit,
        )
        self.assertTrue(result["metrics"]["saturation"])

