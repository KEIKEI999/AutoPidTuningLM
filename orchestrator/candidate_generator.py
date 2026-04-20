from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

from orchestrator.can_codec import try_extract_json
from orchestrator.llm_client import LlmClientError, create_llm_client
from orchestrator.models import CandidateProposal, CandidateRationale, ConfigBundle, PIDGains, TrialRecord
from orchestrator.prompt_builder import PromptBuilder


class CandidateError(ValueError):
    """Raised when a candidate cannot be generated safely."""


def _candidate_key(gains: PIDGains) -> tuple[float, float, float]:
    rounded = gains.rounded(6)
    return (rounded.kp, rounded.ki, rounded.kd)


def _coerce_candidate_object(value: object) -> dict[str, float]:
    if isinstance(value, dict):
        return value  # type: ignore[return-value]
    if not isinstance(value, str):
        raise CandidateError("next_candidate must be an object")
    patterns = {
        "Kp": r"Kp\s*[:=]\s*(-?\d+(?:\.\d+)?)",
        "Ki": r"Ki\s*[:=]\s*(-?\d+(?:\.\d+)?)",
        "Kd": r"Kd\s*[:=]\s*(-?\d+(?:\.\d+)?)",
    }
    parsed: dict[str, float] = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, value)
        if match is None:
            raise CandidateError("next_candidate must be an object")
        parsed[key] = float(match.group(1))
    return parsed


def _parameter_action(new_value: float, reference_value: float) -> str:
    tolerance = max(abs(reference_value) * 0.05, 1e-9)
    if new_value > reference_value + tolerance:
        return "increase"
    if new_value < reference_value - tolerance:
        return "decrease"
    return "keep"


def _synthesize_rationale(
    gains: PIDGains,
    reference: PIDGains,
    expectation: str,
    explanation: str,
) -> CandidateRationale:
    actions = {
        "Kp": _parameter_action(gains.kp, reference.kp),
        "Ki": _parameter_action(gains.ki, reference.ki),
        "Kd": _parameter_action(gains.kd, reference.kd),
    }
    if actions["Kp"] == "increase" or actions["Ki"] == "increase":
        expected_tradeoff = "Faster response, but overshoot or saturation risk may increase."
        risk = "overshoot_or_saturation"
    elif actions["Kd"] == "increase":
        expected_tradeoff = "Oscillation may reduce, but noise sensitivity can increase."
        risk = "noise_sensitivity"
    elif actions["Kp"] == "decrease" or actions["Ki"] == "decrease":
        expected_tradeoff = "Safer and smoother response, but rise time may slow down."
        risk = "slow_response"
    else:
        expected_tradeoff = explanation[:120] if explanation else "Conservative local update around the current best candidate."
        risk = "limited_improvement"
    observed_issue = expectation or "local_refinement"
    return CandidateRationale(
        observed_issue=observed_issue,
        parameter_actions=actions,
        expected_tradeoff=expected_tradeoff[:160],
        risk=risk,
    )


def _normalize_rationale(
    value: object,
    gains: PIDGains,
    reference: PIDGains,
    expectation: str,
    explanation: str,
) -> CandidateRationale:
    synthesized = _synthesize_rationale(gains, reference, expectation, explanation)
    if not isinstance(value, dict):
        return synthesized
    observed_issue = value.get("observed_issue")
    expected_tradeoff = value.get("expected_tradeoff")
    risk = value.get("risk")
    parameter_actions = value.get("parameter_actions")
    if not isinstance(observed_issue, str) or not observed_issue.strip():
        observed_issue = synthesized.observed_issue
    if not isinstance(expected_tradeoff, str) or not expected_tradeoff.strip():
        expected_tradeoff = synthesized.expected_tradeoff
    if not isinstance(risk, str) or not risk.strip():
        risk = synthesized.risk
    normalized_actions = dict(synthesized.parameter_actions)
    if isinstance(parameter_actions, dict):
        for key in ("Kp", "Ki", "Kd"):
            action = parameter_actions.get(key)
            if action in {"increase", "decrease", "keep"}:
                normalized_actions[key] = action
    return CandidateRationale(
        observed_issue=observed_issue[:100],
        parameter_actions=normalized_actions,
        expected_tradeoff=expected_tradeoff[:160],
        risk=risk[:80],
    )


def build_initial_candidates(base: PIDGains, bundle: ConfigBundle) -> list[PIDGains]:
    aggressive_anchor = bundle.limits.clamp(
        PIDGains(
            max(base.kp * 16.0, min(4.0, bundle.limits.kp_max)),
            max(max(base.ki, 0.05) * 80.0, min(4.0, bundle.limits.ki_max)),
            0.0,
        )
    )
    restrained_anchor = bundle.limits.clamp(
        PIDGains(
            max(base.kp * 2.0, min(0.5, bundle.limits.kp_max)),
            max(max(base.ki, 0.05) * 2.0, min(0.1, bundle.limits.ki_max)),
            0.0,
        )
    )
    balanced_anchor = bundle.limits.clamp(
        PIDGains(
            max(base.kp * 8.0, min(2.0, bundle.limits.kp_max)),
            max(max(base.ki, 0.05) * 10.0, min(0.5, bundle.limits.ki_max)),
            0.0,
        )
    )

    # Bootstrap on purpose: first measure the configured initial PID as-is,
    # then swing toward a stronger PI setting, then a weaker one, and finally
    # move to the middle so the evaluator can observe under/over tendencies.
    candidates = [base, aggressive_anchor, restrained_anchor, balanced_anchor]
    factors = [2.0, 1.5, 1.2, 0.8, 0.5]
    for factor in factors:
        candidates.append(PIDGains(base.kp * factor, base.ki, base.kd))
        candidates.append(PIDGains(base.kp, base.ki * factor, base.kd))
        kd_base = base.kd if base.kd > 0 else max(bundle.limits.kd_min, 0.01)
        candidates.append(PIDGains(base.kp, base.ki, kd_base * factor))
    candidates.extend(
        [
            PIDGains(base.kp * 1.2, base.ki * 1.2, base.kd),
            PIDGains(base.kp * 1.5, base.ki * 1.5, base.kd),
            PIDGains(base.kp * 1.2, base.ki, max(base.kd, 0.01) * 1.2),
            PIDGains(base.kp * 1.5, base.ki, max(base.kd, 0.01) * 1.5),
            PIDGains(base.kp * 0.8, base.ki * 0.8, max(base.kd, 0.01) * 0.8),
            PIDGains(base.kp * 1.2, base.ki * 1.2, max(base.kd, 0.01) * 1.2),
        ]
    )
    unique: list[PIDGains] = []
    seen: set[tuple[float, float, float]] = set()
    for candidate in candidates:
        clamped = bundle.limits.clamp(candidate)
        key = _candidate_key(clamped)
        if key not in seen:
            unique.append(clamped)
            seen.add(key)
    return unique


@dataclass
class RuleBasedLlmClient:
    def generate(self, best: PIDGains, history: list[TrialRecord], bundle: ConfigBundle) -> str:
        if not history:
            rationale = CandidateRationale(
                observed_issue="baseline",
                parameter_actions={"Kp": "keep", "Ki": "keep", "Kd": "keep"},
                expected_tradeoff="Establish a baseline response before making directional changes.",
                risk="limited_improvement",
            )
            payload = {
                "mode": "coarse",
                "next_candidate": best.as_dict(),
                "expectation": "baseline",
                "explanation": "Use the current baseline gains as the first candidate.",
                "rationale": rationale.as_dict(),
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)

        latest = history[-1]
        dominant_issue = latest.metrics_detail["summary"]["dominant_issue"]
        candidate = best
        explanation = "Adjust gains conservatively around the current best candidate."
        if dominant_issue in {"rise_too_slow", "settling_too_long"}:
            candidate = PIDGains(best.kp * 1.4, best.ki * 1.2, best.kd)
            explanation = "Speed up the response by increasing Kp and Ki."
        elif dominant_issue in {"overshoot_and_oscillation", "oscillation"}:
            candidate = PIDGains(best.kp * 0.8, best.ki * 0.8, max(best.kd, 0.01) * 1.4)
            explanation = "Reduce overshoot and oscillation by lowering P and I and increasing D."
        elif dominant_issue == "steady_state_error":
            candidate = PIDGains(best.kp, best.ki * 1.4, best.kd)
            explanation = "Improve steady-state accuracy by increasing Ki."
        elif dominant_issue == "saturation":
            candidate = PIDGains(best.kp * 0.7, best.ki * 0.7, max(best.kd, 0.01) * 1.1)
            explanation = "Reduce actuator saturation by lowering overall loop gain."
        elif dominant_issue == "divergence":
            candidate = PIDGains(best.kp * 0.5, best.ki * 0.5, max(best.kd, 0.01) * 1.5)
            explanation = "Move toward a safer region after divergence."
        candidate = bundle.limits.clamp(candidate)
        rationale = _synthesize_rationale(candidate, best, dominant_issue, explanation)
        payload = {
            "mode": "fine",
            "next_candidate": candidate.as_dict(),
            "expectation": dominant_issue,
            "explanation": explanation[:100],
            "rationale": rationale.as_dict(),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)


class CandidateGenerator:
    def __init__(self, bundle: ConfigBundle) -> None:
        self.bundle = bundle
        self.prompt_builder = PromptBuilder()
        self.rule_based_client = RuleBasedLlmClient()
        self.external_client = create_llm_client(bundle)
        self.initial_candidates = build_initial_candidates(bundle.initial_pid, bundle)
        self.seen_candidates: set[tuple[float, float, float]] = set()
        self.generator_name = self._resolve_generator_name()
        self.bootstrap_trials = min(1, len(self.initial_candidates))

    def _resolve_generator_name(self) -> str:
        provider = self.bundle.llm.provider
        if provider == "local_ovms":
            return "local_ovms"
        if provider == "openai_responses":
            return "openai_responses"
        return "rule_based_llm_stub"

    def _validate_payload(
        self,
        payload: dict[str, object],
        reference_candidate: PIDGains,
    ) -> tuple[str, PIDGains, str, str, CandidateRationale]:
        required_keys = {"mode", "next_candidate", "expectation", "explanation"}
        missing = required_keys - set(payload)
        if missing:
            raise CandidateError(f"LLM response missing keys: {sorted(missing)}")
        mode = payload["mode"]
        if mode not in {"coarse", "fine"}:
            raise CandidateError("mode must be coarse or fine")
        next_candidate = _coerce_candidate_object(payload["next_candidate"])
        gains = PIDGains.from_dict(next_candidate)
        if not all(math.isfinite(value) for value in (gains.kp, gains.ki, gains.kd)):
            raise CandidateError("PID candidate must be finite")
        if not self.bundle.limits.contains(gains):
            raise CandidateError("PID candidate is outside limits")
        key = _candidate_key(gains)
        if key in self.seen_candidates:
            raise CandidateError("Duplicate PID candidate")
        expectation = str(payload["expectation"])
        explanation = str(payload["explanation"])[:100]
        rationale = _normalize_rationale(payload.get("rationale"), gains, reference_candidate, expectation, explanation)
        return str(mode), gains, expectation, explanation, rationale

    def _fallback_candidate(self) -> PIDGains:
        for candidate in self.initial_candidates:
            if _candidate_key(candidate) not in self.seen_candidates:
                return candidate
        base = self.bundle.initial_pid
        for delta in [0.1, -0.1, 0.05, -0.05]:
            candidate = self.bundle.limits.clamp(
                PIDGains(base.kp * (1 + delta), base.ki * (1 + delta), max(base.kd, 0.01) * (1 + delta))
            )
            if _candidate_key(candidate) not in self.seen_candidates:
                return candidate
        raise CandidateError("No more unique PID candidates available.")

    def _build_initial_response(self, trial_index: int) -> str:
        initial_candidate = self.initial_candidates[trial_index - 1]
        rationale = _synthesize_rationale(
            initial_candidate,
            self.bundle.initial_pid,
            "initial_pattern",
            "Use the deterministic initial candidate pattern.",
        )
        return json.dumps(
            {
                "mode": "coarse" if trial_index <= 3 else "fine",
                "next_candidate": initial_candidate.as_dict(),
                "expectation": "initial_pattern",
                "explanation": "Use the deterministic initial candidate pattern.",
                "rationale": rationale.as_dict(),
            },
            ensure_ascii=False,
            indent=2,
        )

    def _generate_external_response(
        self,
        system_prompt: str,
        user_prompt: str,
        current_best: PIDGains,
        history: list[TrialRecord],
    ) -> tuple[str, str, dict[str, object]]:
        if self.external_client is None:
            return (
                self.rule_based_client.generate(current_best, history, self.bundle),
                self.generator_name,
                {
                    "provider": self.bundle.llm.provider,
                    "configured_use_conversation_state": self.bundle.llm.use_conversation_state,
                    "conversation_mode": "rule_based_stub",
                },
            )
        raw_response = self.external_client.generate(system_prompt, user_prompt, current_best, history, self.bundle)
        return raw_response, self.generator_name, self.external_client.last_metadata()

    def propose(self, history: list[TrialRecord], trial_index: int) -> CandidateProposal:
        current_best = min(history, key=lambda item: item.metrics["score"]).candidate if history else self.bundle.initial_pid
        best_score = min((item.metrics["score"] for item in history), default=float("inf"))
        system_prompt = self.prompt_builder.build_system_prompt(self.bundle)
        user_prompt = self.prompt_builder.build_candidate_prompt(
            self.bundle,
            history,
            current_best,
            best_score,
            trial_index,
        )
        prompt_text = self.prompt_builder.build_prompt_log_text(system_prompt, user_prompt)
        logged_response_text = ""
        llm_context = {
            "provider": self.bundle.llm.provider,
            "configured_use_conversation_state": self.bundle.llm.use_conversation_state,
            "conversation_mode": "bootstrap",
        }

        if trial_index <= self.bootstrap_trials:
            raw_response = self._build_initial_response(trial_index)
            generator_name = self.generator_name if self.bundle.llm.provider != "rule_based_stub" else "rule_based_llm_stub"
            logged_response_text = raw_response
        else:
            try:
                raw_response, generator_name, llm_context = self._generate_external_response(
                    system_prompt,
                    user_prompt,
                    current_best,
                    history,
                )
                logged_response_text = raw_response
            except (CandidateError, LlmClientError, ValueError):
                error_text = "LLM backend request failed."
                raw_response = self.rule_based_client.generate(current_best, history, self.bundle)
                generator_name = f"{self.generator_name}_fallback"
                logged_response_text = json.dumps(
                    {
                        "llm_context": llm_context,
                        "backend": self.bundle.llm.provider,
                        "error": error_text,
                        "fallback_response": try_extract_json(raw_response),
                    },
                    ensure_ascii=False,
                    indent=2,
                )

        try:
            payload = try_extract_json(raw_response)
            mode, gains, expectation, explanation, rationale = self._validate_payload(payload, current_best)
        except Exception as exc:
            gains = self._fallback_candidate()
            mode = "fine"
            expectation = "fallback_neighbor"
            explanation = "Fallback candidate selected after invalid or duplicate LLM output."
            rationale = _synthesize_rationale(gains, current_best, expectation, explanation)
            fallback_payload = {
                "mode": mode,
                "next_candidate": gains.as_dict(),
                "expectation": expectation,
                "explanation": explanation,
                "rationale": rationale.as_dict(),
            }
            raw_response = json.dumps(
                fallback_payload,
                ensure_ascii=False,
                indent=2,
            )
            logged_response_text = json.dumps(
                {
                    "llm_context": llm_context,
                    "backend": self.bundle.llm.provider,
                    "validation_error": str(exc),
                    "raw_response": logged_response_text or raw_response,
                    "fallback_response": fallback_payload,
                },
                ensure_ascii=False,
                indent=2,
            )
            generator_name = f"{generator_name}_fallback"

        if trial_index > self.bootstrap_trials and not logged_response_text.startswith("{\n  \"llm_context\""):
            try:
                parsed_response = try_extract_json(raw_response)
            except Exception:
                parsed_response = raw_response
            logged_response_text = json.dumps(
                {
                    "llm_context": llm_context,
                    "raw_response": parsed_response,
                },
                ensure_ascii=False,
                indent=2,
            )

        self.seen_candidates.add(_candidate_key(gains))
        return CandidateProposal(
            gains=gains,
            mode=mode,
            generator=generator_name,
            expectation=expectation,
            explanation=explanation,
            rationale=rationale,
            llm_context=llm_context,
            prompt_text=prompt_text,
            response_text=logged_response_text or raw_response,
        )
