from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestrator.llm_client import LlmClientError, LocalOvmsClient, OpenAIResponsesClient
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


class _FakeHttpResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._buffer = io.BytesIO(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def read(self) -> bytes:
        return self._buffer.read()

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


def _make_bundle(*, use_conversation_state: bool, provider: str = "openai_responses", endpoint: str | None = None) -> ConfigBundle:
    return ConfigBundle(
        root_dir=Path("."),
        target_path=Path("configs/target_response.yaml"),
        plant_cases_path=Path("configs/plant_cases.yaml"),
        limits_path=Path("configs/limits.yaml"),
        name="fixture",
        user_instruction=None,
        target=TargetSpec(1.0, 1.0, 2.5, 5.0, 0.01, False, False, False),
        evaluation=EvaluationWindow(6.0, 0.01, 0.0, 6.0, 1.0, 0.02),
        weights=ScoreWeights(0.1, 0.2, 0.25, 0.1, 0.1, 0.1, 0.1, 0.05),
        trial=TrialSettings(5, 3),
        limits=PIDLimits(0.01, 10.0, 0.0, 5.0, 0.0, 2.0),
        initial_pid=PIDGains(0.25, 0.05, 0.0),
        build=BuildConfig("mock", ["msbuild"], Path(".")),
        llm=LlmConfig(
            provider,
            "gpt-5.4" if provider == "openai_responses" else "OpenVINO/Qwen3-8B-int4-ov",
            endpoint or ("https://api.openai.com/v1/responses" if provider == "openai_responses" else "http://127.0.0.1:8000/v3/chat/completions"),
            "OPENAI_API_KEY",
            "pid_candidate_response",
            "en",
            use_conversation_state,
        ),
        runtime_limits=RuntimeLimits(4242, 0.25, 3.0, VectorXlRuntimeConfig(0, 500000, 125, 50, 1000, 50)),
        plant_cases=[
            PlantCase(
                "fixture_case",
                True,
                PlantModelConfig("first_order", {"gain": 1.0, "tau": 0.8}),
                0.0,
                NoiseConfig("none", 0.0),
                NonlinearConfig("none", 0.0),
                PlantRuntime(6.0, 0.01, 1001),
            )
        ],
    )


class OpenAIResponsesClientTest(unittest.TestCase):
    def test_openai_request_timeout_is_wrapped(self) -> None:
        bundle = _make_bundle(use_conversation_state=False)
        client = OpenAIResponsesClient(timeout_sec=1)

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), patch(
            "urllib.request.urlopen", side_effect=TimeoutError("timed out")
        ):
            with self.assertRaises(LlmClientError) as ctx:
                client.generate("SYSTEM", "USER", PIDGains(0.25, 0.05, 0.0), [], bundle)
        self.assertIn("timed out", str(ctx.exception))

    def test_generate_uses_conversation_state_when_enabled(self) -> None:
        bundle = _make_bundle(use_conversation_state=True)
        client = OpenAIResponsesClient()
        requests: list[tuple[str, dict[str, object]]] = []

        def fake_urlopen(req, timeout=0):  # noqa: ANN001, ARG001
            payload = json.loads(req.data.decode("utf-8"))
            requests.append((req.full_url, payload))
            if req.full_url.endswith("/conversations"):
                return _FakeHttpResponse({"id": "conv_123"})
            return _FakeHttpResponse(
                {
                    "output": [
                        {
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": '{"mode":"fine","next_candidate":{"Kp":1.0,"Ki":0.2,"Kd":0.0},"expectation":"ok","explanation":"ok","rationale":{"observed_issue":"ok","parameter_actions":{"Kp":"increase","Ki":"increase","Kd":"keep"},"expected_tradeoff":"ok","risk":"ok"}}',
                                }
                            ]
                        }
                    ]
                }
            )

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            response = client.generate("SYSTEM", "USER", PIDGains(0.25, 0.05, 0.0), [], bundle)
            self.assertIn('"next_candidate"', response)
            self.assertEqual(len(requests), 2)
            self.assertTrue(requests[0][0].endswith("/conversations"))
            self.assertEqual(requests[0][1]["items"][0]["role"], "developer")
            self.assertEqual(requests[0][1]["items"][0]["content"], "SYSTEM")
            self.assertTrue(requests[1][0].endswith("/responses"))
            self.assertEqual(requests[1][1]["conversation"], "conv_123")
            self.assertEqual(requests[1][1]["input"], "USER")
            self.assertNotIn("instructions", requests[1][1])
            self.assertEqual(client.last_metadata()["conversation_id"], "conv_123")

            client.generate("SYSTEM", "USER2", PIDGains(0.25, 0.05, 0.0), [], bundle)
            self.assertEqual(len(requests), 3)
            self.assertTrue(requests[2][0].endswith("/responses"))
            self.assertEqual(requests[2][1]["conversation"], "conv_123")

    def test_generate_sends_instructions_each_time_when_conversation_state_disabled(self) -> None:
        bundle = _make_bundle(use_conversation_state=False)
        client = OpenAIResponsesClient()
        requests: list[tuple[str, dict[str, object]]] = []

        def fake_urlopen(req, timeout=0):  # noqa: ANN001, ARG001
            payload = json.loads(req.data.decode("utf-8"))
            requests.append((req.full_url, payload))
            return _FakeHttpResponse(
                {
                    "output": [
                        {
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": '{"mode":"fine","next_candidate":{"Kp":1.0,"Ki":0.2,"Kd":0.0},"expectation":"ok","explanation":"ok","rationale":{"observed_issue":"ok","parameter_actions":{"Kp":"increase","Ki":"increase","Kd":"keep"},"expected_tradeoff":"ok","risk":"ok"}}',
                                }
                            ]
                        }
                    ]
                }
            )

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client.generate("SYSTEM", "USER", PIDGains(0.25, 0.05, 0.0), [], bundle)
        self.assertEqual(len(requests), 1)
        self.assertTrue(requests[0][0].endswith("/responses"))
        self.assertEqual(requests[0][1]["instructions"], "SYSTEM")
        self.assertNotIn("conversation", requests[0][1])


class LocalOvmsClientTest(unittest.TestCase):
    def test_ovms_request_timeout_is_wrapped(self) -> None:
        client = LocalOvmsClient(timeout_sec=1)
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            with self.assertRaises(LlmClientError) as ctx:
                client._request_json("http://127.0.0.1:8000/v3/chat/completions", {"model": "Qwen3-8B"})
        self.assertIn("timed out", str(ctx.exception))

    def test_generate_accumulates_messages_when_conversation_state_enabled(self) -> None:
        bundle = _make_bundle(use_conversation_state=True, provider="local_ovms")
        client = LocalOvmsClient()
        requests: list[dict[str, object]] = []

        def fake_request_json(url, payload=None, headers=None, *, method="POST"):  # noqa: ANN001, ARG001
            if method == "GET":
                return {"data": [{"id": "OpenVINO/Qwen3-8B-int4-ov"}]}
            requests.append(payload)
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"mode":"fine","next_candidate":{"Kp":1.0,"Ki":0.2,"Kd":0.0},"expectation":"ok","explanation":"ok","rationale":{"observed_issue":"ok","parameter_actions":{"Kp":"increase","Ki":"increase","Kd":"keep"},"expected_tradeoff":"ok","risk":"ok"}}'
                        }
                    }
                ]
            }

        with patch.object(client, "_request_json", side_effect=fake_request_json):
            client.generate("SYSTEM", "USER1", PIDGains(0.25, 0.05, 0.0), [], bundle)
            client.generate("SYSTEM", "USER2", PIDGains(0.25, 0.05, 0.0), [], bundle)

        self.assertEqual(len(requests), 2)
        self.assertEqual(client.last_metadata()["conversation_mode"], "system_side_conversation")
        self.assertEqual(requests[0]["messages"], [{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "USER1"}])
        self.assertEqual(
            requests[1]["messages"],
            [
                {"role": "system", "content": "SYSTEM"},
                {"role": "user", "content": "USER1"},
                {
                    "role": "assistant",
                    "content": '{"mode":"fine","next_candidate":{"Kp":1.0,"Ki":0.2,"Kd":0.0},"expectation":"ok","explanation":"ok","rationale":{"observed_issue":"ok","parameter_actions":{"Kp":"increase","Ki":"increase","Kd":"keep"},"expected_tradeoff":"ok","risk":"ok"}}',
                },
                {"role": "user", "content": "USER2"},
            ],
        )

    def test_generate_remains_stateless_when_conversation_state_disabled(self) -> None:
        bundle = _make_bundle(use_conversation_state=False, provider="local_ovms")
        client = LocalOvmsClient()
        requests: list[dict[str, object]] = []

        def fake_request_json(url, payload=None, headers=None, *, method="POST"):  # noqa: ANN001, ARG001
            if method == "GET":
                return {"data": [{"id": "OpenVINO/Qwen3-8B-int4-ov"}]}
            requests.append(payload)
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"mode":"fine","next_candidate":{"Kp":1.0,"Ki":0.2,"Kd":0.0},"expectation":"ok","explanation":"ok","rationale":{"observed_issue":"ok","parameter_actions":{"Kp":"increase","Ki":"increase","Kd":"keep"},"expected_tradeoff":"ok","risk":"ok"}}'
                        }
                    }
                ]
            }

        with patch.object(client, "_request_json", side_effect=fake_request_json):
            client.generate("SYSTEM", "USER1", PIDGains(0.25, 0.05, 0.0), [], bundle)
            client.generate("SYSTEM", "USER2", PIDGains(0.25, 0.05, 0.0), [], bundle)

        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0]["messages"], [{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "USER1"}])
        self.assertEqual(requests[1]["messages"], [{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "USER2"}])
