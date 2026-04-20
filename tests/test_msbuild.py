from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from orchestrator.build_runner import create_build_runner
from orchestrator.config import load_config_bundle
from tests.test_support import REPO_ROOT


class RealMSBuildIntegrationTest(unittest.TestCase):
    def test_real_msbuild_builds_controller_solution(self) -> None:
        candidates = [
            shutil.which("msbuild"),
            r"C:\Program Files (x86)\Microsoft Visual Studio\2017\WDExpress\MSBuild\15.0\Bin\MSBuild.exe",
        ]
        if not any(candidate and Path(candidate).exists() for candidate in candidates):
            self.skipTest("MSBuild is not available on this machine")

        bundle = load_config_bundle(
            REPO_ROOT / "configs" / "target_response.yaml",
            case_name="first_order_nominal",
            max_trials=1,
            build_mode="msbuild",
            can_adapter="stub",
        )
        runner = create_build_runner(bundle.build)
        result = runner.build(REPO_ROOT / "controller" / "include" / "pid_params.h")
        self.assertEqual(result.status, "success", msg=result.stderr_text or result.stdout_text)
        self.assertTrue((REPO_ROOT / "controller" / "build" / "Release" / "controller.exe").exists())

