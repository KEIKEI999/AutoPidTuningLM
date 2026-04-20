from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PIDGains:
    kp: float
    ki: float
    kd: float

    def rounded(self, digits: int = 6) -> "PIDGains":
        return PIDGains(
            kp=round(self.kp, digits),
            ki=round(self.ki, digits),
            kd=round(self.kd, digits),
        )

    def as_dict(self) -> dict[str, float]:
        return {"Kp": self.kp, "Ki": self.ki, "Kd": self.kd}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PIDGains":
        return cls(
            kp=float(payload["Kp"]),
            ki=float(payload["Ki"]),
            kd=float(payload["Kd"]),
        )


@dataclass(frozen=True)
class TargetSpec:
    setpoint: float
    rise_time_max: float
    settling_time_max: float
    overshoot_max: float
    steady_state_error_max: float
    allow_oscillation: bool
    allow_divergence: bool
    allow_saturation: bool


@dataclass(frozen=True)
class EvaluationWindow:
    duration_sec: float
    sampling_dt_sec: float
    evaluation_start_sec: float
    evaluation_end_sec: float
    steady_state_window_sec: float
    settling_band_ratio: float


@dataclass(frozen=True)
class ScoreWeights:
    rise_time: float
    settling_time: float
    overshoot: float
    steady_state_error: float
    iae: float
    control_variation: float
    oscillation_penalty: float
    divergence_penalty: float

    def as_dict(self) -> dict[str, float]:
        return {
            "rise_time": self.rise_time,
            "settling_time": self.settling_time,
            "overshoot": self.overshoot,
            "steady_state_error": self.steady_state_error,
            "iae": self.iae,
            "control_variation": self.control_variation,
            "oscillation_penalty": self.oscillation_penalty,
            "divergence_penalty": self.divergence_penalty,
        }


@dataclass(frozen=True)
class TrialSettings:
    max_trials: int
    abort_on_consecutive_failures: int


@dataclass(frozen=True)
class PIDLimits:
    kp_min: float
    kp_max: float
    ki_min: float
    ki_max: float
    kd_min: float
    kd_max: float

    def contains(self, gains: PIDGains) -> bool:
        return (
            self.kp_min <= gains.kp <= self.kp_max
            and self.ki_min <= gains.ki <= self.ki_max
            and self.kd_min <= gains.kd <= self.kd_max
        )

    def clamp(self, gains: PIDGains) -> PIDGains:
        return PIDGains(
            kp=min(max(gains.kp, self.kp_min), self.kp_max),
            ki=min(max(gains.ki, self.ki_min), self.ki_max),
            kd=min(max(gains.kd, self.kd_min), self.kd_max),
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "Kp_min": self.kp_min,
            "Kp_max": self.kp_max,
            "Ki_min": self.ki_min,
            "Ki_max": self.ki_max,
            "Kd_min": self.kd_min,
            "Kd_max": self.kd_max,
        }


@dataclass(frozen=True)
class BuildConfig:
    mode: str
    command: list[str]
    working_dir: Path


@dataclass(frozen=True)
class LlmConfig:
    provider: str
    model: str
    endpoint: str
    api_env: str | None
    json_schema_name: str
    prompt_language: str
    use_conversation_state: bool

    def as_dict(self) -> dict[str, str | bool | None]:
        return {
            "provider": self.provider,
            "model": self.model,
            "endpoint": self.endpoint,
            "api_env": self.api_env,
            "json_schema_name": self.json_schema_name,
            "prompt_language": self.prompt_language,
            "use_conversation_state": self.use_conversation_state,
        }


@dataclass(frozen=True)
class VectorXlRuntimeConfig:
    channel_index: int
    bitrate: int
    rx_timeout_ms: int
    startup_wait_ms: int
    exchange_timeout_ms: int
    resend_interval_ms: int

    def as_dict(self) -> dict[str, int]:
        return {
            "channel_index": self.channel_index,
            "bitrate": self.bitrate,
            "rx_timeout_ms": self.rx_timeout_ms,
            "startup_wait_ms": self.startup_wait_ms,
            "exchange_timeout_ms": self.exchange_timeout_ms,
            "resend_interval_ms": self.resend_interval_ms,
        }


@dataclass(frozen=True)
class RuntimeLimits:
    seed: int
    heartbeat_timeout_sec: float
    control_output_limit: float
    vector_xl: VectorXlRuntimeConfig

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "heartbeat_timeout_sec": self.heartbeat_timeout_sec,
            "control_output_limit": self.control_output_limit,
            "vector_xl": self.vector_xl.as_dict(),
        }


@dataclass(frozen=True)
class CandidateRationale:
    observed_issue: str
    parameter_actions: dict[str, str]
    expected_tradeoff: str
    risk: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "observed_issue": self.observed_issue,
            "parameter_actions": dict(self.parameter_actions),
            "expected_tradeoff": self.expected_tradeoff,
            "risk": self.risk,
        }


@dataclass(frozen=True)
class PlantModelConfig:
    type: str
    params: dict[str, float]


@dataclass(frozen=True)
class NoiseConfig:
    type: str
    stddev: float


@dataclass(frozen=True)
class NonlinearConfig:
    type: str
    gain: float


@dataclass(frozen=True)
class PlantRuntime:
    duration_sec: float
    dt_sec: float
    seed: int


@dataclass(frozen=True)
class PlantCase:
    name: str
    enabled: bool
    plant: PlantModelConfig
    deadtime_sec: float
    noise: NoiseConfig
    nonlinear: NonlinearConfig
    runtime: PlantRuntime


@dataclass(frozen=True)
class ConfigBundle:
    root_dir: Path
    target_path: Path
    plant_cases_path: Path
    limits_path: Path
    name: str
    user_instruction: str | None
    target: TargetSpec
    evaluation: EvaluationWindow
    weights: ScoreWeights
    trial: TrialSettings
    limits: PIDLimits
    initial_pid: PIDGains
    build: BuildConfig
    llm: LlmConfig
    runtime_limits: RuntimeLimits
    plant_cases: list[PlantCase]


@dataclass
class CandidateProposal:
    gains: PIDGains
    mode: str
    generator: str
    expectation: str
    explanation: str
    rationale: CandidateRationale
    llm_context: dict[str, Any]
    prompt_text: str
    response_text: str


@dataclass
class TrialArtifacts:
    trial_dir: Path
    summary_json: Path
    metrics_json: Path
    waveform_csv: Path
    pid_params_file: Path
    pid_params_diff: Path
    build_stdout: Path
    build_stderr: Path
    llm_prompt: Path
    llm_response: Path


@dataclass
class TrialRecord:
    trial_index: int
    candidate: PIDGains
    candidate_source: CandidateProposal
    trial_seed: int
    status: str
    failure_type: str | None
    build_status: str
    build_exit_code: int
    plant_case: PlantCase
    artifacts: TrialArtifacts
    metrics: dict[str, Any]
    metrics_detail: dict[str, Any]
    decision: dict[str, Any]
    states: list[str] = field(default_factory=list)
