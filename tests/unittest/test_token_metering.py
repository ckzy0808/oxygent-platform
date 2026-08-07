"""Test token metering functionality."""

from unittest.mock import MagicMock
from types import SimpleNamespace

import pytest

from oxygent.config import Config
from oxygent.schemas import EstimationMethod, OxyResponse, OxyState, TokenUsage
from oxygent.utils.token_utils import (
    TokenEstimator,
    _to_dict,
    aggregate_token_usage,
    build_token_usage,
)

# ---------------------------------------------------------------------------
# TokenUsage schema
# ---------------------------------------------------------------------------


class TestTokenUsage:
    def test_total_tokens_is_computed(self):
        usage = TokenUsage(input_tokens=100, output_tokens=50, model_name="gpt-4o")
        assert usage.total_tokens == 150

    def test_model_dump_enum_values(self):
        usage = TokenUsage(
            input_tokens=10,
            output_tokens=5,
            estimation_method=EstimationMethod.TIKTOKEN,
        )
        data = usage.model_dump()
        assert data["estimation_method"] == "tiktoken"
        assert data["total_tokens"] == 15


# ---------------------------------------------------------------------------
# _to_dict
# ---------------------------------------------------------------------------


class TestToDict:
    def test_dict_passthrough(self):
        d = {"a": 1}
        assert _to_dict(d) is d

    def test_pydantic_model(self):
        usage = TokenUsage(input_tokens=5)
        result = _to_dict(usage)
        assert isinstance(result, dict)
        assert result["input_tokens"] == 5

    def test_plain_object(self):
        class Obj:
            prompt_tokens = 80
            completion_tokens = 40

        result = _to_dict(Obj())
        assert result["prompt_tokens"] == 80
        assert result["completion_tokens"] == 40

    def test_to_dict_method(self):
        class Obj:
            def to_dict(self):
                return {"x": 1}

        assert _to_dict(Obj()) == {"x": 1}


# ---------------------------------------------------------------------------
# TokenEstimator
# ---------------------------------------------------------------------------


class TestTokenEstimator:
    def test_count_tokens_empty(self):
        assert TokenEstimator.count_tokens("", "gpt-4") == 0

    def test_count_tokens_nonempty(self):
        assert TokenEstimator.count_tokens("Hello, world!", "gpt-4") > 0

    def test_estimate_usage(self):
        messages = [{"role": "user", "content": "Hello"}]
        usage = TokenEstimator.estimate_usage(messages, "Hello world!", "gpt-4o")
        assert usage.input_tokens > 0
        assert usage.output_tokens > 0
        assert usage.total_tokens == usage.input_tokens + usage.output_tokens


# ---------------------------------------------------------------------------
# build_token_usage — real provider formats
# ---------------------------------------------------------------------------


class TestBuildTokenUsage:
    """Test build_token_usage with real API response formats."""

    def test_openai_format(self):
        """GPT: nested details."""
        usage = build_token_usage(
            {
                "prompt_tokens": 401,
                "completion_tokens": 10,
                "total_tokens": 411,
                "prompt_tokens_details": {"cached_tokens": 0, "audio_tokens": 0},
                "completion_tokens_details": {
                    "reasoning_tokens": 0,
                    "audio_tokens": 0,
                },
            },
            [],
            "",
            "gpt-4o",
        )
        assert usage.input_tokens == 401
        assert usage.output_tokens == 10
        assert usage.estimation_method == EstimationMethod.EXACT

    def test_deepseek_format(self):
        """DeepSeek: top-level reasoning_tokens, prompt_tokens_details is None."""
        usage = build_token_usage(
            {
                "prompt_tokens": 405,
                "total_tokens": 415,
                "completion_tokens": 10,
                "prompt_tokens_details": None,
                "reasoning_tokens": 42,
            },
            [],
            "",
            "deepseek-r1",
        )
        assert usage.input_tokens == 405
        assert usage.output_tokens == 10
        assert usage.reasoning_tokens == 42

    def test_glm_format(self):
        """GLM: same as DeepSeek layout."""
        usage = build_token_usage(
            {
                "prompt_tokens": 400,
                "total_tokens": 433,
                "completion_tokens": 33,
                "prompt_tokens_details": None,
                "reasoning_tokens": 0,
            },
            [],
            "",
            "glm-4",
        )
        assert usage.input_tokens == 400
        assert usage.output_tokens == 33
        assert usage.reasoning_tokens == 0

    def test_gemini_openai_compat_format(self):
        """Gemini via OpenAI-compat: top-level thoughts_tokens."""
        usage = build_token_usage(
            {
                "prompt_tokens": 436,
                "completion_tokens": 9,
                "thoughts_tokens": 26,
                "total_tokens": 471,
            },
            [],
            "",
            "gemini-2.5-pro",
        )
        assert usage.input_tokens == 436
        assert usage.output_tokens == 9
        assert usage.reasoning_tokens == 26

    def test_gemini_total_tokens_includes_thinking(self):
        """Gemini total_tokens includes thoughts_tokens (differs from input+output)."""
        usage = build_token_usage(
            {
                "prompt_tokens": 7,
                "completion_tokens": 10,
                "thoughts_tokens": 21,
                "total_tokens": 38,
            },
            [],
            "",
            "gemini-2.5-flash",
        )
        assert usage.input_tokens == 7
        assert usage.output_tokens == 10
        assert usage.reasoning_tokens == 21
        assert usage.total_tokens == 38  # API total includes thinking

    def test_gemini_native_format(self):
        """Gemini native: camelCase fields from usageMetadata."""
        usage = build_token_usage(
            {
                "promptTokenCount": 200,
                "candidatesTokenCount": 15,
                "thoughtsTokenCount": 30,
            },
            [],
            "",
            "gemini-2.5-flash",
        )
        assert usage.input_tokens == 200
        assert usage.output_tokens == 15
        assert usage.reasoning_tokens == 30

    def test_doubao_format(self):
        """Doubao: nested details like OpenAI."""
        usage = build_token_usage(
            {
                "completion_tokens": 27,
                "prompt_tokens": 460,
                "total_tokens": 487,
                "prompt_tokens_details": {"cached_tokens": 0},
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
            [],
            "",
            "doubao-pro",
        )
        assert usage.input_tokens == 460
        assert usage.output_tokens == 27

    def test_cached_tokens(self):
        """Extract cached_input_tokens from prompt_tokens_details."""
        usage = build_token_usage(
            {
                "prompt_tokens": 500,
                "completion_tokens": 50,
                "prompt_tokens_details": {"cached_tokens": 300},
            },
            [],
            "",
            "gpt-4o",
        )
        assert usage.cached_input_tokens == 300
        assert usage.cache_creation_input_tokens == 0

    def test_anthropic_format(self):
        """Anthropic: top-level cache_read/cache_creation, no prompt_tokens_details."""
        usage = build_token_usage(
            {
                "input_tokens": 50,
                "output_tokens": 100,
                "cache_read_input_tokens": 100000,
                "cache_creation_input_tokens": 248,
            },
            [],
            "",
            "claude-sonnet-4-20250514",
        )
        assert usage.input_tokens == 50
        assert usage.output_tokens == 100
        assert usage.cached_input_tokens == 100000
        assert usage.cache_creation_input_tokens == 248

    def test_deepseek_cache_format(self):
        """DeepSeek: top-level prompt_cache_hit_tokens."""
        usage = build_token_usage(
            {
                "prompt_tokens": 405,
                "total_tokens": 415,
                "completion_tokens": 10,
                "prompt_tokens_details": None,
                "prompt_cache_hit_tokens": 380,
                "reasoning_tokens": 42,
            },
            [],
            "",
            "deepseek-r1",
        )
        assert usage.cached_input_tokens == 380
        assert usage.cache_creation_input_tokens == 0

    def test_gemini_native_cache_format(self):
        """Gemini native: cachedContentTokenCount。"""
        usage = build_token_usage(
            {
                "promptTokenCount": 200,
                "candidatesTokenCount": 15,
                "thoughtsTokenCount": 30,
                "cachedContentTokenCount": 150,
            },
            [],
            "",
            "gemini-2.5-flash",
        )
        assert usage.cached_input_tokens == 150

    def test_fallback_to_estimator(self):
        usage = build_token_usage(
            None, [{"role": "user", "content": "Hello"}], "Hi there!", "gpt-4o"
        )
        assert usage.input_tokens > 0
        assert usage.estimation_method != EstimationMethod.EXACT

    def test_reasoning_priority_nested_over_toplevel(self):
        """Nested completion_tokens_details.reasoning_tokens takes priority."""
        usage = build_token_usage(
            {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "reasoning_tokens": 999,
                "completion_tokens_details": {"reasoning_tokens": 30},
            },
            [],
            "",
            "test",
        )
        assert usage.reasoning_tokens == 30

    def test_from_sdk_object(self):
        """OpenAI SDK returns objects with attributes, not dicts."""

        class UsageObj:
            prompt_tokens = 80
            completion_tokens = 40
            prompt_tokens_details = None
            completion_tokens_details = None

        usage = build_token_usage(UsageObj(), [], "", "gpt-4o")
        assert usage.input_tokens == 80
        assert usage.output_tokens == 40


# ---------------------------------------------------------------------------
# aggregate_token_usage
# ---------------------------------------------------------------------------


class TestTokenAggregation:
    def test_single_call(self):
        req = MagicMock(shared_data={})
        aggregate_token_usage(
            req, TokenUsage(input_tokens=100, output_tokens=50, model_name="gpt-4o")
        )
        m = req.shared_data["_metrics"]["token_usage"]
        assert m["total_tokens"] == 150
        assert m["request_count"] == 1

    def test_multi_model_accumulation(self):
        req = MagicMock(shared_data={})
        aggregate_token_usage(
            req, TokenUsage(input_tokens=100, output_tokens=50, model_name="gpt-4o")
        )
        aggregate_token_usage(
            req,
            TokenUsage(input_tokens=50, output_tokens=25, model_name="gpt-4o-mini"),
        )
        m = req.shared_data["_metrics"]["token_usage"]
        assert m["total_tokens"] == 225
        assert m["request_count"] == 2
        assert m["by_model"]["gpt-4o"]["total_tokens"] == 150
        assert m["by_model"]["gpt-4o-mini"]["total_tokens"] == 75

    def test_cached_and_reasoning_accumulation(self):
        req = MagicMock(shared_data={})
        u1 = build_token_usage(
            {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "prompt_tokens_details": {"cached_tokens": 60},
                "cache_creation_input_tokens": 10,
                "reasoning_tokens": 20,
            },
            [],
            "",
            "deepseek-r1",
        )
        u2 = build_token_usage(
            {
                "prompt_tokens": 80,
                "completion_tokens": 30,
                "prompt_tokens_details": {"cached_tokens": 40},
                "cache_creation_input_tokens": 5,
                "reasoning_tokens": 10,
            },
            [],
            "",
            "deepseek-r1",
        )
        aggregate_token_usage(req, u1)
        aggregate_token_usage(req, u2)
        m = req.shared_data["_metrics"]["token_usage"]
        assert m["cached_input_tokens"] == 100
        assert m["cache_creation_input_tokens"] == 15
        assert m["reasoning_tokens"] == 30
        assert m["by_model"]["deepseek-r1"]["cached_input_tokens"] == 100
        assert m["by_model"]["deepseek-r1"]["cache_creation_input_tokens"] == 15
        assert m["by_model"]["deepseek-r1"]["reasoning_tokens"] == 30

    def test_disabled(self):
        original = Config.get_token_tracking_enabled()
        try:
            Config.set_token_tracking_enabled(False)
            req = MagicMock(shared_data={})
            aggregate_token_usage(req, TokenUsage(input_tokens=100, output_tokens=50))
            assert "_metrics" not in req.shared_data
        finally:
            Config.set_token_tracking_enabled(original)


# ---------------------------------------------------------------------------
# _after_execute hook
# ---------------------------------------------------------------------------


class TestAfterExecute:
    @pytest.mark.asyncio
    async def test_aggregates_token_usage(self):
        from oxygent.oxy.llms.base_llm import BaseLLM

        req = MagicMock(shared_data={})
        token_usage = TokenUsage(input_tokens=10, output_tokens=5, model_name="gpt-4o")
        resp = OxyResponse(
            state=OxyState.COMPLETED,
            output="hello",
            extra={"usage": token_usage},
        )
        resp.oxy_request = req

        llm = BaseLLM.__new__(BaseLLM)
        await llm._after_execute(resp)

        # TokenUsage should be converted to dict after aggregation
        assert isinstance(resp.extra["usage"], dict)
        assert resp.extra["usage"]["total_tokens"] == 15
        assert req.shared_data["_metrics"]["token_usage"]["total_tokens"] == 15

    @pytest.mark.asyncio
    async def test_notifies_per_call_usage_observer_before_serialization(self):
        from oxygent.oxy.llms.base_llm import BaseLLM

        observed = []

        async def observer(llm, request, usage):
            observed.append((llm, request, usage))

        req = MagicMock(shared_data={})
        req.mas = SimpleNamespace(func_record_model_usage=observer)
        token_usage = TokenUsage(
            input_tokens=23,
            output_tokens=7,
            model_name="gpt-observed",
            estimation_method=EstimationMethod.EXACT,
        )
        resp = OxyResponse(
            state=OxyState.COMPLETED,
            output="hello",
            extra={"usage": token_usage},
        )
        resp.oxy_request = req
        llm = BaseLLM.__new__(BaseLLM)

        await llm._after_execute(resp)

        assert observed == [(llm, req, token_usage)]
        assert resp.extra["usage"]["estimation_method"] == "exact"


# ---------------------------------------------------------------------------
# model_dump includes new cache fields
# ---------------------------------------------------------------------------


class TestModelDump:
    def test_includes_cached_input_tokens(self):
        usage = TokenUsage(
            input_tokens=100,
            output_tokens=50,
            cached_input_tokens=80,
            cache_creation_input_tokens=10,
            model_name="gpt-4o",
        )
        data = usage.model_dump()
        assert data["cached_input_tokens"] == 80
        assert data["cache_creation_input_tokens"] == 10

    def test_defaults_zero(self):
        usage = TokenUsage(input_tokens=100, output_tokens=50)
        data = usage.model_dump()
        assert data["cached_input_tokens"] == 0
        assert data["cache_creation_input_tokens"] == 0
