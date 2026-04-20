from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from orchestrator.models import (
    BuildConfig,
    ConfigBundle,
    EvaluationWindow,
    LlmConfig,
    NoiseConfig,
    NonlinearConfig,
    PIDGains,
    PIDLimits,
    PlantCase,
    PlantModelConfig,
    PlantRuntime,
    RuntimeLimits,
    ScoreWeights,
    TargetSpec,
    TrialSettings,
    VectorXlRuntimeConfig,
)


SUPPORTED_PLANT_TYPES = {"first_order", "second_order"}
SUPPORTED_NOISE_TYPES = {"none", "gaussian"}
SUPPORTED_NONLINEAR_TYPES = {"none", "tanh"}
SUPPORTED_BUILD_MODES = {"mock", "msbuild"}
SUPPORTED_LLM_PROVIDERS = {"rule_based_stub", "local_ovms", "openai_responses"}
SUPPORTED_PROMPT_LANGUAGES = {"en", "ja"}


class ConfigError(ValueError):
    """Raised when configuration files are invalid."""


def _load_yaml_like(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        payload = yaml.safe_load(text)
    except ModuleNotFoundError:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"{path} could not be parsed. Install PyYAML or use JSON-compatible YAML."
            ) from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level.")
    return payload


def _require_mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"Missing or invalid mapping: {key}")
    return value


def _require_number(payload: dict[str, Any], key: str, *, non_negative: bool = False) -> float:
    if key not in payload:
        raise ConfigError(f"Missing required key: {key}")
    value = payload[key]
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ConfigError(f"{key} must be a finite number.")
    numeric = float(value)
    if non_negative and numeric < 0:
        raise ConfigError(f"{key} must be >= 0.")
    return numeric


def _require_bool(payload: dict[str, Any], key: str) -> bool:
    if key not in payload or not isinstance(payload[key], bool):
        raise ConfigError(f"{key} must be a boolean.")
    return bool(payload[key])


def _require_string(payload: dict[str, Any], key: str) -> str:
    if key not in payload or not isinstance(payload[key], str) or not payload[key].strip():
        raise ConfigError(f"{key} must be a non-empty string.")
    return payload[key].strip()


def _parse_target_response(payload: dict[str, Any]) -> tuple[str, TargetSpec, EvaluationWindow, ScoreWeights, TrialSettings]:
    name = _require_string(payload, "name")
    target_map = _require_mapping(payload, "target")
    eval_map = _require_mapping(payload, "evaluation")
    weights_map = _require_mapping(payload, "weights")
    trial_map = _require_mapping(payload, "trial")

    target = TargetSpec(
        setpoint=_require_number(target_map, "setpoint"),
        rise_time_max=_require_number(target_map, "rise_time_max", non_negative=True),
        settling_time_max=_require_number(target_map, "settling_time_max", non_negative=True),
        overshoot_max=_require_number(target_map, "overshoot_max", non_negative=True),
        steady_state_error_max=_require_number(target_map, "steady_state_error_max", non_negative=True),
        allow_oscillation=_require_bool(target_map, "allow_oscillation"),
        allow_divergence=_require_bool(target_map, "allow_divergence"),
        allow_saturation=_require_bool(target_map, "allow_saturation"),
    )
    evaluation = EvaluationWindow(
        duration_sec=_require_number(eval_map, "duration_sec", non_negative=True),
        sampling_dt_sec=_require_number(eval_map, "sampling_dt_sec", non_negative=True),
        evaluation_start_sec=_require_number(eval_map, "evaluation_start_sec", non_negative=True),
        evaluation_end_sec=_require_number(eval_map, "evaluation_end_sec", non_negative=True),
        steady_state_window_sec=_require_number(eval_map, "steady_state_window_sec", non_negative=True),
        settling_band_ratio=_require_number(eval_map, "settling_band_ratio", non_negative=True),
    )
    if evaluation.sampling_dt_sec <= 0:
        raise ConfigError("evaluation.sampling_dt_sec must be > 0.")
    if evaluation.evaluation_end_sec < evaluation.evaluation_start_sec:
        raise ConfigError("evaluation_end_sec must be >= evaluation_start_sec.")
    if evaluation.evaluation_end_sec > evaluation.duration_sec:
        raise ConfigError("evaluation_end_sec must be <= duration_sec.")

    weights = ScoreWeights(
        rise_time=_require_number(weights_map, "rise_time", non_negative=True),
        settling_time=_require_number(weights_map, "settling_time", non_negative=True),
        overshoot=_require_number(weights_map, "overshoot", non_negative=True),
        steady_state_error=_require_number(weights_map, "steady_state_error", non_negative=True),
        iae=_require_number(weights_map, "iae", non_negative=True),
        control_variation=_require_number(weights_map, "control_variation", non_negative=True),
        oscillation_penalty=_require_number(weights_map, "oscillation_penalty", non_negative=True),
        divergence_penalty=_require_number(weights_map, "divergence_penalty", non_negative=True),
    )
    trial = TrialSettings(
        max_trials=int(_require_number(trial_map, "max_trials", non_negative=True)),
        abort_on_consecutive_failures=int(
            _require_number(trial_map, "abort_on_consecutive_failures", non_negative=True)
        ),
    )
    if trial.max_trials <= 0:
        raise ConfigError("trial.max_trials must be > 0.")
    if trial.abort_on_consecutive_failures <= 0:
        raise ConfigError("trial.abort_on_consecutive_failures must be > 0.")
    return name, target, evaluation, weights, trial


def _parse_limits(
    payload: dict[str, Any],
    target_payload: dict[str, Any],
    root_dir: Path,
) -> tuple[PIDLimits, PIDGains, BuildConfig, LlmConfig, RuntimeLimits]:
    pid_limits_map = payload.get("pid_limits")
    if not isinstance(pid_limits_map, dict):
        pid_limits_map = target_payload.get("limits")
    if not isinstance(pid_limits_map, dict):
        raise ConfigError("limits.yaml requires pid_limits or target_response.yaml requires limits.")

    limits = PIDLimits(
        kp_min=_require_number(pid_limits_map, "Kp_min"),
        kp_max=_require_number(pid_limits_map, "Kp_max"),
        ki_min=_require_number(pid_limits_map, "Ki_min"),
        ki_max=_require_number(pid_limits_map, "Ki_max"),
        kd_min=_require_number(pid_limits_map, "Kd_min"),
        kd_max=_require_number(pid_limits_map, "Kd_max"),
    )
    if limits.kp_min > limits.kp_max or limits.ki_min > limits.ki_max or limits.kd_min > limits.kd_max:
        raise ConfigError("PID limit min values must be <= max values.")

    initial_pid_map = _require_mapping(payload, "initial_pid")
    initial_pid = PIDGains.from_dict(initial_pid_map)
    if not limits.contains(initial_pid):
        raise ConfigError("initial_pid must be within pid_limits.")

    build_map = _require_mapping(payload, "build")
    mode = _require_string(build_map, "mode")
    if mode not in SUPPORTED_BUILD_MODES:
        raise ConfigError(f"Unsupported build.mode: {mode}")
    command_value = build_map.get("command", [])
    if not isinstance(command_value, list) or not all(isinstance(v, str) for v in command_value):
        raise ConfigError("build.command must be a list of strings.")
    working_dir_value = build_map.get("working_dir", ".")
    if not isinstance(working_dir_value, str):
        raise ConfigError("build.working_dir must be a string.")
    build = BuildConfig(
        mode=mode,
        command=list(command_value),
        working_dir=(root_dir / working_dir_value).resolve(),
    )

    llm_map = payload.get("llm", {})
    if llm_map is None:
        llm_map = {}
    if not isinstance(llm_map, dict):
        raise ConfigError("limits.llm must be a mapping.")
    provider = str(llm_map.get("provider", "rule_based_stub")).strip()
    if provider not in SUPPORTED_LLM_PROVIDERS:
        raise ConfigError(f"Unsupported llm.provider: {provider}")
    model = str(llm_map.get("model", "")).strip()
    if not model:
        default_model = {
            "rule_based_stub": "rule-based-stub",
            "local_ovms": "OpenVINO/Qwen3-8B-int4-ov",
            "openai_responses": "gpt-5.4",
        }
        model = default_model[provider]
    endpoint = str(llm_map.get("endpoint", "")).strip()
    if not endpoint:
        default_endpoint = {
            "rule_based_stub": "stub://rule-based",
            "local_ovms": "http://127.0.0.1:8000/v3/chat/completions",
            "openai_responses": "https://api.openai.com/v1/responses",
        }
        endpoint = default_endpoint[provider]
    api_env = llm_map.get("api_env")
    if api_env is not None and (not isinstance(api_env, str) or not api_env.strip()):
        raise ConfigError("limits.llm.api_env must be a non-empty string when set.")
    json_schema_name = str(llm_map.get("json_schema_name", "pid_candidate_response")).strip()
    if not json_schema_name:
        raise ConfigError("limits.llm.json_schema_name must be a non-empty string.")
    prompt_language = str(llm_map.get("prompt_language", "en")).strip()
    if prompt_language not in SUPPORTED_PROMPT_LANGUAGES:
        raise ConfigError(f"Unsupported llm.prompt_language: {prompt_language}")
    use_conversation_state_value = llm_map.get("use_conversation_state")
    if use_conversation_state_value is None:
        use_conversation_state = provider in {"openai_responses", "local_ovms"}
    elif isinstance(use_conversation_state_value, bool):
        use_conversation_state = use_conversation_state_value
    else:
        raise ConfigError("limits.llm.use_conversation_state must be a boolean when set.")
    llm = LlmConfig(
        provider=provider,
        model=model,
        endpoint=endpoint,
        api_env=None if api_env is None else api_env.strip(),
        json_schema_name=json_schema_name,
        prompt_language=prompt_language,
        use_conversation_state=use_conversation_state,
    )

    runtime_map = _require_mapping(payload, "runtime")
    heartbeat_timeout_sec = float(runtime_map.get("heartbeat_timeout_sec", 0.25))
    control_output_limit = float(runtime_map.get("control_output_limit", 3.0))
    vector_map = runtime_map.get("vector_xl", {})
    if not isinstance(vector_map, dict):
        raise ConfigError("runtime.vector_xl must be a mapping.")
    default_rx_timeout_ms = max(100, int(heartbeat_timeout_sec * 1000 * 0.5))
    default_exchange_timeout_ms = max(default_rx_timeout_ms * 8, 1000)
    vector_xl = VectorXlRuntimeConfig(
        channel_index=int(float(vector_map.get("channel_index", 0))),
        bitrate=int(float(vector_map.get("bitrate", 500000))),
        rx_timeout_ms=int(float(vector_map.get("rx_timeout_ms", default_rx_timeout_ms))),
        startup_wait_ms=int(float(vector_map.get("startup_wait_ms", 50))),
        exchange_timeout_ms=int(float(vector_map.get("exchange_timeout_ms", default_exchange_timeout_ms))),
        resend_interval_ms=int(float(vector_map.get("resend_interval_ms", 50))),
    )
    runtime_limits = RuntimeLimits(
        seed=int(float(runtime_map.get("seed", 12345))),
        heartbeat_timeout_sec=heartbeat_timeout_sec,
        control_output_limit=control_output_limit,
        vector_xl=vector_xl,
    )
    if runtime_limits.heartbeat_timeout_sec <= 0:
        raise ConfigError("runtime.heartbeat_timeout_sec must be > 0.")
    if runtime_limits.control_output_limit <= 0:
        raise ConfigError("runtime.control_output_limit must be > 0.")
    if runtime_limits.vector_xl.channel_index < 0:
        raise ConfigError("runtime.vector_xl.channel_index must be >= 0.")
    if runtime_limits.vector_xl.bitrate <= 0:
        raise ConfigError("runtime.vector_xl.bitrate must be > 0.")
    if runtime_limits.vector_xl.rx_timeout_ms <= 0:
        raise ConfigError("runtime.vector_xl.rx_timeout_ms must be > 0.")
    if runtime_limits.vector_xl.startup_wait_ms < 0:
        raise ConfigError("runtime.vector_xl.startup_wait_ms must be >= 0.")
    if runtime_limits.vector_xl.exchange_timeout_ms <= 0:
        raise ConfigError("runtime.vector_xl.exchange_timeout_ms must be > 0.")
    if runtime_limits.vector_xl.resend_interval_ms <= 0:
        raise ConfigError("runtime.vector_xl.resend_interval_ms must be > 0.")
    if runtime_limits.vector_xl.resend_interval_ms > runtime_limits.vector_xl.exchange_timeout_ms:
        raise ConfigError("runtime.vector_xl.resend_interval_ms must be <= exchange_timeout_ms.")
    return limits, initial_pid, build, llm, runtime_limits


def _override_msbuild_property(command: list[str], property_name: str, value: str) -> list[str]:
    prefix = f"/p:{property_name}="
    updated: list[str] = []
    replaced = False
    for item in command:
        if item.startswith(prefix):
            updated.append(f"{prefix}{value}")
            replaced = True
        else:
            updated.append(item)
    if not replaced:
        updated.append(f"{prefix}{value}")
    return updated


def _parse_case(payload: dict[str, Any], evaluation: EvaluationWindow) -> PlantCase:
    name = _require_string(payload, "name")
    enabled = bool(payload.get("enabled", True))
    plant_map = _require_mapping(payload, "plant")
    plant_type = _require_string(plant_map, "type")
    if plant_type not in SUPPORTED_PLANT_TYPES:
        raise ConfigError(f"Unsupported plant type: {plant_type}")
    params = {key: float(value) for key, value in plant_map.items() if key != "type"}
    if plant_type == "first_order":
        if "gain" not in params or "tau" not in params:
            raise ConfigError(f"{name}: first_order requires gain and tau.")
        if params["tau"] <= 0:
            raise ConfigError(f"{name}: tau must be > 0.")
    if plant_type == "second_order":
        if "wn" not in params or "zeta" not in params:
            raise ConfigError(f"{name}: second_order requires wn and zeta.")
        if params["wn"] <= 0 or params["zeta"] <= 0:
            raise ConfigError(f"{name}: wn and zeta must be > 0.")
        params.setdefault("gain", 1.0)

    noise_map = _require_mapping(payload, "noise")
    noise_type = _require_string(noise_map, "type")
    if noise_type not in SUPPORTED_NOISE_TYPES:
        raise ConfigError(f"{name}: unsupported noise type {noise_type}")
    noise = NoiseConfig(type=noise_type, stddev=float(noise_map.get("stddev", 0.0)))

    nonlinear_map = _require_mapping(payload, "nonlinear")
    nonlinear_type = _require_string(nonlinear_map, "type")
    if nonlinear_type not in SUPPORTED_NONLINEAR_TYPES:
        raise ConfigError(f"{name}: unsupported nonlinear type {nonlinear_type}")
    nonlinear = NonlinearConfig(type=nonlinear_type, gain=float(nonlinear_map.get("gain", 0.0)))

    runtime_map = _require_mapping(payload, "runtime")
    runtime = PlantRuntime(
        duration_sec=_require_number(runtime_map, "duration_sec", non_negative=True),
        dt_sec=_require_number(runtime_map, "dt_sec", non_negative=True),
        seed=int(_require_number(runtime_map, "seed", non_negative=True)),
    )
    if runtime.duration_sec <= 0 or runtime.dt_sec <= 0:
        raise ConfigError(f"{name}: runtime.duration_sec and dt_sec must be > 0.")
    if evaluation.evaluation_end_sec > runtime.duration_sec:
        raise ConfigError(f"{name}: evaluation_end_sec exceeds runtime.duration_sec.")

    deadtime_sec = _require_number(payload, "deadtime_sec", non_negative=True)
    return PlantCase(
        name=name,
        enabled=enabled,
        plant=PlantModelConfig(type=plant_type, params=params),
        deadtime_sec=deadtime_sec,
        noise=noise,
        nonlinear=nonlinear,
        runtime=runtime,
    )


def load_config_bundle(
    target_path: Path,
    *,
    case_name: str | None = None,
    max_trials: int | None = None,
    user_instruction: str | None = None,
    build_mode: str | None = None,
    can_adapter: str | None = None,
    vector_channel_index: int | None = None,
    vector_bitrate: int | None = None,
    vector_rx_timeout_ms: int | None = None,
    vector_startup_wait_ms: int | None = None,
    vector_exchange_timeout_ms: int | None = None,
    vector_resend_interval_ms: int | None = None,
) -> ConfigBundle:
    target_path = target_path.resolve()
    if not target_path.exists():
        raise ConfigError(f"Config file not found: {target_path}")
    config_dir = target_path.parent
    plant_cases_path = config_dir / "plant_cases.yaml"
    limits_path = config_dir / "limits.yaml"
    if not plant_cases_path.exists():
        raise ConfigError(f"plant_cases.yaml not found next to {target_path.name}")
    if not limits_path.exists():
        raise ConfigError(f"limits.yaml not found next to {target_path.name}")

    target_payload = _load_yaml_like(target_path)
    plant_payload = _load_yaml_like(plant_cases_path)
    limits_payload = _load_yaml_like(limits_path)

    name, target, evaluation, weights, trial = _parse_target_response(target_payload)
    configured_user_instruction = target_payload.get("user_instruction")
    if configured_user_instruction is not None:
        if not isinstance(configured_user_instruction, str) or not configured_user_instruction.strip():
            raise ConfigError("target_response.user_instruction must be a non-empty string when set.")
        configured_user_instruction = configured_user_instruction.strip()
    if user_instruction is not None:
        if not user_instruction.strip():
            raise ConfigError("--user-instruction must be a non-empty string.")
        configured_user_instruction = user_instruction.strip()
    limits, initial_pid, build, llm, runtime_limits = _parse_limits(limits_payload, target_payload, config_dir.parent)
    if build_mode is not None:
        if build_mode not in SUPPORTED_BUILD_MODES:
            raise ConfigError(f"Unsupported build mode override: {build_mode}")
        build = BuildConfig(mode=build_mode, command=list(build.command), working_dir=build.working_dir)
    if can_adapter is not None:
        build = BuildConfig(
            mode=build.mode,
            command=_override_msbuild_property(list(build.command), "CanAdapter", can_adapter),
            working_dir=build.working_dir,
        )
    if any(
        value is not None
        for value in (
            vector_channel_index,
            vector_bitrate,
            vector_rx_timeout_ms,
            vector_startup_wait_ms,
            vector_exchange_timeout_ms,
            vector_resend_interval_ms,
        )
    ):
        vector_xl = VectorXlRuntimeConfig(
            channel_index=runtime_limits.vector_xl.channel_index if vector_channel_index is None else vector_channel_index,
            bitrate=runtime_limits.vector_xl.bitrate if vector_bitrate is None else vector_bitrate,
            rx_timeout_ms=runtime_limits.vector_xl.rx_timeout_ms if vector_rx_timeout_ms is None else vector_rx_timeout_ms,
            startup_wait_ms=runtime_limits.vector_xl.startup_wait_ms if vector_startup_wait_ms is None else vector_startup_wait_ms,
            exchange_timeout_ms=(
                runtime_limits.vector_xl.exchange_timeout_ms
                if vector_exchange_timeout_ms is None
                else vector_exchange_timeout_ms
            ),
            resend_interval_ms=(
                runtime_limits.vector_xl.resend_interval_ms
                if vector_resend_interval_ms is None
                else vector_resend_interval_ms
            ),
        )
        runtime_limits = RuntimeLimits(
            seed=runtime_limits.seed,
            heartbeat_timeout_sec=runtime_limits.heartbeat_timeout_sec,
            control_output_limit=runtime_limits.control_output_limit,
            vector_xl=vector_xl,
        )
        if runtime_limits.vector_xl.channel_index < 0:
            raise ConfigError("--vector-channel-index must be >= 0.")
        if runtime_limits.vector_xl.bitrate <= 0:
            raise ConfigError("--vector-bitrate must be > 0.")
        if runtime_limits.vector_xl.rx_timeout_ms <= 0:
            raise ConfigError("--vector-rx-timeout-ms must be > 0.")
        if runtime_limits.vector_xl.startup_wait_ms < 0:
            raise ConfigError("--vector-startup-wait-ms must be >= 0.")
        if runtime_limits.vector_xl.exchange_timeout_ms <= 0:
            raise ConfigError("--vector-exchange-timeout-ms must be > 0.")
        if runtime_limits.vector_xl.resend_interval_ms <= 0:
            raise ConfigError("--vector-resend-interval-ms must be > 0.")
        if runtime_limits.vector_xl.resend_interval_ms > runtime_limits.vector_xl.exchange_timeout_ms:
            raise ConfigError("--vector-resend-interval-ms must be <= --vector-exchange-timeout-ms.")

    runtime_map = limits_payload.get("runtime", {})
    if not isinstance(runtime_map, dict):
        raise ConfigError("limits.runtime must be a mapping.")
    abort_on_failures = int(runtime_map.get("abort_on_consecutive_failures", trial.abort_on_consecutive_failures))
    if abort_on_failures <= 0:
        raise ConfigError("abort_on_consecutive_failures must be > 0.")

    cases_value = plant_payload.get("cases")
    if not isinstance(cases_value, list):
        raise ConfigError("plant_cases.yaml must contain a list under cases.")
    cases = [_parse_case(case, evaluation) for case in cases_value if isinstance(case, dict)]
    enabled_cases = [case for case in cases if case.enabled]
    if not enabled_cases:
        raise ConfigError("No enabled plant case found.")

    if case_name is not None:
        enabled_cases = [case for case in enabled_cases if case.name == case_name]
        if not enabled_cases:
            raise ConfigError(f"Plant case not found or disabled: {case_name}")

    if max_trials is not None:
        if max_trials <= 0:
            raise ConfigError("--max-trials must be > 0.")
        trial = TrialSettings(max_trials=max_trials, abort_on_consecutive_failures=abort_on_failures)
    else:
        trial = TrialSettings(max_trials=trial.max_trials, abort_on_consecutive_failures=abort_on_failures)

    return ConfigBundle(
        root_dir=config_dir.parent,
        target_path=target_path,
        plant_cases_path=plant_cases_path.resolve(),
        limits_path=limits_path.resolve(),
        name=name,
        user_instruction=configured_user_instruction,
        target=target,
        evaluation=evaluation,
        weights=weights,
        trial=trial,
        limits=limits,
        initial_pid=initial_pid,
        build=build,
        llm=llm,
        runtime_limits=runtime_limits,
        plant_cases=enabled_cases,
    )


def summarize_bundle(bundle: ConfigBundle) -> dict[str, Any]:
    return {
        "name": bundle.name,
        "user_instruction": bundle.user_instruction,
        "target_path": str(bundle.target_path),
        "plant_cases_path": str(bundle.plant_cases_path),
        "limits_path": str(bundle.limits_path),
        "max_trials": bundle.trial.max_trials,
        "abort_on_consecutive_failures": bundle.trial.abort_on_consecutive_failures,
        "build_mode": bundle.build.mode,
        "build_command": bundle.build.command,
        "llm": bundle.llm.as_dict(),
        "initial_pid": bundle.initial_pid.as_dict(),
        "pid_limits": bundle.limits.as_dict(),
        "runtime": bundle.runtime_limits.as_dict(),
        "cases": [case.name for case in bundle.plant_cases],
    }
