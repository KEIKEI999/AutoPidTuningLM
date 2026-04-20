from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orchestrator.models import PIDGains
from orchestrator.pid_params import PIDParamsError, render_updated_pid_params
from tests.test_support import FIXTURES_DIR


class PidParamsUpdateTest(unittest.TestCase):
    def test_updates_only_pid_lines(self) -> None:
        template = (FIXTURES_DIR / "pid_params_template.h").read_text(encoding="utf-8")
        result = render_updated_pid_params(template, PIDGains(1.2, 0.35, 0.08))
        self.assertIn("#define PID_KP    (1.200000)", result.updated_text)
        self.assertIn("#define PID_KI    (0.350000)", result.updated_text)
        self.assertIn("#define PID_KD    (0.080000)", result.updated_text)
        changed_lines = [line for line in result.diff_text.splitlines() if line.startswith(("+", "-"))]
        self.assertTrue(all("PID_" in line or line.startswith(("---", "+++")) for line in changed_lines))

    def test_rejects_macro_outside_auto_edit(self) -> None:
        bad_template = """#ifndef PID_PARAMS_H\n#define PID_PARAMS_H\n#define PID_KP (0.1)\n/* AUTO_EDIT_BEGIN: PID_PARAMS */\n#define PID_KI (0.2)\n#define PID_KD (0.3)\n/* AUTO_EDIT_END: PID_PARAMS */\n#endif\n"""
        with self.assertRaises(PIDParamsError):
            render_updated_pid_params(bad_template, PIDGains(1.0, 1.0, 1.0))

