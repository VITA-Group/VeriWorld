"""Vision-language model (VLM) HTTP client.

Supports OpenAI-compatible ``/v1/chat/completions`` endpoints and the
Anthropic-native ``/v1/messages`` endpoint. Both are accessed through the
same :class:`VLMClient` interface — pass ``base_url`` containing
``anthropic`` to switch to the native protocol (automatic).

API keys are **never** hardcoded in this module. Configuration is read
from a JSON file at runtime (see :func:`load_configs`). An example file
ships as ``model_configs.example.json`` at the repo root.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Sequence, Union

import requests

log = logging.getLogger(__name__)


@dataclass
class ModelConfig:
    """One VLM endpoint.

    Attributes
    ----------
    name : str
        Human-readable label (used for run-dir names).
    model : str
        Provider model identifier (e.g. ``"gpt-4.1"``).
    api_key : str | list[str]
        Single key or a list that the client will rotate through on
        429 / 503 / 529 responses.
    base_url : str
        Endpoint base URL. If it contains ``"anthropic"`` the Anthropic
        native protocol is used; otherwise OpenAI-compatible.
    parallel : bool
        Whether this entry participates in ``run_parallel`` batches by
        default. Single-agent runs (``python -m <task>``) ignore this
        field. Defaults to ``True``.
    extra_params : dict
        Extra keyword arguments merged into the request payload
        (e.g. ``{"reasoning_effort": "minimal"}`` for GPT-5 / o-series).
        Task-level defaults (``model``, ``messages``, ``temperature``,
        ``max_tokens``) cannot be overridden by this dict.
    """

    name: str
    model: str
    api_key: Union[str, List[str]]
    base_url: str
    parallel: bool = True
    extra_params: dict = field(default_factory=dict)
    client_options: dict = field(default_factory=dict)


def load_configs(path: Union[str, Path]) -> List[ModelConfig]:
    """Load a JSON array of :class:`ModelConfig` entries from disk.

    Keys may be provided in three forms:

    * direct string: ``"api_key": "sk-..."``
    * env-var lookup: ``"api_key": "env:OPENAI_API_KEY"`` — resolved at load time
    * list of the above: ``"api_key": ["sk-...", "env:BACKUP_KEY"]`` — the
      :class:`VLMClient` will rotate through them on 429 / 503 / 529.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"{p} not found. Copy model_configs.example.json to {p.name} "
            "and fill in your API keys."
        )
    data = json.loads(p.read_text(encoding="utf-8"))

    def _resolve(value):
        if isinstance(value, str) and value.startswith("env:"):
            var = value[len("env:"):]
            if var not in os.environ:
                raise RuntimeError(
                    f"{p}: api_key references env var ${var} but it is not set."
                )
            return os.environ[var]
        return value

    out: List[ModelConfig] = []
    for entry in data:
        key = entry["api_key"]
        if isinstance(key, list):
            key = [_resolve(k) for k in key]
        else:
            key = _resolve(key)

        # Catch the "user forgot to edit the example" case with a clear error
        def _is_placeholder(k: object) -> bool:
            return isinstance(k, str) and ("PASTE_" in k or "PASTE_YOUR_" in k)

        bad = (_is_placeholder(key) if not isinstance(key, list)
               else any(_is_placeholder(k) for k in key))
        if bad:
            raise RuntimeError(
                f"{p}: entry {entry.get('name', '?')!r} still contains a PASTE_... "
                "placeholder. Edit the file and replace it with a real API key."
            )

        out.append(ModelConfig(
            name=entry["name"], model=entry["model"],
            api_key=key, base_url=entry["base_url"],
            parallel=bool(entry.get("parallel", True)),
            extra_params=dict(entry.get("extra_params", {})),
            client_options=dict(entry.get("client_options", {})),
        ))
    return out


class VLMClient:
    """Minimal HTTP client for chat / vision-chat models.

    Automatically rotates through a list of keys on rate-limit codes
    (429, 503, 529) and retries up to ``max_retries`` times on transient
    network errors.
    """

    def __init__(
        self,
        model: str,
        api_key: Union[str, Sequence[str]],
        base_url: str,
        *,
        timeout: float = 180.0,
        max_retries: int = 10,
        extra_params: Optional[dict] = None,
        request_options: Optional[dict] = None,
    ) -> None:
        self.model = model
        self.api_keys: List[str] = list(api_key) if isinstance(api_key, (list, tuple)) else [api_key]
        self._key_index = 0
        self.base_url = base_url.rstrip("/")
        self.is_anthropic = "anthropic" in base_url
        # Sentinel base_url that bypasses HTTP transport entirely and uses
        # the google-genai Python SDK directly (Files API for video upload).
        # Required because OpenAI-compatible aggregators don't yet have a
        # standard for video input — see ``chat_with_video``.
        self.is_gemini_direct = base_url.startswith("google-genai-direct")
        self.request_options = dict(request_options or {})
        supported = {"temperature", "omit_temperature", "token_parameter", "protocol", "expected_model", "request_model"}
        if set(self.request_options) - supported:
            raise ValueError("Unknown client request options: " + ", ".join(sorted(set(self.request_options) - supported)))
        protocol = self.request_options.get("protocol", "auto")
        if protocol not in {"auto", "openai", "anthropic", "gemini"}:
            raise ValueError("Unsupported API protocol")
        if protocol != "auto":
            self.is_anthropic = protocol == "anthropic"
            self.is_gemini_direct = protocol == "gemini"
        if self.request_options.get("token_parameter", "max_tokens") not in {"max_tokens", "max_completion_tokens"}:
            raise ValueError("Unsupported token parameter")
        if timeout <= 0 or max_retries < 1:
            raise ValueError("timeout and max_retries must be positive")
        self.timeout = timeout
        self.max_retries = max_retries
        self.extra_params: dict = dict(extra_params or {})
        self._reset_response_metadata()

    def _reset_response_metadata(self) -> None:
        """A failed call must not inherit the preceding call's diagnostics."""
        self.last_finish_reason = ""
        self.last_raw_finish_reason = ""
        self.last_returned_model = None
        self.last_usage = None
        self.last_part_count = 0
        self.last_response = None

    def _record_response_metadata(self, data: dict, *, protocol: str) -> None:
        """Retain provider evidence without changing the returned response text.

        The harnesses use MAX_TOKENS to identify incomplete responses. Keep
        the provider's original reason and response alongside that normalized
        flag. No authentication headers are included in these fields.
        """
        self.last_response = data
        self.last_returned_model = data.get("model") or data.get("model_version")
        self.last_usage = data.get("usage") or data.get("usage_metadata")
        if protocol == "openai":
            choices = data.get("choices") or []
            first = choices[0] if choices else {}
            reason = first.get("finish_reason")
            content = (first.get("message") or {}).get("content")
            self.last_part_count = len(content) if isinstance(content, list) else int(content is not None)
        elif protocol == "anthropic":
            reason = data.get("stop_reason")
            self.last_part_count = len(data.get("content") or [])
        else:
            candidates = data.get("candidates") or []
            first = candidates[0] if candidates else {}
            reason = first.get("finish_reason")
            self.last_part_count = len((first.get("content") or {}).get("parts") or [])
        reason = str(getattr(reason, "value", reason) or "")
        expected = self.request_options.get("expected_model")
        if expected is not None and self.last_returned_model != expected:
            raise ValueError("Returned model does not match configured expected_model")
        self.last_raw_finish_reason = reason
        self.last_finish_reason = "MAX_TOKENS" if reason.lower() in {
            "length", "max_tokens", "finishreason.max_tokens"
        } else reason

    def _record_gemini_metadata(self, response) -> None:
        self._record_response_metadata(response.model_dump(mode="json"), protocol="gemini")

    @classmethod
    def from_config(cls, cfg: ModelConfig, **kwargs: Any) -> "VLMClient":
        options = dict(cfg.client_options)
        defaults = {"timeout": options.pop("timeout", 180.0), "max_retries": options.pop("max_retries", 10)}
        defaults.update(kwargs)
        return cls(
            model=cfg.model, api_key=cfg.api_key, base_url=cfg.base_url,
            extra_params=cfg.extra_params, request_options=options, **defaults,
        )

    @property
    def api_key(self) -> str:
        return self.api_keys[self._key_index % len(self.api_keys)]

    def _rotate_key(self) -> None:
        self._key_index += 1

    def chat(self, messages: list, *, temperature: float = 0.5, max_tokens: int = 16384) -> str:
        """Run a chat completion. Returns the assistant's text content.

        Routes to the right backend based on ``base_url``:
        - ``is_gemini_direct``: google-genai SDK text-only call (same
          routing target as ``chat_with_video`` but with no file upload).
          Needed for e.g. the knowledge-accumulation harness's
          per-round summarizer call, which sees no video.
        - ``is_anthropic``: ``/v1/messages`` native protocol.
        - otherwise: OpenAI-compatible ``/chat/completions``.
        """
        temperature = self.request_options.get("temperature", temperature)
        last_err: Optional[BaseException] = None
        self._reset_response_metadata()
        for attempt in range(self.max_retries):
            self._reset_response_metadata()
            try:
                if self.is_gemini_direct:
                    return self._chat_gemini_direct(messages, temperature, max_tokens)
                if self.is_anthropic:
                    return self._chat_anthropic(messages, temperature, max_tokens)
                return self._chat_openai(messages, temperature, max_tokens)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last_err = e
                time.sleep(15 * (attempt + 1))
            except requests.exceptions.HTTPError as e:
                last_err = e
                status = e.response.status_code if e.response is not None else 0
                if status in (429, 503, 529):
                    self._rotate_key()
                    time.sleep(10 * (attempt + 1))
                else:
                    raise
            except Exception as e:  # noqa: BLE001
                # Catches google.genai.errors.ClientError / ServerError —
                # same classification as ``chat_with_video``.
                last_err = e
                if not self.is_gemini_direct:
                    raise
                code = getattr(e, "code", None)
                if isinstance(code, int) and code in (400, 401, 403, 404):
                    raise
                if isinstance(code, int) and code in (429, 500, 502, 503, 529):
                    self._rotate_key()
                time.sleep(10 * (attempt + 1))
        raise RuntimeError(f"VLM call failed after {self.max_retries} retries") from last_err

    def _chat_gemini_direct(self, messages: list, temperature: float, max_tokens: int) -> str:
        """``chat`` over google-genai SDK with image support.

        OpenAI-style ``messages`` are translated as follows:

        - ``role="system"`` content goes to ``system_instruction`` on the
          ``GenerateContentConfig``.
        - ``role="user"`` / ``role="assistant"`` content is flattened into
          a single user turn whose ``parts`` interleave text and image
          bytes. ``{"type": "image_url", "image_url": {"url": "data:..."}}``
          blocks are decoded into ``Part.from_bytes``; raw http(s) URLs
          fall back to ``Part.from_uri``.

        For video input use :meth:`chat_with_video`.
        """
        self._reset_response_metadata()
        import base64
        import re

        from google import genai  # local import — optional dep

        _DATA_URL_RE = re.compile(r"^data:(image/[^;]+);base64,(.+)$", re.DOTALL)

        system_parts: list[str] = []
        user_parts: list = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                if isinstance(content, list):
                    pieces = [b.get("text", "") for b in content
                              if isinstance(b, dict) and b.get("type") == "text"]
                    txt = "\n".join(p for p in pieces if p)
                else:
                    txt = str(content)
                if txt:
                    system_parts.append(txt)
                continue
            if isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    t = b.get("type")
                    if t == "text":
                        txt = b.get("text", "")
                        if txt:
                            user_parts.append(genai.types.Part.from_text(text=txt))
                    elif t == "image_url":
                        url = (b.get("image_url") or {}).get("url", "")
                        match = _DATA_URL_RE.match(url) if url else None
                        if match:
                            mime, b64 = match.group(1), match.group(2)
                            user_parts.append(genai.types.Part.from_bytes(
                                data=base64.b64decode(b64), mime_type=mime,
                            ))
                        elif url:
                            user_parts.append(genai.types.Part.from_uri(
                                file_uri=url, mime_type="image/png",
                            ))
            elif content:
                user_parts.append(genai.types.Part.from_text(text=str(content)))
        if not user_parts:
            user_parts = [genai.types.Part.from_text(text="")]

        client = genai.Client(api_key=self.api_key)
        config_kwargs: dict = {
            "temperature": temperature, "max_output_tokens": max_tokens,
            **{k: v for k, v in self.extra_params.items()
               if k not in ("temperature", "max_output_tokens")},
        }
        if system_parts:
            config_kwargs["system_instruction"] = "\n\n".join(system_parts)
        config = genai.types.GenerateContentConfig(**config_kwargs)
        resp = client.models.generate_content(
            model=self.model,
            contents=[genai.types.Content(role="user", parts=user_parts)],
            config=config,
        )
        self._record_gemini_metadata(resp)
        for cand in (resp.candidates or []):
            for part in (cand.content.parts or []):
                if getattr(part, "text", None):
                    return part.text
        return ""

    def _chat_openai(self, messages: list, temperature: float, max_tokens: int) -> str:
        self._reset_response_metadata()
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        # extra_params merged first so the canonical fields can't be overwritten.
        payload = {k: v for k, v in self.extra_params.items() if k not in ("temperature", "max_tokens", "max_completion_tokens")}
        payload.update(model=self.request_options.get("request_model", self.model), messages=messages)
        payload[self.request_options.get("token_parameter", "max_tokens")] = max_tokens
        if not self.request_options.get("omit_temperature", False):
            payload["temperature"] = temperature
        resp = requests.post(f"{self.base_url}/chat/completions", headers=headers, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        self._record_response_metadata(data, protocol="openai")
        return data["choices"][0]["message"]["content"]

    def chat_with_video(
        self, prompt: str, video_path: Union[str, Path],
        *, temperature: float = 0.5, max_tokens: int = 16384,
    ) -> str:
        """Single-turn vision call with a video attachment.

        Currently only implemented for ``is_gemini_direct`` clients —
        uses google-genai SDK's Files API (upload, poll until ACTIVE,
        reference by URI). Other transports raise NotImplementedError;
        OpenAI-compat aggregators don't yet have a standard video input
        block.

        Returns the assistant's text response (or raises on failure).
        """
        self._reset_response_metadata()
        if not self.is_gemini_direct:
            raise NotImplementedError(
                f"chat_with_video: model {self.model!r} via base_url "
                f"{self.base_url!r} doesn't support video. Use a "
                f"'google-genai-direct' base_url."
            )
        from google import genai  # local import — optional dep

        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(video_path)

        temperature = self.request_options.get("temperature", temperature)
        last_err: Optional[BaseException] = None
        for attempt in range(self.max_retries):
            self._reset_response_metadata()
            try:
                client = genai.Client(api_key=self.api_key)
                vf = client.files.upload(file=str(video_path))
                # Wait for Gemini to finish encoding/indexing the video
                # before referencing it — early references return 400.
                t0 = time.time()
                while vf.state.name == "PROCESSING":
                    if time.time() - t0 > self.timeout:
                        raise RuntimeError(
                            f"Gemini Files API: upload still PROCESSING "
                            f"after {self.timeout}s for {video_path.name}"
                        )
                    time.sleep(2)
                    vf = client.files.get(name=vf.name)
                if vf.state.name != "ACTIVE":
                    raise RuntimeError(
                        f"Gemini Files API: upload ended in state "
                        f"{vf.state.name} for {video_path.name}"
                    )
                config = genai.types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                    **{k: v for k, v in self.extra_params.items()
                       if k not in ("temperature", "max_output_tokens")},
                )
                resp = client.models.generate_content(
                    model=self.model,
                    contents=[genai.types.Content(parts=[
                        genai.types.Part.from_uri(file_uri=vf.uri,
                                                  mime_type="video/mp4"),
                        genai.types.Part.from_text(text=prompt),
                    ])],
                    config=config,
                )
                self._record_gemini_metadata(resp)
                # Extract first text part across all candidates.
                for cand in (resp.candidates or []):
                    for part in (cand.content.parts or []):
                        if getattr(part, "text", None):
                            return part.text
                return ""
            except Exception as e:  # noqa: BLE001
                last_err = e
                # Permanent client errors (bad/expired key, malformed
                # request, file not found) — retrying same payload won't
                # help. Bail immediately so the user sees a real error
                # instead of waiting through ``max_retries`` of backoff.
                code = getattr(e, "code", None)
                if isinstance(code, int) and code in (400, 401, 403, 404):
                    raise
                # 429 / 5xx — rotate key (multiple keys = pooled quota)
                # and back off exponentially.
                if isinstance(code, int) and code in (429, 500, 502, 503, 529):
                    self._rotate_key()
                time.sleep(10 * (attempt + 1))
        raise RuntimeError(
            f"Gemini chat_with_video failed after {self.max_retries} retries"
        ) from last_err

    def _chat_anthropic(self, messages: list, temperature: float, max_tokens: int) -> str:
        self._reset_response_metadata()
        system, msgs = _to_anthropic(messages)
        headers = {
            "x-api-key": self.api_key,
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        payload: dict = {**self.extra_params,
                         "model": self.request_options.get("request_model", self.model), "max_tokens": max_tokens,
                         "temperature": temperature, "messages": msgs}
        if self.request_options.get("omit_temperature", False):
            payload.pop("temperature", None)
        if system:
            payload["system"] = system
        resp = requests.post(f"{self.base_url}/messages", headers=headers, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        self._record_response_metadata(data, protocol="anthropic")
        return "".join(b["text"] for b in data["content"] if b["type"] == "text")


def _to_anthropic(messages: list) -> tuple[str, list]:
    """Convert OpenAI-style messages (with ``image_url`` blocks) to
    Anthropic's native message format (``image`` blocks with base64 source).
    """
    system = ""
    out: list = []
    for m in messages:
        if m["role"] == "system":
            system = m["content"] if isinstance(m["content"], str) else ""
            continue
        content = m["content"]
        if isinstance(content, list):
            new_content: list = []
            for block in content:
                if block.get("type") == "image_url":
                    url = block["image_url"]["url"]
                    prefix = "data:image/png;base64,"
                    if url.startswith(prefix):
                        new_content.append({
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": url[len(prefix):]},
                        })
                elif block.get("type") == "text":
                    new_content.append({"type": "text", "text": block["text"]})
            content = new_content
        out.append({"role": m["role"], "content": content})
    return system, out


__all__ = ["ModelConfig", "VLMClient", "load_configs"]
