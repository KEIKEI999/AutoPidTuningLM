from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

from orchestrator.models import PIDGains


class PIDParamsError(ValueError):
    """Raised when pid_params.h update rules are violated."""


PID_PATTERNS = {
    "PID_KP": re.compile(r"^(?P<prefix>\s*#define\s+PID_KP\s+\()(?P<value>[^)]+)(?P<suffix>\)\s*)$"),
    "PID_KI": re.compile(r"^(?P<prefix>\s*#define\s+PID_KI\s+\()(?P<value>[^)]+)(?P<suffix>\)\s*)$"),
    "PID_KD": re.compile(r"^(?P<prefix>\s*#define\s+PID_KD\s+\()(?P<value>[^)]+)(?P<suffix>\)\s*)$"),
}


@dataclass
class PIDUpdateResult:
    updated_text: str
    diff_text: str


def _format_gain(value: float) -> str:
    return f"{value:.6f}"


def _replace_line(line: str, macro: str, value: float) -> str:
    match = PID_PATTERNS[macro].match(line)
    if not match:
        raise PIDParamsError(f"Could not match editable line for {macro}")
    return f"{match.group('prefix')}{_format_gain(value)}{match.group('suffix')}"


def render_updated_pid_params(original_text: str, gains: PIDGains) -> PIDUpdateResult:
    lines = original_text.splitlines()
    updated = list(lines)

    begin = next((index for index, line in enumerate(lines) if "AUTO_EDIT_BEGIN" in line), None)
    end = next((index for index, line in enumerate(lines) if "AUTO_EDIT_END" in line), None)
    if (begin is None) != (end is None):
        raise PIDParamsError("AUTO_EDIT markers must both exist or both be absent.")
    if begin is not None and end is not None and begin >= end:
        raise PIDParamsError("AUTO_EDIT marker order is invalid.")

    targets = {"PID_KP": gains.kp, "PID_KI": gains.ki, "PID_KD": gains.kd}
    seen: set[str] = set()
    for index, line in enumerate(lines):
        stripped = line.strip()
        for macro, value in targets.items():
            if stripped.startswith(f"#define {macro}"):
                if begin is not None and not (begin < index < end):
                    raise PIDParamsError(f"{macro} exists outside AUTO_EDIT section.")
                updated[index] = _replace_line(line, macro, value)
                seen.add(macro)
                break

    missing = [macro for macro in targets if macro not in seen]
    if missing:
        raise PIDParamsError(f"Editable macros not found: {', '.join(missing)}")

    diff_lines = list(
        difflib.unified_diff(
            original_text.splitlines(),
            updated,
            fromfile="pid_params_original.h",
            tofile="pid_params_updated.h",
            lineterm="",
        )
    )
    for diff_line in diff_lines:
        if diff_line.startswith(("---", "+++", "@@")):
            continue
        if diff_line.startswith(("+", "-")) and not any(macro in diff_line for macro in targets):
            raise PIDParamsError("Detected a non-PID diff in pid_params.h")

    updated_text = "\n".join(updated) + ("\n" if original_text.endswith("\n") else "")
    diff_text = "\n".join(diff_lines) + ("\n" if diff_lines else "")
    return PIDUpdateResult(updated_text=updated_text, diff_text=diff_text)


def update_pid_params_file(path: Path, gains: PIDGains) -> PIDUpdateResult:
    original_text = path.read_text(encoding="utf-8")
    result = render_updated_pid_params(original_text, gains)
    path.write_text(result.updated_text, encoding="utf-8")
    return result

