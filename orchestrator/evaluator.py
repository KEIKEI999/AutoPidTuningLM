from __future__ import annotations

import csv
import math
from pathlib import Path
from statistics import mean
from typing import Any

from orchestrator.models import EvaluationWindow, ScoreWeights, TargetSpec


def load_waveform_csv(path: Path) -> list[dict[str, float]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for row in reader:
            rows.append(
                {
                    "time_sec": float(row["time_sec"]),
                    "setpoint": float(row["setpoint"]),
                    "measurement": float(row["measurement"]),
                    "control_output": float(row["control_output"]),
                    "error": float(row["error"]),
                    "saturated": float(row.get("saturated", "0")),
                }
            )
    if not rows:
        raise ValueError("Waveform CSV is empty.")
    return rows


def _normalize(value: float, target: float) -> float:
    if target <= 0:
        return max(value, 0.0)
    return max(value, 0.0) / target


def _count_sign_changes(values: list[float]) -> int:
    changes = 0
    prev_sign = 0
    for value in values:
        sign = 1 if value > 0 else -1 if value < 0 else 0
        if sign != 0 and prev_sign != 0 and sign != prev_sign:
            changes += 1
        if sign != 0:
            prev_sign = sign
    return changes


def evaluate_waveform(
    rows: list[dict[str, float]],
    target: TargetSpec,
    window: EvaluationWindow,
    weights: ScoreWeights,
    control_limit: float,
    *,
    trial_index: int | None = None,
    waveform_ref: str | None = None,
) -> dict[str, Any]:
    times = [row["time_sec"] for row in rows]
    measurements = [row["measurement"] for row in rows]
    controls = [row["control_output"] for row in rows]
    errors = [row["error"] for row in rows]
    dt = times[1] - times[0] if len(times) > 1 else window.sampling_dt_sec
    setpoint = target.setpoint
    band = abs(setpoint) * window.settling_band_ratio
    threshold_90 = 0.9 * setpoint

    rise_time = next((time for time, value in zip(times, measurements) if value >= threshold_90), window.duration_sec)
    peak = max(measurements)
    peak_index = measurements.index(peak)
    peak_time = times[peak_index]
    overshoot = 0.0 if setpoint == 0 else max(0.0, ((peak - setpoint) / abs(setpoint)) * 100.0)

    settling_time = window.duration_sec
    for index, time in enumerate(times):
        if all(abs(value - setpoint) <= band for value in measurements[index:]):
            settling_time = time
            break

    steady_state_samples = [
        row["measurement"]
        for row in rows
        if row["time_sec"] >= max(0.0, window.evaluation_end_sec - window.steady_state_window_sec)
    ]
    steady_state_value = mean(steady_state_samples) if steady_state_samples else measurements[-1]
    steady_state_error = abs(setpoint - steady_state_value)
    iae = sum(abs(error) * dt for error in errors)
    ise = sum((error ** 2) * dt for error in errors)
    itae = sum(time * abs(error) * dt for time, error in zip(times, errors))
    max_abs_error = max(abs(error) for error in errors)
    control_variation = sum(abs(curr - prev) for prev, curr in zip(controls, controls[1:]))
    control_energy = sum((value ** 2) * dt for value in controls)
    saturated_samples = sum(1 for row in rows if row.get("saturated", 0.0) >= 1.0)
    saturation_ratio = saturated_samples / max(len(rows), 1)
    oscillation = _count_sign_changes([value - setpoint for value in measurements[len(measurements) // 3 :]]) >= 4
    divergence = (
        any(not math.isfinite(value) for value in measurements + controls)
        or max(abs(value) for value in measurements) > max(3.0 * abs(setpoint), 3.0)
    )
    # Treat brief clipping during the launch transient differently from sustained
    # actuator saturation. A short burst at t=0 is often acceptable in practice.
    saturation = saturated_samples >= max(3, len(rows) // 20)

    cost_breakdown = {
        "rise_time_cost": weights.rise_time * _normalize(rise_time, target.rise_time_max),
        "settling_time_cost": weights.settling_time * _normalize(settling_time, target.settling_time_max),
        "overshoot_cost": weights.overshoot * _normalize(overshoot, max(target.overshoot_max, 1e-9)),
        "steady_state_error_cost": weights.steady_state_error
        * _normalize(steady_state_error, max(target.steady_state_error_max, 1e-9)),
        "iae_cost": weights.iae * _normalize(iae, max(window.duration_sec * abs(setpoint), 1e-9)),
        "control_variation_cost": weights.control_variation
        * (
            _normalize(control_variation, max(control_limit * max(len(rows) - 1, 1), 1e-9))
            + (1.0 if saturation else 0.0)
        ),
        "oscillation_penalty": weights.oscillation_penalty * (1.0 if oscillation and not target.allow_oscillation else 0.0),
        "divergence_penalty": weights.divergence_penalty * (1.0 if divergence and not target.allow_divergence else 0.0),
    }
    total_score = sum(cost_breakdown.values())
    pass_fail = {
        "rise_time": rise_time <= target.rise_time_max,
        "settling_time": settling_time <= target.settling_time_max,
        "overshoot": overshoot <= target.overshoot_max,
        "steady_state_error": steady_state_error <= target.steady_state_error_max,
        "oscillation": (not oscillation) or target.allow_oscillation,
        "divergence": (not divergence) or target.allow_divergence,
        "saturation": (not saturation) or target.allow_saturation,
    }
    pass_fail["overall"] = all(pass_fail.values())

    dominant_issue = "acceptable"
    if divergence:
        dominant_issue = "divergence"
    elif saturation:
        dominant_issue = "saturation"
    elif oscillation and overshoot > target.overshoot_max:
        dominant_issue = "overshoot_and_oscillation"
    elif rise_time > target.rise_time_max:
        dominant_issue = "rise_too_slow"
    elif settling_time > target.settling_time_max:
        dominant_issue = "settling_too_long"
    elif steady_state_error > target.steady_state_error_max:
        dominant_issue = "steady_state_error"

    metrics = {
        "rise_time": rise_time,
        "peak_time": peak_time,
        "settling_time": settling_time,
        "overshoot": overshoot,
        "steady_state_error": steady_state_error,
        "iae": iae,
        "ise": ise,
        "itae": itae,
        "max_abs_error": max_abs_error,
        "control_variation": control_variation,
        "control_energy": control_energy,
        "saturated_samples": saturated_samples,
        "saturation_ratio": saturation_ratio,
        "oscillation": oscillation,
        "divergence": divergence,
        "saturation": saturation,
    }
    return {
        "trial_index": trial_index,
        "waveform_ref": waveform_ref,
        "sampling": {
            "dt_sec": dt,
            "duration_sec": times[-1],
            "num_samples": len(rows),
        },
        "target": {
            "setpoint": target.setpoint,
            "rise_time_max": target.rise_time_max,
            "settling_time_max": target.settling_time_max,
            "overshoot_max": target.overshoot_max,
            "steady_state_error_max": target.steady_state_error_max,
            "allow_oscillation": target.allow_oscillation,
            "allow_divergence": target.allow_divergence,
            "allow_saturation": target.allow_saturation,
        },
        "window": {
            "evaluation_start_sec": window.evaluation_start_sec,
            "evaluation_end_sec": window.evaluation_end_sec,
            "steady_state_window_sec": window.steady_state_window_sec,
            "settling_band_ratio": window.settling_band_ratio,
        },
        "metrics": metrics,
        "cost_breakdown": {
            **cost_breakdown,
            "total_score": total_score,
        },
        "weights": weights.as_dict(),
        "pass_fail": pass_fail,
        "summary": {
            "dominant_issue": dominant_issue,
            "evaluation_result": "target_met" if pass_fail["overall"] else "needs_improvement",
            "comment": dominant_issue.replace("_", " "),
        },
    }


def evaluate_waveform_file(
    waveform_path: Path,
    target: TargetSpec,
    window: EvaluationWindow,
    weights: ScoreWeights,
    control_limit: float,
    *,
    trial_index: int | None = None,
) -> dict[str, Any]:
    rows = load_waveform_csv(waveform_path)
    return evaluate_waveform(
        rows,
        target,
        window,
        weights,
        control_limit,
        trial_index=trial_index,
        waveform_ref=str(waveform_path),
    )
