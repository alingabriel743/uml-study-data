"""
Base agent class using Claude Agent SDK.
Adapted from atonis-req-copilot pattern with verbose logging.
"""

import asyncio
import contextvars
import json
import os
import time
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Callable, Optional

from app.utils.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Configure AWS Bedrock at module level (once at startup)
# Credentials are loaded automatically from ~/.aws/credentials
if settings.use_bedrock:
    os.environ["CLAUDE_CODE_USE_BEDROCK"] = "1"
    os.environ["AWS_REGION"] = settings.aws_region
    # Set the model for Claude Agent SDK
    os.environ["ANTHROPIC_MODEL"] = settings.bedrock_model_id
    logger.info(f" Bedrock enabled: region={settings.aws_region}, model={settings.bedrock_model_id} (1M context)")
else:
    # Pin the model explicitly so runs are reproducible (otherwise the SDK default is used)
    os.environ["ANTHROPIC_MODEL"] = settings.anthropic_model
    logger.info(f"  Using Anthropic Direct API: model={settings.anthropic_model}")


def resolved_model() -> dict:
    """Provider and model actually configured for this process (recorded in run manifests)."""
    return {
        "provider": "bedrock" if settings.use_bedrock else "anthropic",
        "model": os.environ.get("ANTHROPIC_MODEL", ""),
    }


# Per-run usage ledger: the orchestrator sets a list; every agent session appends its
# ResultMessage statistics (tokens, cache, cost, duration). Tasks created after the
# ledger is set inherit it, so parallel agents report into the same run.
usage_ledger: contextvars.ContextVar[Optional[list]] = contextvars.ContextVar("usage_ledger", default=None)


class BaseAgent(ABC):
    """
    Base agent class wrapping Claude Agent SDK.

    Provides:
    - Claude Agent SDK integration with built-in tools (Read, Write, Glob, Grep, Bash)
    - Standard message handling and streaming
    - Tool permission management
    - Hooks for monitoring and logging
    - Verbose logging of all agent activity
    """

    def __init__(
        self,
        name: str,
        system_prompt: str,
        tools: list[str] | None = None,
        permission_mode: str = "acceptEdits",
    ):
        self.name = name
        self.system_prompt = system_prompt
        # None → default read-only set; [] → no tools at all (plain-LLM baseline)
        self.tools = ["Read", "Glob", "Grep"] if tools is None else tools
        self.permission_mode = permission_mode
        self._sdk_available = self._check_sdk()
        self._hooks: dict[str, list[Callable]] = {
            "before_execute": [],
            "after_message": [],
            "on_error": [],
        }

    def _check_sdk(self) -> bool:
        """Check if Claude Agent SDK is available."""
        try:
            from claude_agent_sdk import ClaudeSDKClient  # noqa: F401
            return True
        except ImportError:
            logger.warning(
                f"  Claude Agent SDK not available for {self.name}. "
                "Install with: pip install claude-agent-sdk"
            )
            return False

    def add_hook(self, event: str, callback: Callable) -> None:
        """Add a hook callback for monitoring agent behavior."""
        if event in self._hooks:
            self._hooks[event].append(callback)

    async def _trigger_hooks(self, event: str, *args, **kwargs) -> None:
        """Trigger all callbacks for a hook event."""
        for callback in self._hooks.get(event, []):
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(*args, **kwargs)
                else:
                    callback(*args, **kwargs)
            except Exception as e:
                logger.error(f"Hook callback failed for {event}: {e}")

    async def execute(
        self,
        prompt: str,
        context: Optional[dict] = None,
        session_id: Optional[str] = None,
    ) -> AsyncIterator[dict]:
        """
        Execute the agent with the given prompt using ClaudeSDKClient.

        Yields:
            Messages from the agent (reasoning, tool calls, results)
        """
        logger.info(f"")
        logger.info(f"{'=' * 80}")
        logger.info(f" AGENT: {self.name.upper()}")
        logger.info(f"{'=' * 80}")

        if not self._sdk_available:
            logger.error(f" Claude Agent SDK not available for {self.name}")
            yield {
                "type": "error",
                "message": "Claude Agent SDK not available. Install with: pip install claude-agent-sdk",
                "agent": self.name,
            }
            return

        from claude_agent_sdk import (
            ClaudeSDKClient,
            ClaudeAgentOptions,
            AssistantMessage,
            ResultMessage,
            SystemMessage,
            UserMessage,
            TextBlock,
            ToolUseBlock,
            ToolResultBlock,
        )

        full_prompt = self._build_prompt(prompt, context)
        logger.info(f" Prompt length: {len(full_prompt)} characters")
        logger.debug(f" Full prompt:\n{full_prompt[:1000]}...")

        if context:
            logger.info(f" Context provided: {list(context.keys())}")
        if session_id:
            logger.info(f" Resuming session: {session_id}")

        # Trigger before_execute hooks
        await self._trigger_hooks("before_execute", agent=self.name, prompt=full_prompt)

        try:
            # Build options for Claude Agent SDK
            logger.info(f"  Configuring Claude Agent SDK options:")
            logger.info(f"   ├─ Tools: {self.tools}")
            logger.info(f"   ├─ Permission mode: {self.permission_mode}")
            logger.info(f"   ├─ System prompt length: {len(self.system_prompt)} characters")
            logger.info(f"   └─ Bedrock: {'YES' if settings.use_bedrock else 'NO'}")

            options = ClaudeAgentOptions(
                # `tools` restricts the AVAILABLE tool set; `allowed_tools` only pre-approves.
                # Without `tools`, every built-in Claude Code tool (Bash, WebFetch, ...) is available.
                tools=self.tools,
                allowed_tools=self.tools,
                system_prompt=self.system_prompt,
                permission_mode=self.permission_mode,
                resume=session_id,
            )

            logger.info(f"")
            logger.info(f" Starting ClaudeSDKClient...")
            start_time = time.time()

            async with ClaudeSDKClient(options=options) as client:
                init_time = time.time() - start_time
                logger.info(f" ClaudeSDKClient initialized in {init_time:.1f}s")

                # Send the query
                logger.info(f" Sending query to Claude...")
                query_start = time.time()
                await client.query(full_prompt)
                query_time = time.time() - query_start
                logger.info(f" Query sent in {query_time:.1f}s, waiting for messages...")

                message_count = 0
                tool_call_count = 0
                text_block_count = 0
                captured_session_id = None

                # Receive and process messages
                logger.info(f"")
                logger.info(f"{'─' * 60}")
                logger.info(f" MESSAGE STREAM")
                logger.info(f"{'─' * 60}")

                async for message in client.receive_messages():
                    message_count += 1
                    msg_type = type(message).__name__
                    elapsed = time.time() - start_time
                    logger.info(f"")
                    logger.info(f" Message #{message_count} [{msg_type}] (t+{elapsed:.1f}s)")

                    # --- SystemMessage ---
                    if isinstance(message, SystemMessage):
                        subtype = getattr(message, 'subtype', None)
                        logger.info(f"     SystemMessage subtype: {subtype}")
                        if subtype == 'init':
                            data = getattr(message, 'data', {})
                            captured_session_id = data.get('session_id')
                            if captured_session_id:
                                logger.info(f"    Session ID captured: {captured_session_id}")
                            # Log MCP server status if present
                            mcp_servers = getattr(message, 'mcp_servers', None)
                            if mcp_servers:
                                for srv in mcp_servers:
                                    name = getattr(srv, 'name', str(srv))
                                    status = getattr(srv, 'status', 'unknown')
                                    logger.info(f"    MCP server: {name} → {status}")

                    # --- AssistantMessage ---
                    elif isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                text_block_count += 1
                                text = block.text
                                logger.info(f"    [{self.name}] Text ({len(text)} chars):")
                                # Log first 300 chars of reasoning
                                for line in text[:300].split('\n'):
                                    if line.strip():
                                        logger.info(f"      │ {line.strip()}")
                                if len(text) > 300:
                                    logger.info(f"      │ ... ({len(text) - 300} more chars)")

                            elif isinstance(block, ToolUseBlock):
                                tool_call_count += 1
                                tool_name = block.name
                                tool_input = block.input or {}
                                logger.info(f"    [{self.name}] Tool #{tool_call_count}: {tool_name}")

                                # Detailed input logging per tool type
                                if tool_name in ["Read", "Write", "Edit"]:
                                    logger.info(f"      ├─ File: {tool_input.get('path', 'N/A')}")
                                    if tool_name == "Write":
                                        content = tool_input.get('content', '')
                                        logger.info(f"      ├─ Content length: {len(content)} chars")
                                        logger.info(f"      └─ Preview: {content[:100]}...")
                                    elif tool_name == "Edit":
                                        logger.info(f"      └─ Edit operation on file")
                                elif tool_name == "Bash":
                                    cmd = tool_input.get('command', '')
                                    logger.info(f"      └─ Command: {cmd[:200]}")
                                elif tool_name in ["Glob", "Grep"]:
                                    pattern = tool_input.get('pattern', tool_input.get('glob', 'N/A'))
                                    path = tool_input.get('path', '.')
                                    logger.info(f"      ├─ Pattern: {pattern}")
                                    logger.info(f"      └─ Path: {path}")
                                else:
                                    # Generic tool input logging
                                    input_str = json.dumps(tool_input, default=str)
                                    logger.info(f"      └─ Input: {input_str[:200]}")

                    # --- UserMessage (tool results) ---
                    elif isinstance(message, UserMessage):
                        if hasattr(message, 'content') and isinstance(message.content, list):
                            for block in message.content:
                                if isinstance(block, ToolResultBlock):
                                    content = block.content if isinstance(block.content, str) else str(block.content)
                                    is_error = getattr(block, 'is_error', None)
                                    tool_id = getattr(block, 'tool_use_id', '')[:12]
                                    if is_error:
                                        logger.warning(f"     Tool result (ERROR) [{tool_id}]: {content[:300]}")
                                    else:
                                        logger.info(f"    Tool result [{tool_id}] ({len(content)} chars)")
                                        # Show preview of result for small results
                                        if len(content) < 500:
                                            for line in content[:300].split('\n')[:5]:
                                                if line.strip():
                                                    logger.info(f"      │ {line.strip()}")
                                        else:
                                            logger.info(f"      │ (large result, {len(content)} chars)")

                    # --- ResultMessage ---
                    elif isinstance(message, ResultMessage):
                        subtype = message.subtype
                        duration = getattr(message, 'duration_ms', 0)
                        turns = getattr(message, 'num_turns', 0)
                        cost = getattr(message, 'total_cost_usd', 0)
                        logger.info(f"    RESULT: {subtype}")
                        logger.info(f"      ├─ Duration: {duration}ms ({duration/1000:.1f}s)")
                        logger.info(f"      ├─ Turns: {turns}")
                        logger.info(f"      ├─ Cost: ${cost:.4f}")
                        if hasattr(message, 'usage') and message.usage:
                            usage = message.usage
                            logger.info(f"      ├─ Input tokens:  {usage.get('input_tokens', 0):,}")
                            logger.info(f"      ├─ Output tokens: {usage.get('output_tokens', 0):,}")
                            cache_read = usage.get('cache_read_input_tokens', 0)
                            cache_create = usage.get('cache_creation_input_tokens', 0)
                            if cache_read or cache_create:
                                logger.info(f"      ├─ Cache read:   {cache_read:,}")
                                logger.info(f"      └─ Cache create: {cache_create:,}")

                    # Process message into standard format
                    processed = self._process_message(message)

                    # Trigger after_message hooks
                    await self._trigger_hooks("after_message", agent=self.name, message=processed)

                    yield processed

                    # Break on result message (end of conversation)
                    if isinstance(message, ResultMessage):
                        break

                # Final summary
                total_time = time.time() - start_time
                logger.info(f"")
                logger.info(f"{'─' * 60}")
                logger.info(f" AGENT EXECUTION SUMMARY: {self.name}")
                logger.info(f"{'─' * 60}")
                logger.info(f"   Total messages:    {message_count}")
                logger.info(f"   Text blocks:       {text_block_count}")
                logger.info(f"   Tool calls:        {tool_call_count}")
                logger.info(f"   Total time:        {total_time:.1f}s")
                if captured_session_id:
                    logger.info(f"   Session ID:        {captured_session_id}")
                logger.info(f"{'=' * 80}")
                logger.info(f" {self.name} execution completed successfully")
                logger.info(f"{'=' * 80}")

        except ImportError as e:
            error_msg = f"Missing dependency for {self.name}: {e}"
            logger.error(f" {error_msg}", exc_info=True)
            await self._trigger_hooks("on_error", agent=self.name, error=error_msg)
            yield {"type": "error", "message": error_msg, "agent": self.name}

        except Exception as e:
            total_time = time.time() - start_time
            error_msg = f"Agent {self.name} execution failed: {e}"
            logger.error(f"")
            logger.error(f"{'=' * 80}")
            logger.error(f" AGENT FAILED: {self.name}")
            logger.error(f"{'=' * 80}")
            logger.error(f"   Error type: {type(e).__name__}")
            logger.error(f"   Error msg:  {str(e)}")
            logger.error(f"   Time:       {total_time:.1f}s")
            logger.error(f"   Full traceback:", exc_info=True)
            await self._trigger_hooks("on_error", agent=self.name, error=str(e))
            yield {"type": "error", "message": str(e), "agent": self.name}

        finally:
            logger.info(f"")
            logger.info(f"Finished {self.name} execution")

    def _build_prompt(self, prompt: str, context: Optional[dict]) -> str:
        """Build the full prompt with context."""
        cleanup = "\nIMPORTANT: If you create any temporary files, delete them before finishing.\n"
        if context:
            context_str = "\n".join(f"- {k}: {v}" for k, v in context.items())
            return f"Context:\n{context_str}\n\nTask:\n{prompt}\n{cleanup}"
        return f"Task:\n{prompt}\n{cleanup}"

    def _process_message(self, message: Any) -> dict:
        """Process a Claude Agent SDK message into a standard format."""
        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            SystemMessage,
            UserMessage,
            TextBlock,
            ToolUseBlock,
            ToolResultBlock,
        )

        if isinstance(message, AssistantMessage):
            text_parts = []
            tool_calls = []
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    tool_calls.append({"name": block.name, "input": block.input})
            return {
                "type": "assistant",
                "text": "\n".join(text_parts),
                "tool_calls": tool_calls,
                "agent": self.name,
            }

        elif isinstance(message, ResultMessage):
            usage = {}
            if hasattr(message, "usage") and message.usage:
                usage = {
                    "input_tokens": message.usage.get("input_tokens", 0),
                    "output_tokens": message.usage.get("output_tokens", 0),
                    "cache_read_input_tokens": message.usage.get("cache_read_input_tokens", 0),
                    "cache_creation_input_tokens": message.usage.get("cache_creation_input_tokens", 0),
                    "total_tokens": (
                        message.usage.get("input_tokens", 0)
                        + message.usage.get("output_tokens", 0)
                    ),
                }
            ledger = usage_ledger.get()
            if ledger is not None:
                ledger.append({
                    "agent": self.name,
                    "subtype": message.subtype,
                    "duration_ms": getattr(message, "duration_ms", 0),
                    "num_turns": getattr(message, "num_turns", 0),
                    "cost_usd": getattr(message, "total_cost_usd", 0) or 0,
                    "usage": usage,
                })
            return {
                "type": "result",
                "subtype": message.subtype,
                "duration_ms": getattr(message, "duration_ms", 0),
                "num_turns": getattr(message, "num_turns", 0),
                "cost_usd": getattr(message, "total_cost_usd", 0),
                "usage": usage,
                "agent": self.name,
            }

        elif isinstance(message, SystemMessage):
            return {
                "type": "system",
                "subtype": getattr(message, "subtype", None),
                "data": getattr(message, "data", {}),
                "agent": self.name,
            }

        elif isinstance(message, UserMessage):
            tool_results = []
            if hasattr(message, "content") and isinstance(message.content, list):
                for block in message.content:
                    if isinstance(block, ToolResultBlock):
                        tool_results.append({
                            "tool_use_id": getattr(block, "tool_use_id", ""),
                            "content": block.content if isinstance(block.content, str) else str(block.content),
                        })
            return {
                "type": "tool_result",
                "tool_results": tool_results,
                "agent": self.name,
            }

        return {"type": "unknown", "raw": str(message), "agent": self.name}

    @abstractmethod
    async def run(self, **kwargs) -> dict:
        """Run the agent's main task. Implemented by specialized agents."""
        pass
