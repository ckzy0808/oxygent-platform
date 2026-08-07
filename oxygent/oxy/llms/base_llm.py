"""Base LLM module for Large Language Model implementations.

This module provides the abstract base class for all LLM implementations in the OxyGent
system. It handles multimodal input processing, think message extraction, and provides a
consistent interface for different LLM providers.
"""

import copy
import inspect
import json
import logging
import os
from typing import Any, Optional

import aiofiles
from pydantic import Field

from ...config import Config
from ...schemas import OxyRequest, OxyResponse, TokenUsage
from ...utils.common_utils import (
    extract_first_json,
    image_to_base64,
    parse_mixed_string,
    video_to_base64,
)
from ...utils.token_utils import aggregate_token_usage, build_token_usage
from ..base_oxy import Oxy

logger = logging.getLogger(__name__)


class BaseLLM(Oxy):
    """Base class for Large Language Model implementations.

    This class provides common functionality for all LLM implementations including:
    - Multimodal input processing (images, videos)
    - Think message extraction and forwarding
    - Base64 conversion for media URLs
    - Error handling with user-friendly messages

    Attributes:
        category: The category type, always "llm" for LLM implementations.
        timeout: Maximum execution time in seconds.
        llm_params: Additional parameters specific to the LLM implementation.
        is_send_think: Whether to send think messages to the frontend.
        friendly_error_text: User-friendly error message for exceptions.
        is_convert_url_to_base64: Whether to convert media URLs to base64.
        max_image_pixels: Maximum pixel count for image processing.
        max_video_size: Maximum size in bytes for video processing.
    """

    category: str = Field(
        "llm", description="Category identifier, always 'llm' for LLM implementations"
    )
    semaphore: int = Field(
        default_factory=Config.get_llm_semaphore, description="Concurrency limit"
    )
    timeout: float = Field(
        default_factory=Config.get_llm_timeout, description="Timeout in seconds."
    )

    llm_params: dict[str, Any] = Field(
        default_factory=dict, description="Additional provider-specific LLM parameters"
    )
    is_send_think: bool = Field(
        default_factory=Config.get_message_is_send_think,
        description="Whether to send think messages to the frontend.",
    )
    friendly_error_text: Optional[str] = Field(
        default="Sorry, I seem to have encountered a problem. Please try again.",
        description="User-friendly error message displayed when exceptions occur.",
    )

    is_multimodal_supported: bool = Field(
        False, description="whether to support multimodal input"
    )

    is_convert_url_to_base64: bool = Field(
        default=False,
        description="Whether to convert image or video URLs to base64 format.",
    )
    max_image_pixels: int = Field(
        default=10000000, description="Maximum pixel count allowed per image."
    )
    max_video_size: int = Field(
        default=12 * 1024 * 1024,
        description="Maximum video file size in bytes (default: 12MB).",
    )
    max_file_size_bytes: int = Field(
        default=2 * 1024 * 1024,
        description="Maximum non-media file size (bytes) for base64 embedding.",
    )
    base64_image_prefix: str = Field(
        default="data:image",
        description="Prefix used to detect base64-encoded image data URIs",
    )
    base64_video_prefix: str = Field(
        default="data:video",
        description="Prefix used to detect base64-encoded video data URIs",
    )
    is_disable_system_prompt: bool = Field(
        default=False, description="Whether to omit the system prompt from the LLM call"
    )

    async def _get_messages(self, oxy_request: OxyRequest) -> list[dict[str, Any]]:
        """Build the message list for the LLM call.

        Merges the system prompt into the first user message when system prompts
        are disabled, parses multimodal content (text, image, video, file), and
        optionally converts media URLs to base64-encoded data URIs.

        Args:
            oxy_request: The incoming request whose ``arguments["messages"]``
                list will be processed.

        Returns:
            A list of message dicts ready to be sent to the LLM provider.
        """
        # merge system prompt
        if (
            self.is_disable_system_prompt
            and oxy_request.arguments["messages"][0].get("role") == "system"
        ):
            oxy_request.arguments["messages"][1]["content"] = (
                oxy_request.arguments["messages"][0]["content"]
                + "\nUser Input: "
                + oxy_request.arguments["messages"][1]["content"]
            )
            oxy_request.arguments["messages"] = oxy_request.arguments["messages"][1:]

        # Preprocess messages for multimodal input
        if not self.is_multimodal_supported:
            return oxy_request.arguments["messages"]

        messages_processed = copy.deepcopy(oxy_request.arguments["messages"])
        messages_temp = []
        for message in messages_processed:
            role, content = message["role"], message["content"]
            if role == "user":
                # Skip processing if not a str type
                if not isinstance(content, str):
                    messages_temp.append(message)
                    continue
                # Parse text
                items = parse_mixed_string(content)
                # Read file and replace with its text content
                index_of_doc_url = [
                    i for i, item in enumerate(items) if item["type"] == "doc_url"
                ]
                for i in index_of_doc_url:
                    item = items[i]
                    item_link = item["link"]
                    if os.path.exists(item_link):
                        async with aiofiles.open(item_link, "r") as f:
                            doc_content = await f.read()
                            items[i] = {
                                "type": "text",
                                "content": f"The content of the `{item['desc']}` is: {doc_content} --- ",
                            }
                    else:
                        items[i] = {"type": "text", "content": item["content"]}

                # Check whether the message is plain text
                is_pure_text = all([item["type"] == "text" for item in items])
                # Concatenate directly
                if is_pure_text:
                    content = "".join([item["content"] for item in items])
                else:
                    content = []
                    for item in items:
                        item_type = item["type"]
                        if item_type == "text":
                            content.append(
                                {"type": item_type, item_type: item["content"]}
                            )
                        elif item_type in ["image_url", "video_url"]:
                            content.append(
                                {
                                    "type": "text",
                                    "text": f"The content of the `{item['desc']}` is: ",
                                }
                            )
                            content.append(
                                {"type": item_type, item_type: {"url": item["link"]}}
                            )
                        else:
                            pass
            messages_temp.append({"role": role, "content": content})
        messages_processed = messages_temp

        # Keep URL as-is when base64 conversion is disabled
        if not self.is_convert_url_to_base64:
            return messages_processed

        # convert url into base_64 data
        for message in messages_processed:
            if not isinstance(message.get("content"), list):
                continue

            for item in message["content"]:
                item_type = item.get("type")
                if item_type == "text":
                    continue

                if item_type == "image_url":
                    item[item_type]["url"] = await image_to_base64(
                        item[item_type]["url"],
                        self.max_image_pixels,
                        self.base64_image_prefix,
                    )
                elif item_type == "video_url":
                    item[item_type]["url"] = await video_to_base64(
                        item[item_type]["url"],
                        self.max_video_size,
                        self.base64_video_prefix,
                    )
                else:
                    logger.warning(
                        f"Unexpected content type: {item_type}",
                        extra={
                            "trace_id": oxy_request.current_trace_id,
                            "node_id": oxy_request.node_id,
                        },
                    )

        return messages_processed

    async def _execute(self, oxy_request: OxyRequest) -> OxyResponse:
        """Execute the LLM request.

        Subclasses must override this method with provider-specific logic.

        Args:
            oxy_request: The request containing messages and LLM parameters.

        Returns:
            An OxyResponse with the model's generated text.

        Raises:
            NotImplementedError: Always, unless overridden by a subclass.
        """
        raise NotImplementedError("This method is not yet implemented")

    async def _post_send_message(self, oxy_response: OxyResponse) -> None:
        """Send think messages to the frontend after response generation.

        Extracts and forwards thinking process messages to the frontend if
        is_send_think is enabled. Supports both XML-style <think> tags and
        JSON-based think messages.

        Args:
            oxy_response: The response object containing the LLM output.
        """
        await super()._post_send_message(oxy_response)
        # Send thinking process to frontend
        oxy_request = oxy_response.oxy_request
        if self.is_send_think:
            try:
                msg = ""
                if "</think>" in oxy_response.output:
                    msg = (
                        oxy_response.output.split("</think>")[0]
                        .replace("<think>", "")
                        .strip()
                    )
                else:
                    tool_call_dict = json.loads(extract_first_json(oxy_response.output))
                    if "think" in tool_call_dict:
                        msg = tool_call_dict["think"].strip()
                await oxy_request.send_message(
                    {"type": "think", "content": msg, "agent": oxy_request.caller}
                )
            except json.JSONDecodeError:
                pass
            except Exception as e:
                logger.error(
                    f"Unexpected error extracting think message from LLM output (model={self.name}, caller={oxy_request.caller}): {e}",
                    extra={
                        "trace_id": oxy_request.current_trace_id,
                        "node_id": oxy_request.node_id,
                    },
                    exc_info=True,
                )

    async def _after_execute(self, oxy_response: OxyResponse) -> OxyResponse:
        """Post-execution hook: aggregate token usage then serialize to dict.

        Args:
            oxy_response: The response returned by ``_execute``.

        Returns:
            The same OxyResponse with ``extra["usage"]`` converted to a dict.
        """
        usage = oxy_response.extra.get("usage")
        if isinstance(usage, TokenUsage):
            aggregate_token_usage(oxy_response.oxy_request, usage)
            observer = getattr(
                oxy_response.oxy_request.mas, "func_record_model_usage", None
            )
            if observer is not None:
                try:
                    observed = observer(self, oxy_response.oxy_request, usage)
                    if inspect.isawaitable(observed):
                        await observed
                except Exception as exc:
                    logger.warning(
                        "model usage observer failed; model=%s error_type=%s",
                        self.name,
                        type(exc).__name__,
                        extra={
                            "trace_id": oxy_response.oxy_request.current_trace_id,
                            "node_id": oxy_response.oxy_request.node_id,
                        },
                    )
            oxy_response.extra["usage"] = usage.model_dump()
        return oxy_response

    def _build_payload(self, oxy_request: OxyRequest, payload: dict) -> dict:
        """Merge global LLM config, instance params, and request args into payload.

        Priority (last wins): global config < self.llm_params < request arguments.
        The ``messages`` key in request arguments is always skipped.

        Args:
            oxy_request: The current request whose arguments are merged.
            payload: The mutable dict to update in place.

        Returns:
            The same ``payload`` dict after all merges have been applied.
        """
        payload.update(Config.get_llm_config(exclude=["semaphore", "timeout"]))
        payload.update(self.llm_params)
        for k, v in oxy_request.arguments.items():
            if k != "messages":
                payload[k] = v
        return payload

    def _build_token_usage(
        self, usage_data: Any, messages: list[dict[str, Any]], output: str
    ) -> TokenUsage:
        """Build a TokenUsage object, falling back to estimation when the
        provider does not return usage data.

        Args:
            usage_data: Raw usage dict/object returned by the provider, or None.
            messages: The messages sent to the model (used for estimation).
            output: The generated text (used for estimation).

        Returns:
            A populated TokenUsage instance.
        """
        return build_token_usage(usage_data, messages, output, self.model_name)
