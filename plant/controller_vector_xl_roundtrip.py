from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from orchestrator.config import ConfigError, load_config_bundle
from orchestrator.runtime import TrialRuntimeError, run_external_controller_trial


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify C controller.exe and plant simulator roundtrip over Vector XL.")
    parser.add_argument("--target", required=True, help="Path to target_response.yaml")
    parser.add_argument("--case", required=True, help="Plant case name")
    parser.add_argument("--output-dir", required=True, help="Directory for waveform and logs")
    parser.add_argument("--controller-exe", help="Optional override for controller.exe path")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        bundle = load_config_bundle(Path(args.target), case_name=args.case, build_mode="msbuild", can_adapter="vector_xl")
        case = bundle.plant_cases[0]
        output_dir = Path(args.output_dir).resolve()
        result = run_external_controller_trial(
            output_dir,
            bundle.initial_pid,
            case,
            bundle.target,
            bundle.runtime_limits,
            bundle.runtime_limits.seed + case.runtime.seed,
            build_config=bundle.build,
            controller_executable=None if not args.controller_exe else Path(args.controller_exe),
        )
        summary_path = output_dir / "summary.json"
        summary_payload = {
            "status": "success",
            "waveform_csv": str(result.waveform_path),
            "duration_ms": result.duration_ms,
            "controller_exit_code": result.controller_exit_code,
            "plant_exit_code": result.plant_exit_code,
            "logs": result.logs,
        }
        summary_path.write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")
        print(
            f"Controller/plant Vector XL roundtrip succeeded: "
            f"duration_ms={result.duration_ms} output_dir={output_dir}"
        )
        return 0
    except (ConfigError, TrialRuntimeError) as exc:
        parser.print_usage(sys.stderr)
        print(f"Roundtrip error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
