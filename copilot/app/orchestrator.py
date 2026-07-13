"""Agent Orchestrator.

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
  * Anthropic API errors propagate — the caller's fallback path owns them.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from pydantic import BaseModel

from .scope import PatientScopeGuard, ScopeViolation
from .sessions import SessionStore

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 10

# Descriptions help the model pick the right tool (esp. code status / goals of care).
TOOL_DESCRIPTIONS = {
    "get_patient_summary": "Demographics, active problems, and recent context — cheap orientation.",
    "get_recent_encounters": "Recent visits / encounters, most recent first.",
    "search_notes": "Search clinical notes for a term; returns matching excerpts.",
    "get_medications": "Ordered medications with PRN flag and interval (orders only).",
    "get_allergies": "Documented allergies and reactions.",
    "get_labs": "Recent lab results with values and dates.",
    "get_vitals": "Recent vital signs.",
    "get_problem_list": "Active and historical problems.",
    "get_goals_of_care": "Code status and goals of care (e.g. DNR / full code / comfort measures).",
}

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
    input_tokens: int = 0       # summed across the turn's model round-trips
    output_tokens: int = 0


class Orchestrator:
    def __init__(
        self,
        anthropic_client: Any,
        tool_registry: dict[str, ToolFn],
        session_store: SessionStore,
        model: str = "claude-sonnet-4-5",
    ):
        self._client = anthropic_client
        self._tools = tool_registry
        self._sessions = session_store
        self._model = model

    def _tool_specs(self) -> list[dict]:
        specs = []
        for name in self._tools:
            properties = {
                "patient_id": {
                    "type": "string",
                    "description": "The active patient's id. Required.",
                }
            }
            required = ["patient_id"]
            if name == "search_notes":
                properties["query"] = {
                    "type": "string",
                    "description": "Case-insensitive term to match against note text.",
                }
                required.append("query")
            specs.append(
                {
                    "name": name,
                    "description": TOOL_DESCRIPTIONS.get(name, name),
                    "input_schema": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                }
            )
        return specs

    def _system_prompt(self, patient_id: str) -> str:
        return (
            "You are a read-only clinical co-pilot embedded in OpenEMR, assisting "
            f"a hospice nurse with the open chart for patient {patient_id}. Use the "
            "provided tools to retrieve this patient's data; always pass "
            f"patient_id={patient_id} and never any other patient id.\n\n"
            "CITATION PROTOCOL (required — the verification layer enforces it):\n"
            "- Every sentence must end with a marker.\n"
            "- For a statement drawn from a retrieved record, append "
            "[src: <source_id>] using the exact source_id field of the record it "
            "came from — copy it verbatim, character for character; never add a "
            "resource-type prefix, shorten it, or invent an id. Cite every "
            "record the sentence relies on.\n"
            "- For general medical knowledge NOT from this patient's record, append "
            "[general].\n"
            "- Never state a patient-specific fact without a [src: ...] marker. If no "
            "retrieved record supports a statement, do not make it.\n\n"
            "Keep answers concise and scannable for a nurse between rooms."
        )

    async def _execute_tool(
        self,
        block: Any,
        bearer_token: str,
        scope_guard: PatientScopeGuard,
        tools_used: list,
        retrieved: list,
    ) -> dict:
        def error(reason: str) -> dict:
            return {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": reason,
                "is_error": True,
            }

        try:
            scope_guard.validate_tool_call(block.name, block.input)
        except ScopeViolation:
            return error("tool call denied: out of patient scope")

        tool = self._tools.get(block.name)
        if tool is None:
            return error(f"unknown tool: {block.name}")

        try:
            records = await tool(block.input, bearer_token)
        except Exception as exc:
            # The model gets a generic failure; details stay in server logs
            # (exception type only — never record contents).
            logger.warning(
                "tool failed name=%s error=%s", block.name, type(exc).__name__
            )
            return error(f"tool failed: {block.name}")

        tools_used.append(block.name)
        retrieved.extend(records)
        content = json.dumps(
            [r.model_dump() if hasattr(r, "model_dump") else r for r in records],
            default=str,
        )
        return {"type": "tool_result", "tool_use_id": block.id, "content": content}

    async def run_turn(
        self,
        *,
        patient_id: str,
        bearer_token: str,
        session_id: str,
        message: str,
        scope_guard: PatientScopeGuard,
    ) -> TurnDraft:
        if not message.strip():
            raise ValueError("message is required")
        if not bearer_token.strip():
            raise ValueError("bearer token is required")

        messages = self._sessions.history(session_id, patient_id)
        messages.append({"role": "user", "content": message})

        tools_used: list[str] = []
        retrieved: list[Any] = []
        input_tokens = 0
        output_tokens = 0

        for _ in range(MAX_TOOL_ITERATIONS):
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=self._system_prompt(patient_id),
                messages=messages,
                tools=self._tool_specs(),
            )
            usage = getattr(response, "usage", None)
            input_tokens += getattr(usage, "input_tokens", 0) or 0
            output_tokens += getattr(usage, "output_tokens", 0) or 0

            if response.stop_reason != "tool_use":
                answer = "".join(
                    block.text for block in response.content if block.type == "text"
                )
                # Only a completed turn is recorded — failed turns never
                # pollute the session.
                self._sessions.append(session_id, patient_id, "user", message)
                self._sessions.append(session_id, patient_id, "assistant", answer)
                return TurnDraft(
                    answer=answer,
                    retrieved=retrieved,
                    tools_used=tools_used,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )

            results = [
                await self._execute_tool(
                    block, bearer_token, scope_guard, tools_used, retrieved
                )
                for block in response.content
                if block.type == "tool_use"
            ]
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": results})

        raise ToolLoopLimitError(
            f"model exceeded {MAX_TOOL_ITERATIONS} tool iterations"
        )
