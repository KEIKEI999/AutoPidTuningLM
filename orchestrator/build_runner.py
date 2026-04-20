from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from orchestrator.models import BuildConfig


@dataclass
class BuildResult:
    status: str
    exit_code: int
    duration_ms: int
    stdout_text: str
    stderr_text: str
    command_text: str


class MockBuildRunner:
    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail

    def build(self, pid_params_path: Path) -> BuildResult:
        text = pid_params_path.read_text(encoding="utf-8")
        macros = ["PID_KP", "PID_KI", "PID_KD"]
        if self.should_fail or any(macro not in text for macro in macros):
            return BuildResult(
                status="failure",
                exit_code=1,
                duration_ms=10,
                stdout_text="Mock build started.\n",
                stderr_text="pid_params.h validation failed.\n",
                command_text="mock-build controller",
            )
        return BuildResult(
            status="success",
            exit_code=0,
            duration_ms=10,
            stdout_text="Mock build succeeded.\n",
            stderr_text="",
            command_text="mock-build controller",
        )


class MSBuildRunner:
    def __init__(self, build_config: BuildConfig) -> None:
        self.build_config = build_config

    def _resolve_msbuild(self) -> str | None:
        command = list(self.build_config.command)
        if command and Path(command[0]).exists():
            return command[0]
        found = shutil.which("msbuild")
        if found:
            return found
        candidates = [
            Path(r"C:\Program Files (x86)\Microsoft Visual Studio\2017\WDExpress\MSBuild\15.0\Bin\MSBuild.exe"),
            Path(r"C:\Program Files (x86)\Microsoft Visual Studio\2017\BuildTools\MSBuild\15.0\Bin\MSBuild.exe"),
            Path(r"C:\Program Files\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe"),
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return None

    def build(self, pid_params_path: Path) -> BuildResult:
        del pid_params_path
        msbuild_path = self._resolve_msbuild()
        if not msbuild_path:
            return BuildResult(
                status="failure",
                exit_code=1,
                duration_ms=0,
                stdout_text="",
                stderr_text="MSBuild.exe was not found.",
                command_text="msbuild <missing>",
            )

        args = list(self.build_config.command)
        if args and Path(args[0]).exists():
            command = args
        elif args and (args[0].lower().endswith("msbuild.exe") or args[0].lower() == "msbuild"):
            command = [msbuild_path, *args[1:]]
        else:
            command = [msbuild_path, *args]

        started = time.perf_counter()
        completed = subprocess.run(
            command,
            cwd=self.build_config.working_dir,
            capture_output=True,
            text=False,
            shell=False,
            check=False,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        stdout_text = _decode_output(completed.stdout)
        stderr_text = _decode_output(completed.stderr)
        return BuildResult(
            status="success" if completed.returncode == 0 else "failure",
            exit_code=completed.returncode,
            duration_ms=duration_ms,
            stdout_text=stdout_text,
            stderr_text=stderr_text,
            command_text=" ".join(command),
        )


def create_build_runner(build_config: BuildConfig) -> MockBuildRunner | MSBuildRunner:
    if build_config.mode == "msbuild":
        return MSBuildRunner(build_config)
    return MockBuildRunner(should_fail=False)


def _decode_output(payload: bytes) -> str:
    for encoding in ("utf-8", "cp932", "mbcs"):
        try:
            return payload.decode(encoding)
        except Exception:
            continue
    return payload.decode("utf-8", errors="replace")
