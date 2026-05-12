"""Tool normalization layer for multi-provider LLM support.

Converts between Maigie's internal tool format (ToolDefinition, ToolCallRequest)
and provider-specific formats for Gemini, OpenAI, and Anthropic. Also handles
relaxed JSON parsing for malformed tool call arguments.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from src.services.llm.types import ToolCallRequest, ToolDefinition

logger = logging.getLogger(__name__)


def _relaxed_json_parse(raw: str) -> dict[str, Any]:
    """Attempt to parse a JSON string with relaxed rules.

    Handles common issues from LLM-generated JSON:
    - Trailing commas before closing braces/brackets
    - Single quotes instead of double quotes
    - Unquoted keys
    - Trailing text after valid JSON

    Raises:
        ValueError: If parsing fails even after relaxed attempts.
    """
    # First, try strict JSON parsing
    try:
        result = json.loads(raw)
        if isinstance(result, dict):
            return result
        return {"_value": result}
    except (json.JSONDecodeError, TypeError):
        pass

    if not raw or not raw.strip():
        return {}

    text = raw.strip()

    # Remove trailing commas before } or ]
    text = re.sub(r",\s*([}\]])", r"\1", text)

    # Try again after removing trailing commas
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
        return {"_value": result}
    except (json.JSONDecodeError, TypeError):
        pass

    # Replace single quotes with double quotes (naive approach for simple cases)
    # Only do this if there are no double quotes already in the string
    if "'" in text and '"' not in text:
        text_dq = text.replace("'", '"')
        try:
            result = json.loads(text_dq)
            if isinstance(result, dict):
                return result
            return {"_value": result}
        except (json.JSONDecodeError, TypeError):
            pass

    # Try to fix unquoted keys: word: -> "word":
    text_fixed = re.sub(r"(?<=[{,])\s*(\w+)\s*:", r' "\1":', text)
    # Also fix trailing commas in the fixed version
    text_fixed = re.sub(r",\s*([}\]])", r"\1", text_fixed)
    try:
        result = json.loads(text_fixed)
        if isinstance(result, dict):
            return result
        return {"_value": result}
    except (json.JSONDecodeError, TypeError):
        pass

    raise ValueError(
        f"Failed to parse tool call arguments as JSON (even with relaxed parsing): {raw!r}"
    )


class ToolNormalizer:
    """Converts between Maigie internal tool format and provider-specific formats.

    Provides three categories of methods:
    1. to_<provider>: Convert internal ToolDefinition list to provider format
    2. normalize_tool_calls_<provider>: Convert provider tool call responses to ToolCallRequest
    3. to_tool_result_<provider>: Convert tool execution results to provider result format
    """

    # -------------------------------------------------------------------------
    # Tool definition conversion: internal → provider format
    # -------------------------------------------------------------------------

    def to_gemini(self, tools: list[ToolDefinition]) -> list[dict]:
        """Convert internal tool definitions to Gemini function declarations.

        Gemini format:
            {
                "name": "tool_name",
                "description": "...",
                "parameters": { <JSON Schema object> }
            }
        """
        declarations = []
        for tool in tools:
            declaration: dict[str, Any] = {
                "name": tool.name,
                "description": tool.description,
            }
            # Build the parameters schema
            params_schema = self._build_parameters_schema(tool)
            if params_schema:
                declaration["parameters"] = params_schema
            declarations.append(declaration)
        return declarations

    def to_openai(self, tools: list[ToolDefinition]) -> list[dict]:
        """Convert internal tool definitions to OpenAI function-calling format.

        OpenAI format:
            {
                "type": "function",
                "function": {
                    "name": "tool_name",
                    "description": "...",
                    "parameters": { <JSON Schema object> }
                }
            }
        """
        result = []
        for tool in tools:
            params_schema = self._build_parameters_schema(tool)
            function_def: dict[str, Any] = {
                "name": tool.name,
                "description": tool.description,
            }
            if params_schema:
                function_def["parameters"] = params_schema
            result.append({"type": "function", "function": function_def})
        return result

    def to_anthropic(self, tools: list[ToolDefinition]) -> list[dict]:
        """Convert internal tool definitions to Anthropic tool format.

        Anthropic format:
            {
                "name": "tool_name",
                "description": "...",
                "input_schema": { <JSON Schema object> }
            }
        """
        result = []
        for tool in tools:
            params_schema = self._build_parameters_schema(tool)
            tool_def: dict[str, Any] = {
                "name": tool.name,
                "description": tool.description,
                "input_schema": params_schema or {"type": "object", "properties": {}},
            }
            result.append(tool_def)
        return result

    # -------------------------------------------------------------------------
    # Tool call normalization: provider response → internal ToolCallRequest
    # -------------------------------------------------------------------------

    def normalize_tool_calls_gemini(self, function_calls: list) -> list[ToolCallRequest]:
        """Normalize Gemini FunctionCall objects to internal ToolCallRequest.

        Gemini FunctionCall objects have:
            - name: str
            - args: dict (already parsed)

        Gemini does not provide a call ID, so we generate a UUID.
        """
        results = []
        for fc in function_calls:
            name = getattr(fc, "name", None) or (fc.get("name") if isinstance(fc, dict) else None)
            if not name:
                logger.warning("Skipping Gemini function call with no name: %s", fc)
                continue

            # Args can be a dict attribute or a dict key
            args = getattr(fc, "args", None)
            if args is None and isinstance(fc, dict):
                args = fc.get("args", {})
            if args is None:
                args = {}

            # If args is a string (shouldn't happen for Gemini but handle defensively)
            if isinstance(args, str):
                try:
                    args = _relaxed_json_parse(args)
                except ValueError as e:
                    logger.error(
                        "Failed to parse Gemini tool call args for %s: %s",
                        name,
                        e,
                    )
                    args = {}

            # Generate a unique ID since Gemini doesn't provide one
            call_id = str(uuid.uuid4())

            results.append(ToolCallRequest(id=call_id, name=name, arguments=dict(args)))
        return results

    def normalize_tool_calls_openai(self, tool_calls: list) -> list[ToolCallRequest]:
        """Normalize OpenAI tool_calls response objects to internal ToolCallRequest.

        OpenAI tool_calls have:
            - id: str
            - function.name: str
            - function.arguments: str (JSON string)
        """
        results = []
        for tc in tool_calls:
            # Support both object attribute access and dict access
            if isinstance(tc, dict):
                call_id = tc.get("id", str(uuid.uuid4()))
                function = tc.get("function", {})
                name = function.get("name", "") if isinstance(function, dict) else ""
                raw_args = function.get("arguments", "{}") if isinstance(function, dict) else "{}"
            else:
                call_id = getattr(tc, "id", str(uuid.uuid4()))
                function = getattr(tc, "function", None)
                name = getattr(function, "name", "") if function else ""
                raw_args = getattr(function, "arguments", "{}") if function else "{}"

            if not name:
                logger.warning("Skipping OpenAI tool call with no name: %s", tc)
                continue

            # Parse the JSON arguments string with relaxed parsing
            if isinstance(raw_args, str):
                try:
                    arguments = _relaxed_json_parse(raw_args)
                except ValueError as e:
                    logger.error(
                        "Failed to parse OpenAI tool call args for %s: %s",
                        name,
                        e,
                    )
                    raise ValueError(
                        f"Malformed tool call arguments from OpenAI for '{name}': {e}"
                    ) from e
            elif isinstance(raw_args, dict):
                arguments = raw_args
            else:
                arguments = {}

            results.append(ToolCallRequest(id=call_id, name=name, arguments=arguments))
        return results

    def normalize_tool_calls_anthropic(self, content_blocks: list) -> list[ToolCallRequest]:
        """Normalize Anthropic tool_use content blocks to internal ToolCallRequest.

        Anthropic tool_use blocks have:
            - id: str
            - name: str
            - input: dict (already parsed)
        """
        results = []
        for block in content_blocks:
            # Filter to only tool_use blocks
            block_type = (
                block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            )
            if block_type != "tool_use":
                continue

            if isinstance(block, dict):
                call_id = block.get("id", str(uuid.uuid4()))
                name = block.get("name", "")
                input_data = block.get("input", {})
            else:
                call_id = getattr(block, "id", str(uuid.uuid4()))
                name = getattr(block, "name", "")
                input_data = getattr(block, "input", {})

            if not name:
                logger.warning("Skipping Anthropic tool_use block with no name: %s", block)
                continue

            # Input is typically already a dict, but handle string case defensively
            if isinstance(input_data, str):
                try:
                    input_data = _relaxed_json_parse(input_data)
                except ValueError as e:
                    logger.error(
                        "Failed to parse Anthropic tool call input for %s: %s",
                        name,
                        e,
                    )
                    raise ValueError(
                        f"Malformed tool call arguments from Anthropic for '{name}': {e}"
                    ) from e
            elif not isinstance(input_data, dict):
                input_data = {}

            results.append(ToolCallRequest(id=call_id, name=name, arguments=dict(input_data)))
        return results

    # -------------------------------------------------------------------------
    # Tool result conversion: execution result → provider format
    # -------------------------------------------------------------------------

    def to_tool_result_gemini(self, name: str, result: dict) -> dict:
        """Convert tool execution result to Gemini FunctionResponse format.

        Gemini format:
            {
                "function_response": {
                    "name": "tool_name",
                    "response": { ... result dict ... }
                }
            }
        """
        return {
            "function_response": {
                "name": name,
                "response": result,
            }
        }

    def to_tool_result_openai(self, tool_call_id: str, result: dict) -> dict:
        """Convert tool execution result to OpenAI tool message format.

        OpenAI format:
            {
                "role": "tool",
                "tool_call_id": "call_xxx",
                "content": "<JSON string of result>"
            }
        """
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(result),
        }

    def to_tool_result_anthropic(self, tool_use_id: str, result: dict) -> dict:
        """Convert tool execution result to Anthropic tool_result block format.

        Anthropic format:
            {
                "type": "tool_result",
                "tool_use_id": "toolu_xxx",
                "content": "<JSON string of result>"
            }
        """
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": json.dumps(result),
        }

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _build_parameters_schema(self, tool: ToolDefinition) -> dict[str, Any]:
        """Build a JSON Schema object for tool parameters.

        If the tool's parameters dict already looks like a complete JSON Schema
        (has "type" and "properties"), use it directly. Otherwise, wrap the
        parameters as properties in an object schema.
        """
        params = tool.parameters

        if not params:
            return {"type": "object", "properties": {}}

        # If it already has "type": "object" and "properties", it's a full schema
        if params.get("type") == "object" and "properties" in params:
            schema = dict(params)
            # Ensure required field is set from the tool definition
            if tool.required:
                schema["required"] = tool.required
            return schema

        # Otherwise, treat the params dict as a properties map
        # Each value should be a property schema (e.g., {"type": "string", "description": "..."})
        schema: dict[str, Any] = {
            "type": "object",
            "properties": params,
        }
        if tool.required:
            schema["required"] = tool.required
        return schema
