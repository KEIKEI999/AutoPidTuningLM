from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.config import ConfigError, load_config_bundle, summarize_bundle
from orchestrator.tuner import Tuner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PID auto-tuning MVP orchestrator")
    parser.add_argument("--config", required=True, help="Path to target_response.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Validate config only")
    parser.add_argument("--case", help="Restrict execution to a single plant case")
    parser.add_argument("--max-trials", type=int, help="Temporarily override max_trials")
    parser.add_argument("--user-instruction", help="Optional one-shot operator instruction added to the internal LLM prompt")
    parser.add_argument("--user-instruction-file", help="Path to a UTF-8 text file containing optional operator instruction")
    parser.add_argument("--output-dir", default="results/latest_run", help="Output directory")
    parser.add_argument("--build-mode", choices=["mock", "msbuild"], help="Override build backend")
    parser.add_argument("--can-adapter", choices=["stub", "vector_xl"], help="Override controller CAN adapter")
    parser.add_argument("--vector-channel-index", type=int, help="Override Vector XL channel index")
    parser.add_argument("--vector-bitrate", type=int, help="Override Vector XL bitrate")
    parser.add_argument("--vector-rx-timeout-ms", type=int, help="Override Vector XL receive timeout in ms")
    parser.add_argument("--vector-startup-wait-ms", type=int, help="Override controller startup wait in ms")
    parser.add_argument("--vector-exchange-timeout-ms", type=int, help="Override controller exchange timeout in ms")
    parser.add_argument("--vector-resend-interval-ms", type=int, help="Override controller resend interval in ms")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.user_instruction and args.user_instruction_file:
        parser.print_usage(sys.stderr)
        print("Configuration error: --user-instruction and --user-instruction-file cannot be used together.", file=sys.stderr)
        return 2

    instruction_text = None
    if args.user_instruction_file:
        try:
            instruction_text = Path(args.user_instruction_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            parser.print_usage(sys.stderr)
            print(f"Configuration error: failed to read --user-instruction-file: {exc}", file=sys.stderr)
            return 2
        if not instruction_text:
            parser.print_usage(sys.stderr)
            print("Configuration error: --user-instruction-file is empty.", file=sys.stderr)
            return 2
    elif args.user_instruction:
        instruction_text = args.user_instruction

    try:
        bundle = load_config_bundle(
            Path(args.config),
            case_name=args.case,
            max_trials=args.max_trials,
            user_instruction=instruction_text,
            build_mode=args.build_mode,
            can_adapter=args.can_adapter,
            vector_channel_index=args.vector_channel_index,
            vector_bitrate=args.vector_bitrate,
            vector_rx_timeout_ms=args.vector_rx_timeout_ms,
            vector_startup_wait_ms=args.vector_startup_wait_ms,
            vector_exchange_timeout_ms=args.vector_exchange_timeout_ms,
            vector_resend_interval_ms=args.vector_resend_interval_ms,
        )
    except ConfigError as exc:
        parser.print_usage(sys.stderr)
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    first_case = bundle.plant_cases[0].name if bundle.plant_cases else "<none>"
    print(
        f"Start tuning: case={first_case} max_trials={bundle.trial.max_trials} "
        f"seed={bundle.runtime_limits.seed} output_dir={Path(args.output_dir).resolve()}"
    )

    if args.dry_run:
        print(json.dumps(summarize_bundle(bundle), ensure_ascii=False, indent=2))
        return 0

    output_dir = Path(args.output_dir).resolve()
    tuner = Tuner(bundle, output_dir)
    result = tuner.run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "FINISHED" else 4


if __name__ == "__main__":
    raise SystemExit(main())
