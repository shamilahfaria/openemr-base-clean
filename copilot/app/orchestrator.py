"""Agent Orchestrator — STUB (no implementation yet).

A thin Anthropic tool-use loop (ARCHITECTURE.md, Component 5). Per turn:
load session history -> build a bounded, patient-scoped prompt -> let Claude
call read-only tools (each call validated by the PatientScopeGuard FIRST) ->
collect every retrieved record -> return the draft for verification.

Rules the tests pin:
  * The scope guard validates every tool_use BEFORE execution. A violation
    means the tool is NOT executed; Claude receives an error tool_result.
  * Unknown tool names and tool failures become error tool_results — the loop
    continues; the orchestrator never crashes the turn for a bad tool call.
  * The tool loop is capped at MAX_TOOL_ITERATIONS (runaway guard).
  * The system prompt names the active patient id (bounded prompt).
  * History: prior turns are sent to Claude; the user message and final answer
    are appended to the session after the turn.
  * Fail closed at entry: blank message or bearer token -> ValueError before
    any Claude call.
  * Anthropic API errors propagate — fallback logic (build step 8) owns them.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from pydantic import BaseModel

from .scope import PatientScopeGuard
from .sessions import SessionStore

MAX_TOOL_ITERATIONS = 10

# A tool receives the model-proposed arguments plus the caller's bearer token
# and returns the retrieved records.
ToolFn = Callable[[dict, str], Awaitable[list]]


class ToolLoopLimitError(Exception):
    """The model kept requesting tools past MAX_TOOL_ITERATIONS."""


class TurnDraft(BaseModel):
    """Unverified draft of one turn — input to the verification layer."""

    answer: str
    retrieved: list[Any]        # every record returned by tools this turn
    tools_used: list[str]       # tool names actually executed


class Orchestrator:
    def __init__(
        self,
        anthropic_client: Any,
        tool_registry: dict[str, ToolFn],
        session_store: SessionStore,
        model: str = "claude-sonnet-4-5",
    ):
        raise NotImplementedError

    async def run_turn(
        self,
        *,
        patient_id: str,
        bearer_token: str,
        session_id: str,
        message: str,
        scope_guard: PatientScopeGuard,
    ) -> TurnDraft:
        raise NotImplementedError
