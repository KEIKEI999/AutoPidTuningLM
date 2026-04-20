from __future__ import annotations

import argparse
import sys
from pathlib import Path

from orchestrator.config import ConfigError, load_config_bundle
from orchestrator.models import PIDGains
from orchestrator.runtime import run_closed_loop_trial


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a single virtual plant trial.")
    parser.add_argument("--target", required=True, help="Path to target_response.yaml")
    parser.add_argument("--case", required=True, help="Plant case name")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    parser.add_argument("--kp", type=float, required=True)
    parser.add_argument("--ki", type=float, required=True)
    parser.add_argument("--kd", type=float, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        bundle = load_config_bundle(Path(args.target), case_name=args.case)
        output_dir = Path(args.output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        case = bundle.plant_cases[0]
        run_closed_loop_trial(
            output_dir,
            PIDGains(args.kp, args.ki, args.kd),
            case,
            bundle.target,
            bundle.runtime_limits,
            bundle.runtime_limits.seed + case.runtime.seed,
        )
    except ConfigError as exc:
        parser.print_usage(sys.stderr)
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
