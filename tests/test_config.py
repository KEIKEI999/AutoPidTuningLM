from __future__ import annotations

import unittest
from pathlib import Path

from orchestrator.config import ConfigError, load_config_bundle
from tests.test_support import make_config_dir, workspace_temp_dir


class ConfigValidationTest(unittest.TestCase):
    def test_valid_config_bundle_loads(self) -> None:
        with workspace_temp_dir() as tmp:
            config_dir = make_config_dir(
                tmp,
                target="config_valid_target.yaml",
                cases="config_valid_cases.yaml",
                limits="config_valid_limits.yaml",
            )
            bundle = load_config_bundle(config_dir / "target_response.yaml")
            self.assertEqual(bundle.name, "fixture_target")
            self.assertIsNone(bundle.user_instruction)
            self.assertEqual(bundle.plant_cases[0].name, "fixture_case")
            self.assertEqual(bundle.runtime_limits.vector_xl.channel_index, 0)
            self.assertEqual(bundle.runtime_limits.vector_xl.bitrate, 500000)
            self.assertEqual(bundle.runtime_limits.vector_xl.exchange_timeout_ms, 1000)
            self.assertEqual(bundle.llm.provider, "local_ovms")
            self.assertEqual(bundle.llm.model, "OpenVINO/Qwen3-8B-int4-ov")
            self.assertEqual(bundle.llm.endpoint, "http://127.0.0.1:8000/v3/chat/completions")
            self.assertEqual(bundle.llm.prompt_language, "en")
            self.assertTrue(bundle.llm.use_conversation_state)

    def test_conversation_state_defaults_to_enabled_for_openai_and_ovms(self) -> None:
        with workspace_temp_dir() as tmp:
            config_dir = make_config_dir(
                tmp,
                target="config_valid_target.yaml",
                cases="config_valid_cases.yaml",
                limits="config_valid_limits.yaml",
            )
            limits_path = Path(config_dir / "limits.yaml")
            text = limits_path.read_text(encoding="utf-8")
            text = text.replace('"provider": "local_ovms"', '"provider": "openai_responses"')
            text = text.replace('"model": "OpenVINO/Qwen3-8B-int4-ov"', '"model": "gpt-5.4"')
            text = text.replace('"endpoint": "http://127.0.0.1:8000/v3/chat/completions"', '"endpoint": "https://api.openai.com/v1/responses"')
            limits_path.write_text(text, encoding="utf-8")
            bundle = load_config_bundle(config_dir / "target_response.yaml")
            self.assertTrue(bundle.llm.use_conversation_state)

    def test_vector_runtime_overrides_apply(self) -> None:
        with workspace_temp_dir() as tmp:
            config_dir = make_config_dir(
                tmp,
                target="config_valid_target.yaml",
                cases="config_valid_cases.yaml",
                limits="config_valid_limits.yaml",
            )
            bundle = load_config_bundle(
                config_dir / "target_response.yaml",
                vector_channel_index=2,
                vector_bitrate=250000,
                vector_rx_timeout_ms=80,
                vector_startup_wait_ms=10,
                vector_exchange_timeout_ms=700,
                vector_resend_interval_ms=20,
            )
            self.assertEqual(bundle.runtime_limits.vector_xl.channel_index, 2)
            self.assertEqual(bundle.runtime_limits.vector_xl.bitrate, 250000)
            self.assertEqual(bundle.runtime_limits.vector_xl.rx_timeout_ms, 80)
            self.assertEqual(bundle.runtime_limits.vector_xl.startup_wait_ms, 10)
            self.assertEqual(bundle.runtime_limits.vector_xl.exchange_timeout_ms, 700)
            self.assertEqual(bundle.runtime_limits.vector_xl.resend_interval_ms, 20)

    def test_missing_target_key_is_rejected(self) -> None:
        with workspace_temp_dir() as tmp:
            config_dir = make_config_dir(
                tmp,
                target="config_invalid_target_missing.yaml",
                cases="config_valid_cases.yaml",
                limits="config_valid_limits.yaml",
            )
            with self.assertRaises(ConfigError):
                load_config_bundle(config_dir / "target_response.yaml")

    def test_user_instruction_override_applies(self) -> None:
        with workspace_temp_dir() as tmp:
            config_dir = make_config_dir(
                tmp,
                target="config_valid_target.yaml",
                cases="config_valid_cases.yaml",
                limits="config_valid_limits.yaml",
            )
            bundle = load_config_bundle(
                config_dir / "target_response.yaml",
                user_instruction="Prefer monotonic response over aggressive rise time.",
            )
            self.assertEqual(bundle.user_instruction, "Prefer monotonic response over aggressive rise time.")

    def test_invalid_llm_provider_is_rejected(self) -> None:
        with workspace_temp_dir() as tmp:
            config_dir = make_config_dir(
                tmp,
                target="config_valid_target.yaml",
                cases="config_valid_cases.yaml",
                limits="config_valid_limits.yaml",
            )
            limits_path = Path(config_dir / "limits.yaml")
            text = limits_path.read_text(encoding="utf-8")
            limits_path.write_text(text.replace('"provider": "local_ovms"', '"provider": "unknown_provider"'), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config_bundle(config_dir / "target_response.yaml")

    def test_invalid_prompt_language_is_rejected(self) -> None:
        with workspace_temp_dir() as tmp:
            config_dir = make_config_dir(
                tmp,
                target="config_valid_target.yaml",
                cases="config_valid_cases.yaml",
                limits="config_valid_limits.yaml",
            )
            limits_path = Path(config_dir / "limits.yaml")
            text = limits_path.read_text(encoding="utf-8")
            limits_path.write_text(text.replace('"prompt_language": "en"', '"prompt_language": "fr"'), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config_bundle(config_dir / "target_response.yaml")

    def test_invalid_plant_type_is_rejected(self) -> None:
        with workspace_temp_dir() as tmp:
            config_dir = make_config_dir(
                tmp,
                target="config_valid_target.yaml",
                cases="config_invalid_cases_type.yaml",
                limits="config_valid_limits.yaml",
            )
            with self.assertRaises(ConfigError):
                load_config_bundle(config_dir / "target_response.yaml")

    def test_min_greater_than_max_is_rejected(self) -> None:
        with workspace_temp_dir() as tmp:
            config_dir = make_config_dir(
                tmp,
                target="config_valid_target.yaml",
                cases="config_valid_cases.yaml",
                limits="config_invalid_limits_minmax.yaml",
            )
            with self.assertRaises(ConfigError):
                load_config_bundle(config_dir / "target_response.yaml")
