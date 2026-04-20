from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from orchestrator.models import ConfigBundle, PIDGains, TrialRecord


class LlmClientError(RuntimeError):
    """Raised when an LLM backend cannot provide a valid response."""


class BaseLlmClient:
    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        best: PIDGains,
        history: list[TrialRecord],
        bundle: ConfigBundle,
    ) -> str:
        raise NotImplementedError

    def last_metadata(self) -> dict[str, Any]:
        return {}


def _pid_candidate_response_schema(bundle: ConfigBundle) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": bundle.llm.json_schema_name,
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["coarse", "fine"],
                    },
                    "next_candidate": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "Kp": {"type": "number"},
                            "Ki": {"type": "number"},
                            "Kd": {"type": "number"},
                        },
                        "required": ["Kp", "Ki", "Kd"],
                    },
                    "expectation": {"type": "string"},
                    "explanation": {"type": "string"},
                    "rationale": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "observed_issue": {"type": "string"},
                            "parameter_actions": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "Kp": {"type": "string", "enum": ["increase", "decrease", "keep"]},
                                    "Ki": {"type": "string", "enum": ["increase", "decrease", "keep"]},
                                    "Kd": {"type": "string", "enum": ["increase", "decrease", "keep"]},
                                },
                                "required": ["Kp", "Ki", "Kd"],
                            },
                            "expected_tradeoff": {"type": "string"},
                            "risk": {"type": "string"},
                        },
                        "required": ["observed_issue", "parameter_actions", "expected_tradeoff", "risk"],
                    },
                },
                "required": ["mode", "next_candidate", "expectation", "explanation", "rationale"],
            },
        },
    }


@dataclass
class LocalOvmsClient(BaseLlmClient):
    timeout_sec: int = 120
    session_messages: list[dict[str, str]] | None = None
    latest_metadata: dict[str, Any] | None = None

    def _request_json(
        self,
        url: str,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        *,
        method: str = "POST",
    ) -> dict[str, Any]:
        raw_body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        req = request.Request(url, data=raw_body, headers=request_headers, method=method)
        try:
            with request.urlopen(req, timeout=self.timeout_sec) as response:
                body = response.read().decode("utf-8")
        except (TimeoutError, socket.timeout) as exc:
            raise LlmClientError(f"OVMS request timed out after {self.timeout_sec} seconds") from exc
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LlmClientError(f"OVMS request failed: HTTP {exc.code} {detail}") from exc
        except error.URLError as exc:
            raise LlmClientError(f"OVMS request failed: {exc}") from exc
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise LlmClientError("OVMS response was not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise LlmClientError("OVMS response must be a JSON object")
        return parsed

    def _discover_model(self, bundle: ConfigBundle) -> str:
        endpoint = bundle.llm.endpoint
        base_url = endpoint.rsplit("/", 2)[0]
        payload = self._request_json(f"{base_url}/models", method="GET")
        data = payload.get("data")
        if not isinstance(data, list):
            raise LlmClientError("OVMS /v3/models response missing data list")
        available = [item.get("id") for item in data if isinstance(item, dict) and isinstance(item.get("id"), str)]
        configured = bundle.llm.model
        if configured in available:
            return configured
        if len(available) == 1:
            return available[0]
        raise LlmClientError(
            f"Configured OVMS model '{configured}' was not found. Available models: {available or '<none>'}"
        )

    def _ensure_session_messages(self, system_prompt: str) -> list[dict[str, str]]:
        if self.session_messages is None:
            self.session_messages = [{"role": "system", "content": system_prompt}]
        return self.session_messages

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        best: PIDGains,
        history: list[TrialRecord],
        bundle: ConfigBundle,
    ) -> str:
        del best, history
        model_name = self._discover_model(bundle)
        if bundle.llm.use_conversation_state:
            messages = [*self._ensure_session_messages(system_prompt), {"role": "user", "content": user_prompt}]
        else:
            messages = [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ]
        payload = {
            "model": model_name,
            "messages": messages,
            "stream": False,
            "temperature": 0.0,
            "max_completion_tokens": 240,
            "response_format": _pid_candidate_response_schema(bundle),
            "chat_template_kwargs": {"enable_thinking": False},
        }
        response = self._request_json(bundle.llm.endpoint, payload)
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LlmClientError("OVMS response missing choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise LlmClientError("OVMS response choice must be an object")
        message = first.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise LlmClientError("OVMS response missing message.content")
        content = str(message["content"])
        self.latest_metadata = {
            "provider": bundle.llm.provider,
            "configured_use_conversation_state": bundle.llm.use_conversation_state,
            "conversation_mode": "system_side_conversation" if bundle.llm.use_conversation_state else "stateless",
            "model": model_name,
            "messages_sent": len(messages),
        }
        if bundle.llm.use_conversation_state:
            session_messages = self._ensure_session_messages(system_prompt)
            session_messages.append({"role": "user", "content": user_prompt})
            session_messages.append({"role": "assistant", "content": content})
            self.latest_metadata["session_message_count"] = len(session_messages)
        return content

    def last_metadata(self) -> dict[str, Any]:
        return {} if self.latest_metadata is None else dict(self.latest_metadata)


@dataclass
class OpenAIResponsesClient(BaseLlmClient):
    timeout_sec: int = 120
    conversation_id: str | None = None
    latest_metadata: dict[str, Any] | None = None

    def _request_json(self, url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        req = request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_sec) as response:
                body = response.read().decode("utf-8")
        except (TimeoutError, socket.timeout) as exc:
            raise LlmClientError(f"OpenAI request timed out after {self.timeout_sec} seconds") from exc
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LlmClientError(f"OpenAI request failed: HTTP {exc.code} {detail}") from exc
        except error.URLError as exc:
            raise LlmClientError(f"OpenAI request failed: {exc}") from exc
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise LlmClientError("OpenAI response must be a JSON object")
        return parsed

    def _api_root(self, bundle: ConfigBundle) -> str:
        if bundle.llm.endpoint.endswith("/responses"):
            return bundle.llm.endpoint[: -len("/responses")]
        return bundle.llm.endpoint.rsplit("/", 1)[0]

    def _ensure_conversation(self, bundle: ConfigBundle, api_key: str, system_prompt: str) -> str:
        if self.conversation_id is not None:
            return self.conversation_id
        payload = {
            "metadata": {
                "project": "AutoTuningLM",
                "llm_model": bundle.llm.model,
            },
            "items": [
                {
                    "type": "message",
                    "role": "developer",
                    "content": system_prompt,
                }
            ],
        }
        response = self._request_json(f"{self._api_root(bundle)}/conversations", api_key, payload)
        conversation_id = response.get("id")
        if not isinstance(conversation_id, str) or not conversation_id:
            raise LlmClientError("OpenAI conversation creation did not return an id")
        self.conversation_id = conversation_id
        return conversation_id

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        best: PIDGains,
        history: list[TrialRecord],
        bundle: ConfigBundle,
    ) -> str:
        del best, history
        api_env = bundle.llm.api_env or "OPENAI_API_KEY"
        api_key = os.environ.get(api_env)
        if not api_key:
            raise LlmClientError(f"Environment variable {api_env} is not set")
        payload = {
            "model": bundle.llm.model,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": bundle.llm.json_schema_name,
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "mode": {"type": "string", "enum": ["coarse", "fine"]},
                            "next_candidate": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "Kp": {"type": "number"},
                                    "Ki": {"type": "number"},
                                    "Kd": {"type": "number"},
                                },
                                "required": ["Kp", "Ki", "Kd"],
                            },
                            "expectation": {"type": "string"},
                            "explanation": {"type": "string"},
                            "rationale": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "observed_issue": {"type": "string"},
                                    "parameter_actions": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "properties": {
                                            "Kp": {"type": "string", "enum": ["increase", "decrease", "keep"]},
                                            "Ki": {"type": "string", "enum": ["increase", "decrease", "keep"]},
                                            "Kd": {"type": "string", "enum": ["increase", "decrease", "keep"]},
                                        },
                                        "required": ["Kp", "Ki", "Kd"],
                                    },
                                    "expected_tradeoff": {"type": "string"},
                                    "risk": {"type": "string"},
                                },
                                "required": ["observed_issue", "parameter_actions", "expected_tradeoff", "risk"],
                            },
                        },
                        "required": ["mode", "next_candidate", "expectation", "explanation", "rationale"],
                    },
                }
            },
        }
        if bundle.llm.use_conversation_state:
            payload["conversation"] = self._ensure_conversation(bundle, api_key, system_prompt)
            payload["input"] = user_prompt
            self.latest_metadata = {
                "provider": bundle.llm.provider,
                "configured_use_conversation_state": True,
                "conversation_mode": "openai_conversation",
                "conversation_id": payload["conversation"],
                "model": bundle.llm.model,
            }
        else:
            payload["instructions"] = system_prompt
            payload["input"] = [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": user_prompt}],
                },
            ]
            self.latest_metadata = {
                "provider": bundle.llm.provider,
                "configured_use_conversation_state": False,
                "conversation_mode": "stateless",
                "model": bundle.llm.model,
            }
        parsed = self._request_json(bundle.llm.endpoint, api_key, payload)
        output = parsed.get("output")
        if not isinstance(output, list):
            raise LlmClientError("OpenAI response missing output")
        texts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    texts.append(part["text"])
        if not texts:
            raise LlmClientError("OpenAI response missing output text")
        return "\n".join(texts)

    def last_metadata(self) -> dict[str, Any]:
        return {} if self.latest_metadata is None else dict(self.latest_metadata)


def create_llm_client(bundle: ConfigBundle) -> BaseLlmClient | None:
    if bundle.llm.provider == "local_ovms":
        return LocalOvmsClient()
    if bundle.llm.provider == "openai_responses":
        return OpenAIResponsesClient()
    return None
