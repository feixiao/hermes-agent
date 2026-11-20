"""回合循环中的单轮工具调用执行切片（Step 6: Tool Round）。

核心职责与不变式：
1. 工具调用校验、截断修复与去重（``validate_tool_calls`` / ``deduplicate``）
2. **Persist-Before-Execute 契约**：在执行任何带副作用的工具前，必须先把包含 ``tool_calls``
   的 assistant 消息原子持久化到 SessionDB（若发生系统断电或破坏性工具重启，恢复时可查已执行块）
3. 工具并发执行（``ThreadPoolExecutor``）与结果严格按原始 ``tool_call_id`` 顺序回写
4. 护栏拦截与持久化失败熔断（Fail-Closed 终止当前回合）
5. 工具结果返回后的增量微压缩检查（``compress_after_tool_results``）"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
import logging
from typing import Any, Dict, Optional, Tuple

from agent.message_metadata import append_message
from agent.message_sanitization import coalesce_tool_call_id, normalize_provider_tool_call_ids
from agent.turn_preflight import compress_after_tool_results
from agent.turn_tool_validation import validate_tool_calls

logger = logging.getLogger("agent.conversation_loop")

# Post-response housekeeping tools: a round made only of these mutes tool progress.
_HOUSEKEEPING_TOOLS = frozenset({"memory", "todo_list", "skill_manage", "session_search"})
# Rounds whose visible line is the turn's reply if the follow-up is empty (no empty-response nudge):
# housekeeping, and setup_beat, whose card already blocked for the user's answer.
_LINE_IS_REPLY_TOOLS = _HOUSEKEEPING_TOOLS | {"setup_beat"}


@dataclass
class ToolRoundVerdict:
    """工具轮次执行裁决对象。

    ``action`` 取值：
    - ``"continue"``: 工具成功执行并写入结果，驱动下一轮 API 调用
    - ``"break"``: 中断回合循环（持久化失败、安全护栏阻断、后置压缩终结等）
    - ``"return"``: 直接结束回合并返回最终结果 dict（由 ``result`` 携带）"""
    action: str
    messages: Any
    conversation_history: Any
    active_system_prompt: Any
    compression_attempts: Any
    final_response: Any
    failed: Any
    _turn_exit_reason: Any
    truncated_tool_call_retries: Any
    current_turn_user_idx: Any
    result: Optional[dict[str, Any]] = None


def run_tool_round(
    agent: Any, *, assistant_message: Any, finish_reason: Any, messages: Any,
    conversation_history: Any, api_call_count: Any, effective_task_id: Any, user_message: Any,
    system_message: Any, active_system_prompt: Any, compression_attempts: Any,
    max_compression_attempts: Any, final_response: Any, failed: Any, _turn_exit_reason: Any,
    truncated_tool_call_retries: Any, current_turn_user_idx: Any,
) -> ToolRoundVerdict:
    """按模型最初返回的精确顺序执行一轮工具调用。

    【核心不变式】：
    - **Persist-Before-Execute**：执行工具前先将 assistant tool_calls 刷入 SQLite，
      若持久化失败则绝不凭内存状态裸执行工具，直接 Fail-Closed 中断回合。
    - **顺序回写**：即使多个无依赖工具并发执行，其写回 messages 的 tool 结果也必须
      与原始 tool_calls 序列严格一一对应。"""
    from agent.conversation_loop import _invalid_tool_name_error_content

    def _verdict(action: str, result: Optional[dict[str, Any]] = None) -> ToolRoundVerdict:
        return ToolRoundVerdict(
            action=action, messages=messages, conversation_history=conversation_history,
            active_system_prompt=active_system_prompt, compression_attempts=compression_attempts,
            final_response=final_response, failed=failed, _turn_exit_reason=_turn_exit_reason,
            truncated_tool_call_retries=truncated_tool_call_retries,
            current_turn_user_idx=current_turn_user_idx, result=result,
        )

    if not agent.quiet_mode:
        agent._vprint(f"{agent.log_prefix}🔧 Processing {len(assistant_message.tool_calls)} tool call(s)...")

    if agent.verbose_logging:
        for tc in assistant_message.tool_calls:
            raw_args = tc.function.arguments
            args_preview = raw_args[:200] if isinstance(raw_args, str) else repr(raw_args)[:200]
            logging.debug("Tool call: %s with args: %s...", tc.function.name, args_preview)

    _tvv = validate_tool_calls(
        agent, assistant_message, finish_reason, messages=messages,
        conversation_history=conversation_history, api_call_count=api_call_count,
        effective_task_id=effective_task_id,
    )
    if _tvv.action == "return":
        return _verdict("return", _tvv.result)
    if _tvv.action == "continue":
        return _verdict("continue")

    # Normalize only this unpersisted turn. Each local bridge entry must pass
    # through the same scope, approvals, state and scheduling as a singleton.
    from agent.tool_call_batches import expand_local_tool_batches
    assistant_message.tool_calls = expand_local_tool_batches(
        assistant_message.tool_calls, provider_data=getattr(assistant_message, "provider_data", None))

    # Post-call guardrails.
    assistant_message.tool_calls = agent._deduplicate_tool_calls(
        agent._cap_delegate_task_calls(assistant_message.tool_calls)
    )
    # Filtering can turn a mixed batch into an all-provider one; re-check the final batch
    # before it is staged (idempotent for already-normalized ids).
    normalize_provider_tool_call_ids(assistant_message.tool_calls)

    # Mixed batch: the assistant message keeps EVERY emitted call (each tool_call needs a
    # matching result) while only valid ones dispatch.
    _invalid_batch_calls = [
        tc for tc in assistant_message.tool_calls if tc.function.name not in agent.valid_tool_names
    ] if _tvv.mixed_invalid_batch else []

    assistant_msg, duplicate_previous_interim = stage_tool_call_message(
        agent, assistant_message=assistant_message, finish_reason=finish_reason, messages=messages
    )
    append_message(messages, assistant_msg)

    # Mixed batch: error-result invalid calls and drop them from execution.
    if _invalid_batch_calls:
        for tc in _invalid_batch_calls:
            append_message(messages, {
                "role": "tool",
                "name": tc.function.name,
                "tool_call_id": coalesce_tool_call_id(tc),
                "content": _invalid_tool_name_error_content(
                    tc.function.name, agent.valid_tool_names
                ),
            })
        assistant_message.tool_calls = [
            tc for tc in assistant_message.tool_calls if tc.function.name in agent.valid_tool_names
        ]

    # ── 核心不变式：Persist-Before-Execute（先落盘持久化 tool_calls 再执行任何工具副作用）──
    try:
        _tool_turn_persisted = agent._flush_messages_to_session_db(messages, conversation_history)
    except Exception as exc:
        _tool_turn_persisted = False
        from hermes_state import classify_persistence_error
        agent._last_persistence_error_cause = classify_persistence_error(exc)
        logger.warning(
            "Incremental tool-call persistence failed before execution "
            "(session=%s): %s",
            agent.session_id or "none",
            exc,
        )

    if _tool_turn_persisted is False:
        # Canonical append failed: never project the row or run tools from process-only
        # state; break rather than retry. No recorded cause means genuinely unknown.
        if getattr(agent, "_last_persistence_error_cause", None) is None:
            agent._last_persistence_error_cause = "unknown"
        _turn_exit_reason = "session_persistence_failed"
        final_response = ""
        failed = True
        return _verdict("break")

    # A UI must never observe an assistant/tool-call row that is only an in-memory
    # projection: emit interim commentary after the DB append.
    if not duplicate_previous_interim:
        agent._emit_interim_assistant_message(assistant_msg)

    # Flush open streaming boxes before tools so early content doesn't wrap tool feed
    # lines. Display callback only — TTS (_stream_callback) must NOT receive None (EOS).
    if agent.stream_delta_callback:
        with suppress(Exception):
            agent.stream_delta_callback(None)

    # Opt-in (compression.prepare_ahead): when this response put the session near the trigger,
    # write the next compaction summary while the tools run. No-op otherwise; never raises.
    from agent.prepared_compaction import maybe_prepare
    maybe_prepare(agent, messages, origin="tool-batch")

    # ── 核心分派：执行工具调用（注册表并发或内联拦截），按原始顺序收集结果回写 messages ──
    agent._execute_tool_calls(assistant_message, messages, effective_task_id, api_call_count)
    from hermes_cli.observability.shared_metrics_harness import finish_tool_round

    finish_tool_round(agent)

    if getattr(agent, "_incremental_persistence_failed", False):
        # Tool result could not be made canonical: never send the in-memory result to
        # the model or project later events from this turn.
        _turn_exit_reason = "session_persistence_failed"
        final_response = ""
        failed = True
        return _verdict("break")

    if agent._tool_guardrail_halt_decision is not None:
        decision = agent._tool_guardrail_halt_decision
        _turn_exit_reason = "guardrail_halt"
        final_response = agent._toolguard_controlled_halt_response(decision)
        agent._emit_diagnostic_status(f"⚠️ Tool guardrail halted {decision.tool_name}: {decision.code}")
        append_message(messages, {"role": "assistant", "content": final_response})
        # Emit the halt so it isn't mistaken for a crash; the stream callback is still
        # alive, so SSE/TUI clients see the explanation. A held turn shows it as the final only.
        if final_response:
            agent._safe_print(f"\n{final_response}\n")
            if agent.stream_delta_callback and not getattr(agent, "_hold_stream", False):
                with suppress(Exception):
                    agent.stream_delta_callback(final_response)
                    agent.stream_delta_callback(None)
        return _verdict("break")

    # Reset per-turn retry counters so one truncation can't poison the turn.
    truncated_tool_call_retries = 0
    # Defer the paragraph break: _fire_stream_delta() prepends one "\n\n" when real
    # text arrives, so tool iterations don't stack blank lines.
    agent._stream_needs_break = True
    # ── PTC 编程式工具调用退款：若唯一工具是 execute_code 则退还迭代预算 ──
    if {tc.function.name for tc in assistant_message.tool_calls} == {"execute_code"}:
        agent.iteration_budget.refund()

    _ptc = compress_after_tool_results(
        agent, messages=messages, system_message=system_message, user_message=user_message,
        active_system_prompt=active_system_prompt, conversation_history=conversation_history,
        compression_attempts=compression_attempts,
        max_compression_attempts=max_compression_attempts, effective_task_id=effective_task_id,
        final_response=final_response, turn_exit_reason=_turn_exit_reason,
        current_turn_user_idx=current_turn_user_idx,
    )
    messages = _ptc.messages
    active_system_prompt = _ptc.active_system_prompt
    conversation_history = _ptc.conversation_history
    compression_attempts = _ptc.compression_attempts
    final_response = _ptc.final_response
    _turn_exit_reason = _ptc.turn_exit_reason
    current_turn_user_idx = _ptc.current_turn_user_idx
    if _ptc.end_turn:
        return _verdict("break")

    # Save session log incrementally (so progress is visible even if interrupted)
    agent._session_messages = messages
    # Touch activity so slow post-tool work plus a slow follow-up API call can't exceed
    # the gateway inactivity timeout (HERMES_AGENT_TIMEOUT).
    # Touch activity before continuing so the gateway's inactivity monitor never sees a stale timestamp
    # between tool completion and the start of the next API call. Without this, a tool-call result (which
    # takes ~0s to process) followed by slow post-tool processing (compression, persist) and a slow
    # follow-up API call can exceed the gateway inactivity timeout (HERMES_AGENT_TIMEOUT, default 1800s) and
    # the gateway kills the session before the next activity touch fires (#69559, #69131).
    agent._touch_activity(f"tool results posted, continuing iteration #{api_call_count}")
    return _verdict("continue")


def stage_tool_call_message(
    agent: Any, *, assistant_message: Any, finish_reason: Any, messages: Any
) -> tuple[dict[str, Any], bool]:
    """Build the assistant tool-call row and update the per-turn fallback/mute state.

    Drops a bare bracketed marker beside a call (#78148), classifies housekeeping-only
    rounds, keeps visible content as the empty-follow-up fallback, pops thinking-only
    prefills (resetting their counters), re-arms the post-tool nudge and the
    dropped-tool-call stall budget. Returns ``(assistant_msg, duplicate_previous_interim)``;
    the flag suppresses re-emitting interim commentary the previous ``incomplete`` row
    already showed."""
    from agent.conversation_loop import _STALE_MARKER_RE

    assistant_msg = agent._build_assistant_message(assistant_message, finish_reason)

    turn_content = assistant_message.content or ""

    # A bare bracketed token (e.g. ``[memory]``) beside a function call is protocol
    # scaffolding; persisting it lets the post-tool fallback replay it forever (#78148).
    if assistant_message.tool_calls and _STALE_MARKER_RE.fullmatch(turn_content.strip()):
        logger.warning(
            "Discarding bare tool-call marker from assistant content: %s", turn_content
        )
        turn_content = ""
        assistant_msg["content"] = ""

    # Classify tools regardless of visible content: a substantive tool-only turn must
    # invalidate any older housekeeping fallback (so a two-turn-old housekeeping
    # narration isn't attributed to the preceding tool turn), and clear the mute flag a
    # prior housekeeping turn set, else _vprint suppresses this turn's tool progress.
    _all_housekeeping = all(
        tc.function.name in _HOUSEKEEPING_TOOLS for tc in assistant_message.tool_calls
    )
    # The line written with a blocking card is the reply when the follow-up comes back empty.
    _line_is_reply = all(tc.function.name in _LINE_IS_REPLY_TOOLS for tc in assistant_message.tool_calls)
    if assistant_message.tool_calls and not _all_housekeeping:
        agent._last_content_with_tools = None
        agent._last_content_tools_all_housekeeping = False
        agent._mute_post_response = False

    # Content + tool_calls in one turn: keep the content as a fallback final response in
    # case the follow-up turn after tools is empty. Mute only when EVERY tool call is
    # post-response housekeeping; substantive tools keep output on.
    if turn_content and agent._has_content_after_think_block(turn_content):
        agent._last_content_with_tools = turn_content
        agent._last_content_tools_all_housekeeping = _line_is_reply
        if _all_housekeeping and agent._has_stream_consumers():
            agent._mute_post_response = True
        elif agent._should_emit_quiet_tool_messages() and not getattr(agent, "_hold_stream", False):
            clean = agent._strip_think_blocks(turn_content).strip()
            if clean:
                agent._vprint(f"  ┊ 💬 {clean}")

    # Pop thinking-only prefill message(s) before appending (same rationale as the
    # final-response path). A landed tool call resets the thinking retry budget even
    # when recovery used a request-local reminder instead of a legacy prefill row.
    _had_prefill = False
    while messages and isinstance(messages[-1], dict) and messages[-1].get("_thinking_prefill"):
        messages.pop()
        _had_prefill = True
    if _had_prefill or getattr(agent, "_thinking_prefill_retries", 0):
        agent._thinking_prefill_retries = 0
        agent._empty_content_retries = 0
    # Re-arm the post-tool nudge so it can fire on a LATER tool round; a landed tool call
    # recovers any dropped-tool-call stall, so refresh that budget per stall.
    agent._post_tool_empty_retried = False
    agent._dropped_toolcall_retries = 0

    previous_msg = messages[-1] if messages else None
    current_interim_visible = agent._interim_assistant_visible_text(assistant_msg)
    duplicate_previous_interim = (
        bool(current_interim_visible)
        and isinstance(previous_msg, dict)
        and previous_msg.get("role") == "assistant"
        and previous_msg.get("finish_reason") == "incomplete"
        and agent._interim_assistant_visible_text(previous_msg) == current_interim_visible
    )
    return assistant_msg, duplicate_previous_interim
