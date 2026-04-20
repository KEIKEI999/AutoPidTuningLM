from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

from orchestrator.evaluator import load_waveform_csv
from orchestrator.models import TrialRecord


def generate_waveform_overlay_plot(
    history: list[TrialRecord],
    output_path: Path,
    *,
    selected_trial_index: int | None = None,
) -> None:
    valid_trials = [record for record in history if record.artifacts.waveform_csv.exists()]
    if not valid_trials:
        raise ValueError("No waveform CSV files found for plotting.")

    figure, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    measurement_ax, control_ax = axes

    setpoint_plotted = False
    for record in valid_trials:
        rows = load_waveform_csv(record.artifacts.waveform_csv)
        times = [row["time_sec"] for row in rows]
        measurements = [row["measurement"] for row in rows]
        controls = [row["control_output"] for row in rows]
        score = float(record.metrics.get("score", 0.0))
        is_selected = selected_trial_index is not None and record.trial_index == selected_trial_index
        oscillation = bool(record.metrics.get("oscillation", False))
        divergence = bool(record.metrics.get("divergence", False))
        saturation = bool(record.metrics.get("saturation", False))
        label = (
            f"trial {record.trial_index} "
            f"score={score:.3f} "
            f"osc={oscillation} "
            f"div={divergence} "
            f"sat={saturation}"
        )
        if is_selected:
            label = f"{label} [selected]"
        measurement_ax.plot(
            times,
            measurements,
            linewidth=3.0 if is_selected else 1.4,
            alpha=0.95 if is_selected else 0.8,
            zorder=3 if is_selected else 2,
            label=label,
        )
        control_ax.plot(
            times,
            controls,
            linewidth=2.4 if is_selected else 1.1,
            alpha=0.95 if is_selected else 0.8,
            zorder=3 if is_selected else 2,
            label=label,
        )
        if not setpoint_plotted:
            setpoints = [row["setpoint"] for row in rows]
            measurement_ax.plot(times, setpoints, "k--", linewidth=1.5, label="setpoint")
            setpoint_plotted = True

    measurement_ax.set_title("PID Trial Overlay (osc/div/sat shown in legend)")
    measurement_ax.set_ylabel("Measurement")
    measurement_ax.grid(True, linestyle="--", alpha=0.3)
    measurement_ax.legend(loc="best", fontsize=8)

    control_ax.set_title("Control Output Overlay (osc/div/sat shown in legend)")
    control_ax.set_xlabel("Time [s]")
    control_ax.set_ylabel("Control Output")
    control_ax.grid(True, linestyle="--", alpha=0.3)
    control_ax.legend(loc="best", fontsize=8)

    figure.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=150)
    plt.close(figure)
