from __future__ import annotations

import argparse
import sys
from pathlib import Path

from orchestrator.can_if import CanIfConfig, CanIfStatus
from orchestrator.config import ConfigError, load_config_bundle
from plant.can_io import VectorXlLibrary, can_if_init_vector_xl
from plant.roundtrip import PlantRoundtripError, run_plant_roundtrip


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify plant roundtrip over Vector XL CAN.")
    parser.add_argument("--target", required=True, help="Path to target_response.yaml")
    parser.add_argument("--case", required=True, help="Plant case name")
    parser.add_argument("--output-dir", required=True, help="Directory for waveform.csv and summary.json")
    parser.add_argument("--channel-index", type=int, default=0, help="Vector CAN channel index")
    parser.add_argument("--bitrate", type=int, default=500000, help="CAN bitrate")
    parser.add_argument("--steps", type=int, default=20, help="Number of roundtrip iterations")
    parser.add_argument("--control-output", type=float, default=1.0, help="Injected control output value")
    parser.add_argument("--timeout-ms", type=int, default=50, help="Per-step receive timeout")
    return parser


def _open_or_raise(handle, label: str) -> None:
    status = handle.open()
    if status != CanIfStatus.OK:
        raise PlantRoundtripError(f"{label} open failed with status={int(status)} last_error={handle.get_last_error()}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    host_handle = None
    plant_handle = None
    try:
        bundle = load_config_bundle(Path(args.target), case_name=args.case)
        case = bundle.plant_cases[0]
        output_dir = Path(args.output_dir).resolve()
        library = VectorXlLibrary()
        can_config = CanIfConfig(channel_index=args.channel_index, bitrate=args.bitrate, rx_timeout_ms=args.timeout_ms)
        host_handle = can_if_init_vector_xl(can_config, app_name="AutoTuningLMHost", library=library)
        plant_handle = can_if_init_vector_xl(can_config, app_name="AutoTuningLMPlant", library=library)
        _open_or_raise(host_handle, "host")
        _open_or_raise(plant_handle, "plant")
        result = run_plant_roundtrip(
            output_dir,
            host_handle,
            plant_handle,
            case,
            bundle.target,
            seed=bundle.runtime_limits.seed + case.runtime.seed,
            steps=args.steps,
            control_output=args.control_output,
            timeout_ms=args.timeout_ms,
        )
        print(
            f"Vector XL plant roundtrip succeeded: steps={args.steps} "
            f"last_measurement={result.last_measurement:.6f} output_dir={output_dir}"
        )
        return 0
    except (ConfigError, PlantRoundtripError) as exc:
        parser.print_usage(sys.stderr)
        print(f"Roundtrip error: {exc}", file=sys.stderr)
        return 2
    finally:
        if plant_handle is not None:
            plant_handle.deinit()
        if host_handle is not None:
            host_handle.deinit()


if __name__ == "__main__":
    raise SystemExit(main())
