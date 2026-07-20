"""Protocol-level tests for the phase-one Provider adapters."""

import httpx
import pytest
import respx

from oxygent.platform import (
    GeminiAdapter,
    MappingCredentialResolver,
    ModelProfile,
    ModelRequest,
    OllamaAdapter,
    OpenAICompatibleAdapter,
    ProviderProfile,
)


def request_for(provider_type: str, base_url: str, model_name: str) -> ModelRequest:
    provider = ProviderProfile(
        id=f"{provider_type}_provider",
        name=provider_type,
        providerType=provider_type,
        baseUrl=base_url,
        credentialReference="secret-ref",
        healthStatus="healthy",
    )
    model = ModelProfile(
        id=f"{provider_type}_model",
        providerId=provider.id,
        modelName=model_name,
        displayName=model_name,
        capabilities={"text"},
        healthStatus="healthy",
    )
    return ModelRequest(
        provider=provider,
        model=model,
        messages=[
            {"role": "system", "content": "System instruction"},
            {"role": "user", "content": "Hello"},
        ],
        parameters={"temperature": 0.2, "max_tokens": 64},
    )


@pytest.mark.asyncio
@respx.mock
async def test_openai_compatible_adapter_reuses_existing_llm():
    route = respx.post("https://openai-compatible.invalid/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "completion-1",
                "object": "chat.completion",
                "created": 1,
                "model": "compatible-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "done"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 2,
                    "total_tokens": 6,
                },
            },
        )
    )
    adapter = OpenAICompatibleAdapter(
        MappingCredentialResolver({"secret-ref": "openai-test-secret"})
    )

    response = await adapter.complete(
        request_for(
            "openai-compatible",
            "https://openai-compatible.invalid/v1",
            "compatible-model",
        )
    )

    assert route.called
    assert response.output == "done"
    assert response.input_tokens == 4
    assert response.output_tokens == 2


@pytest.mark.asyncio
@respx.mock
async def test_gemini_adapter_uses_generate_content_protocol():
    route = respx.post(
        "https://generativelanguage.invalid/v1beta/models/gemini-test:generateContent"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": "gemini response"}]}}],
                "usageMetadata": {
                    "promptTokenCount": 5,
                    "candidatesTokenCount": 3,
                },
            },
        )
    )
    adapter = GeminiAdapter(
        MappingCredentialResolver({"secret-ref": "gemini-test-secret"})
    )

    response = await adapter.complete(
        request_for(
            "gemini",
            "https://generativelanguage.invalid/v1beta",
            "gemini-test",
        )
    )

    payload = route.calls[0].request.content.decode()
    assert route.called
    assert '"systemInstruction"' in payload
    assert '"maxOutputTokens":64' in payload
    assert response.output == "gemini response"
    assert response.input_tokens == 5


@pytest.mark.asyncio
@respx.mock
async def test_ollama_adapter_uses_chat_protocol():
    route = respx.post("http://ollama.invalid/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": "ollama response"},
                "prompt_eval_count": 6,
                "eval_count": 4,
            },
        )
    )
    adapter = OllamaAdapter(MappingCredentialResolver({}))

    response = await adapter.complete(
        request_for("ollama", "http://ollama.invalid", "qwen-test")
    )

    payload = route.calls[0].request.content.decode()
    assert route.called
    assert '"stream":false' in payload
    assert '"num_predict":64' in payload
    assert response.output == "ollama response"
    assert response.output_tokens == 4
