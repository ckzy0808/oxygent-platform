"""Provider protocol adapters used by the rule-based ModelRouter."""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Protocol

import httpx
from pydantic import Field

from oxygent.oxy.llms.openai_llm import OpenAILLM
from oxygent.schemas import EstimationMethod, OxyRequest, OxyState, TokenUsage
from oxygent.utils.token_utils import build_token_usage

from .common import PlatformModel
from .credentials import CredentialResolver
from .profiles import HealthStatus, ModelProfile, ProviderProfile, ProviderType


class ProviderCallError(RuntimeError):
    """Sanitized provider failure raised to ModelRouter."""


class ModelRequest(PlatformModel):
    provider: ProviderProfile
    model: ModelProfile
    messages: list[dict[str, Any]]
    parameters: dict[str, Any] = Field(default_factory=dict)
    transport_request: Any = Field(default=None, exclude=True, repr=False)


class ModelResponse(PlatformModel):
    output: str
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    token_count_method: EstimationMethod = EstimationMethod.EXACT
    latency_ms: float = Field(default=0.0, ge=0)


class ModelEvent(PlatformModel):
    type: str
    delta: str = ""
    response: ModelResponse | None = None


class HealthResult(PlatformModel):
    status: HealthStatus
    latency_ms: float = Field(default=0.0, ge=0)
    reason: str = ""


class ModelProviderAdapter(Protocol):
    async def complete(self, request: ModelRequest) -> ModelResponse: ...

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]: ...

    async def health_check(
        self, provider: ProviderProfile, model: ModelProfile
    ) -> HealthResult: ...


def _usage_counts(
    usage: TokenUsage | dict[str, Any] | None,
) -> tuple[int, int, EstimationMethod]:
    if isinstance(usage, TokenUsage):
        return usage.input_tokens, usage.output_tokens, usage.estimation_method
    if isinstance(usage, dict):
        method = usage.get("estimation_method", EstimationMethod.EXACT)
        return (
            int(usage.get("input_tokens", 0)),
            int(usage.get("output_tokens", 0)),
            EstimationMethod(method),
        )
    return 0, 0, EstimationMethod.APPROXIMATE


class BaseProviderAdapter:
    def __init__(self, credential_resolver: CredentialResolver) -> None:
        self.credential_resolver = credential_resolver

    def _credential(self, provider: ProviderProfile) -> str | None:
        return self.credential_resolver.resolve(provider.credential_reference)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        """Compatibility stream: adapters may later override with native deltas."""
        response = await self.complete(request)
        if response.output:
            yield ModelEvent(type="delta", delta=response.output)
        yield ModelEvent(type="done", response=response)


class OpenAICompatibleAdapter(BaseProviderAdapter):
    """Wrap the existing OpenAILLM implementation without URL inference."""

    async def complete(self, request: ModelRequest) -> ModelResponse:
        credential = self._credential(request.provider) or "EMPTY"
        llm = OpenAILLM(
            name=f"adapter_{request.provider.id}_{request.model.id}",
            base_url=request.provider.base_url,
            api_key=credential,
            model_name=request.model.model_name,
            timeout=request.provider.timeout,
            is_send_think=False,
        )
        arguments = {
            "messages": request.messages,
            **request.parameters,
            "stream": False,
        }
        if isinstance(request.transport_request, OxyRequest):
            oxy_request = request.transport_request.clone_with(arguments=arguments)
        else:
            oxy_request = OxyRequest(arguments=arguments, is_send_message=False)

        started = time.perf_counter()
        response = await asyncio.wait_for(
            llm._execute(oxy_request), timeout=request.provider.timeout
        )
        latency_ms = (time.perf_counter() - started) * 1000
        if response.state is not OxyState.COMPLETED:
            raise ProviderCallError(
                "OpenAI-compatible provider returned a failed state"
            )
        input_tokens, output_tokens, token_count_method = _usage_counts(
            response.extra.get("usage")
        )
        return ModelResponse(
            output=str(response.output),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            token_count_method=token_count_method,
            latency_ms=latency_ms,
        )

    async def health_check(
        self, provider: ProviderProfile, model: ModelProfile
    ) -> HealthResult:
        headers = {}
        credential = self._credential(provider)
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=provider.timeout) as client:
                response = await client.get(
                    f"{provider.base_url.rstrip('/')}/models", headers=headers
                )
            status = (
                HealthStatus.HEALTHY
                if response.is_success
                else HealthStatus.UNAVAILABLE
            )
            reason = f"HTTP {response.status_code}"
        except (httpx.HTTPError, TimeoutError) as exc:
            status = HealthStatus.UNAVAILABLE
            reason = type(exc).__name__
        return HealthResult(
            status=status,
            latency_ms=(time.perf_counter() - started) * 1000,
            reason=reason,
        )


class OpenAIResponsesAdapter(BaseProviderAdapter):
    """Explicit OpenAI Responses API adapter for ``input``-based gateways."""

    @staticmethod
    def _payload(request: ModelRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model.model_name,
            "input": request.messages,
        }
        parameter_map = {
            "temperature": "temperature",
            "top_p": "top_p",
            "max_tokens": "max_output_tokens",
            "max_output_tokens": "max_output_tokens",
        }
        for source, target in parameter_map.items():
            if source in request.parameters:
                payload[target] = request.parameters[source]
        return payload

    @staticmethod
    def _output_text(data: dict[str, Any]) -> str:
        values: list[str] = []
        for output in data.get("output", []):
            if not isinstance(output, dict):
                continue
            for content in output.get("content", []):
                if not isinstance(content, dict):
                    continue
                if content.get("type") in {"output_text", "text"}:
                    values.append(str(content.get("text", "")))
        return "".join(values)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        headers = {"Content-Type": "application/json"}
        credential = self._credential(request.provider)
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=request.provider.timeout) as client:
                response = await client.post(
                    request.provider.base_url,
                    headers=headers,
                    json=self._payload(request),
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderCallError(
                f"OpenAI Responses provider returned HTTP {exc.response.status_code}"
            ) from exc
        except (httpx.HTTPError, TimeoutError) as exc:
            raise ProviderCallError(
                f"OpenAI Responses provider transport failed: {type(exc).__name__}"
            ) from exc
        latency_ms = (time.perf_counter() - started) * 1000
        data = response.json()
        output = self._output_text(data)
        if not output:
            raise ProviderCallError("OpenAI Responses provider returned no output text")
        usage = build_token_usage(
            data.get("usage") or None,
            request.messages,
            output,
            request.model.model_name,
        )
        return ModelResponse(
            output=output,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            token_count_method=usage.estimation_method,
            latency_ms=latency_ms,
        )

    async def health_check(
        self, provider: ProviderProfile, model: ModelProfile
    ) -> HealthResult:
        started = time.perf_counter()
        try:
            await self.complete(
                ModelRequest(
                    provider=provider,
                    model=model,
                    messages=[{"role": "user", "content": "Reply OK."}],
                    parameters={"max_output_tokens": 4},
                )
            )
            status = HealthStatus.HEALTHY
            reason = "Responses API completed"
        except ProviderCallError as exc:
            status = HealthStatus.UNAVAILABLE
            reason = str(exc)
        return HealthResult(
            status=status,
            latency_ms=(time.perf_counter() - started) * 1000,
            reason=reason,
        )


class GeminiAdapter(BaseProviderAdapter):
    """Explicit Gemini generateContent adapter."""

    @staticmethod
    def _payload(request: ModelRequest) -> dict[str, Any]:
        contents = []
        system_parts = []
        for message in request.messages:
            content = message.get("content", "")
            if message.get("role") == "system":
                system_parts.append({"text": str(content)})
                continue
            role = "model" if message.get("role") == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": str(content)}]})

        params = dict(request.parameters)
        generation_config = {}
        parameter_map = {
            "temperature": "temperature",
            "top_p": "topP",
            "max_tokens": "maxOutputTokens",
        }
        for source, target in parameter_map.items():
            if source in params:
                generation_config[target] = params[source]

        payload: dict[str, Any] = {"contents": contents}
        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}
        if generation_config:
            payload["generationConfig"] = generation_config
        return payload

    async def complete(self, request: ModelRequest) -> ModelResponse:
        url = request.provider.base_url.rstrip("/")
        if not url.endswith(":generateContent"):
            url = f"{url}/models/{request.model.model_name}:generateContent"
        headers = {"Content-Type": "application/json"}
        credential = self._credential(request.provider)
        if credential:
            headers["X-goog-api-key"] = credential

        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=request.provider.timeout) as client:
            response = await client.post(
                url, headers=headers, json=self._payload(request)
            )
            response.raise_for_status()
        latency_ms = (time.perf_counter() - started) * 1000
        data = response.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        output = "".join(str(part.get("text", "")) for part in parts)
        usage = build_token_usage(
            data.get("usageMetadata") or None,
            request.messages,
            output,
            request.model.model_name,
        )
        return ModelResponse(
            output=output,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            token_count_method=usage.estimation_method,
            latency_ms=latency_ms,
        )

    async def health_check(
        self, provider: ProviderProfile, model: ModelProfile
    ) -> HealthResult:
        headers = {}
        credential = self._credential(provider)
        if credential:
            headers["X-goog-api-key"] = credential
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=provider.timeout) as client:
                response = await client.get(
                    f"{provider.base_url.rstrip('/')}/models", headers=headers
                )
            status = (
                HealthStatus.HEALTHY
                if response.is_success
                else HealthStatus.UNAVAILABLE
            )
            reason = f"HTTP {response.status_code}"
        except (httpx.HTTPError, TimeoutError) as exc:
            status = HealthStatus.UNAVAILABLE
            reason = type(exc).__name__
        return HealthResult(
            status=status,
            latency_ms=(time.perf_counter() - started) * 1000,
            reason=reason,
        )


class OllamaAdapter(BaseProviderAdapter):
    """Explicit Ollama /api/chat adapter."""

    async def complete(self, request: ModelRequest) -> ModelResponse:
        params = dict(request.parameters)
        options = {}
        if "temperature" in params:
            options["temperature"] = params["temperature"]
        if "top_p" in params:
            options["top_p"] = params["top_p"]
        if "max_tokens" in params:
            options["num_predict"] = params["max_tokens"]
        payload: dict[str, Any] = {
            "model": request.model.model_name,
            "messages": request.messages,
            "stream": False,
        }
        if options:
            payload["options"] = options

        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=request.provider.timeout) as client:
            response = await client.post(
                f"{request.provider.base_url.rstrip('/')}/api/chat", json=payload
            )
            response.raise_for_status()
        latency_ms = (time.perf_counter() - started) * 1000
        data = response.json()
        output = str(data.get("message", {}).get("content", ""))
        raw_usage = (
            {
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
            }
            if "prompt_eval_count" in data or "eval_count" in data
            else None
        )
        usage = build_token_usage(
            raw_usage, request.messages, output, request.model.model_name
        )
        return ModelResponse(
            output=output,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            token_count_method=usage.estimation_method,
            latency_ms=latency_ms,
        )

    async def health_check(
        self, provider: ProviderProfile, model: ModelProfile
    ) -> HealthResult:
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=provider.timeout) as client:
                response = await client.get(f"{provider.base_url.rstrip('/')}/api/tags")
            status = (
                HealthStatus.HEALTHY
                if response.is_success
                else HealthStatus.UNAVAILABLE
            )
            reason = f"HTTP {response.status_code}"
        except (httpx.HTTPError, TimeoutError) as exc:
            status = HealthStatus.UNAVAILABLE
            reason = type(exc).__name__
        return HealthResult(
            status=status,
            latency_ms=(time.perf_counter() - started) * 1000,
            reason=reason,
        )


class ProviderAdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[ProviderType, ModelProviderAdapter] = {}

    def register(
        self, provider_type: ProviderType, adapter: ModelProviderAdapter
    ) -> None:
        self._adapters[provider_type] = adapter

    def get(self, provider_type: ProviderType) -> ModelProviderAdapter:
        try:
            return self._adapters[provider_type]
        except KeyError as exc:
            raise ProviderCallError(
                f"no adapter registered for provider type {provider_type.value}"
            ) from exc


def default_provider_adapters(
    credential_resolver: CredentialResolver,
) -> ProviderAdapterRegistry:
    registry = ProviderAdapterRegistry()
    registry.register(
        ProviderType.OPENAI_COMPATIBLE,
        OpenAICompatibleAdapter(credential_resolver),
    )
    registry.register(
        ProviderType.OPENAI_RESPONSES,
        OpenAIResponsesAdapter(credential_resolver),
    )
    registry.register(ProviderType.GEMINI, GeminiAdapter(credential_resolver))
    registry.register(ProviderType.OLLAMA, OllamaAdapter(credential_resolver))
    return registry
