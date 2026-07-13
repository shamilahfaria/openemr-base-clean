"""
The agent orchestrator (tool-use loop).

The Anthropic client, tool registry, and session store are all injected fakes:
these tests pin loop behavior, scope enforcement, and failure semantics — no
network, no real SDK.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.fhir.client import FhirUnavailableError
from app.orchestrator import (
    MAX_TOOL_ITERATIONS,
    Orchestrator,
    ToolLoopLimitError,
    TurnDraft,
)
from app.scope import PatientScopeGuard, ScopeViolation
from app.sessions import SessionStore

PATIENT = "uuid-pat-1"
OTHER = "uuid-other-patient"
TOKEN = "test-bearer-token-123"
SID = "session-1"


# --- fakes ---------------------------------------------------------------------

def text_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=text)],
    )


def tool_response(name: str, arguments: dict, tool_id: str = "tu_1") -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[SimpleNamespace(type="tool_use", id=tool_id, name=name, input=arguments)],
    )


class FakeAnthropic:
    """Scripted stand-in for the Anthropic SDK client (messages.create)."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.create_calls: list[dict] = []
        outer = self

        async def create(**kwargs):
            outer.create_calls.append(kwargs)
            if isinstance(outer._responses[0], Exception):
                raise outer._responses.pop(0)
            return outer._responses.pop(0)

        self.messages = SimpleNamespace(create=create)


class RecordingTool:
    """Async tool double: records calls, returns canned records or raises."""

    def __init__(self, result: object):
        self._result = result
        self.calls: list[tuple[dict, str]] = []

    async def __call__(self, arguments: dict, bearer_token: str) -> list:
        self.calls.append((arguments, bearer_token))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


MED_RECORD = {"source_id": "med-1", "name": "Morphine sulfate"}


def make_orchestrator(responses: list, tools: dict | None = None, store: SessionStore | None = None):
    client = FakeAnthropic(responses)
    store = store or SessionStore()
    orch = Orchestrator(client, tools or {}, store)
    return orch, client, store


async def run(orch, message="how is her pain?", patient_id=PATIENT, bearer=TOKEN):
    return await orch.run_turn(
        patient_id=patient_id,
        bearer_token=bearer,
        session_id=SID,
        message=message,
        scope_guard=PatientScopeGuard(patient_id),
    )


# --- happy path ------------------------------------------------------------------

class TestSimpleTurn:
    @pytest.mark.anyio
    async def test_text_only_turn_returns_answer(self):
        orch, _client, _store = make_orchestrator([text_response("Pain controlled overnight.")])
        draft = await run(orch)
        assert isinstance(draft, TurnDraft)
        assert draft.answer == "Pain controlled overnight."
        assert draft.tools_used == []
        assert draft.retrieved == []

    @pytest.mark.anyio
    async def test_system_prompt_names_the_active_patient(self):
        orch, client, _store = make_orchestrator([text_response("ok")])
        await run(orch)
        assert PATIENT in client.create_calls[0]["system"]

    @pytest.mark.anyio
    async def test_user_message_is_the_last_message_sent(self):
        orch, client, _store = make_orchestrator([text_response("ok")])
        await run(orch, message="any allergy concerns?")
        messages = client.create_calls[0]["messages"]
        assert messages[-1] == {"role": "user", "content": "any allergy concerns?"}


class TestToolUseLoop:
    @pytest.mark.anyio
    async def test_requested_tool_is_executed_with_model_arguments(self):
        tool = RecordingTool([MED_RECORD])
        orch, _client, _store = make_orchestrator(
            [
                tool_response("get_medications", {"patient_id": PATIENT}),
                text_response("She has one active order."),
            ],
            tools={"get_medications": tool},
        )
        draft = await run(orch)
        assert tool.calls == [({"patient_id": PATIENT}, TOKEN)]
        assert draft.answer == "She has one active order."
        assert draft.tools_used == ["get_medications"]

    @pytest.mark.anyio
    async def test_retrieved_records_are_collected_for_the_verifier(self):
        tool = RecordingTool([MED_RECORD])
        orch, _client, _store = make_orchestrator(
            [
                tool_response("get_medications", {"patient_id": PATIENT}),
                text_response("done"),
            ],
            tools={"get_medications": tool},
        )
        draft = await run(orch)
        assert draft.retrieved == [MED_RECORD]

    @pytest.mark.anyio
    async def test_tool_result_is_returned_to_the_model(self):
        tool = RecordingTool([MED_RECORD])
        orch, client, _store = make_orchestrator(
            [
                tool_response("get_medications", {"patient_id": PATIENT}, tool_id="tu_42"),
                text_response("done"),
            ],
            tools={"get_medications": tool},
        )
        await run(orch)
        followup = client.create_calls[1]["messages"][-1]
        assert followup["role"] == "user"
        (block,) = followup["content"]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "tu_42"

    @pytest.mark.anyio
    async def test_chained_tool_calls_all_execute(self):
        meds = RecordingTool([MED_RECORD])
        allergies = RecordingTool([{"source_id": "alg-1", "substance": "Penicillin"}])
        orch, _client, _store = make_orchestrator(
            [
                tool_response("get_medications", {"patient_id": PATIENT}, "tu_1"),
                tool_response("get_allergies", {"patient_id": PATIENT}, "tu_2"),
                text_response("no conflicts found"),
            ],
            tools={"get_medications": meds, "get_allergies": allergies},
        )
        draft = await run(orch)
        assert meds.calls and allergies.calls
        assert draft.tools_used == ["get_medications", "get_allergies"]
        assert len(draft.retrieved) == 2

    @pytest.mark.anyio
    async def test_runaway_tool_loop_is_capped(self):
        tool = RecordingTool([MED_RECORD])
        endless = [
            tool_response("get_medications", {"patient_id": PATIENT}, f"tu_{i}")
            for i in range(MAX_TOOL_ITERATIONS + 5)
        ]
        orch, _client, _store = make_orchestrator(endless, tools={"get_medications": tool})
        with pytest.raises(ToolLoopLimitError):
            await run(orch)
        assert len(tool.calls) <= MAX_TOOL_ITERATIONS


class TestMultiTurn:
    @pytest.mark.anyio
    async def test_prior_turns_are_sent_to_the_model(self):
        store = SessionStore()
        orch, client, _ = make_orchestrator(
            [text_response("first answer"), text_response("second answer")],
            store=store,
        )
        await run(orch, message="first question")
        await run(orch, message="second question")
        messages = client.create_calls[1]["messages"]
        assert messages[0] == {"role": "user", "content": "first question"}
        assert messages[1] == {"role": "assistant", "content": "first answer"}
        assert messages[-1] == {"role": "user", "content": "second question"}

    @pytest.mark.anyio
    async def test_turn_is_appended_to_the_session(self):
        store = SessionStore()
        orch, _client, _ = make_orchestrator([text_response("the answer")], store=store)
        await run(orch, message="the question")
        assert store.history(SID, PATIENT) == [
            {"role": "user", "content": "the question"},
            {"role": "assistant", "content": "the answer"},
        ]


class TestScopeEnforcement:
    @pytest.mark.anyio
    async def test_out_of_scope_tool_call_is_not_executed(self):
        tool = RecordingTool([MED_RECORD])
        orch, _client, _store = make_orchestrator(
            [
                tool_response("get_medications", {"patient_id": OTHER}),
                text_response("cannot access that patient"),
            ],
            tools={"get_medications": tool},
        )
        draft = await run(orch)
        assert tool.calls == []            # the S1 boundary held
        assert draft.tools_used == []
        assert draft.retrieved == []

    @pytest.mark.anyio
    async def test_out_of_scope_call_returns_error_tool_result_to_model(self):
        tool = RecordingTool([MED_RECORD])
        orch, client, _store = make_orchestrator(
            [
                tool_response("get_medications", {"patient_id": OTHER}, "tu_9"),
                text_response("cannot access that patient"),
            ],
            tools={"get_medications": tool},
        )
        await run(orch)
        (block,) = client.create_calls[1]["messages"][-1]["content"]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "tu_9"
        assert block["is_error"] is True


class TestFailureSemantics:
    @pytest.mark.anyio
    async def test_unknown_tool_name_becomes_error_result_not_a_crash(self):
        orch, client, _store = make_orchestrator(
            [
                tool_response("drop_all_tables", {"patient_id": PATIENT}, "tu_7"),
                text_response("that tool does not exist"),
            ],
        )
        draft = await run(orch)
        assert draft.answer == "that tool does not exist"
        (block,) = client.create_calls[1]["messages"][-1]["content"]
        assert block["is_error"] is True

    @pytest.mark.anyio
    async def test_tool_failure_becomes_error_result_and_loop_continues(self):
        tool = RecordingTool(FhirUnavailableError("down"))
        orch, client, _store = make_orchestrator(
            [
                tool_response("get_medications", {"patient_id": PATIENT}, "tu_8"),
                text_response("could not retrieve medications"),
            ],
            tools={"get_medications": tool},
        )
        draft = await run(orch)
        assert draft.answer == "could not retrieve medications"
        assert draft.tools_used == []      # failed executions don't count as used
        (block,) = client.create_calls[1]["messages"][-1]["content"]
        assert block["is_error"] is True

    @pytest.mark.anyio
    async def test_anthropic_api_error_propagates(self):
        orch, _client, _store = make_orchestrator([RuntimeError("api down")])
        with pytest.raises(RuntimeError):
            await run(orch)

    @pytest.mark.anyio
    async def test_blank_message_fails_closed_before_any_model_call(self):
        orch, client, _store = make_orchestrator([text_response("never sent")])
        with pytest.raises(ValueError):
            await run(orch, message="   ")
        assert client.create_calls == []

    @pytest.mark.anyio
    async def test_blank_bearer_fails_closed_before_any_model_call(self):
        orch, client, _store = make_orchestrator([text_response("never sent")])
        with pytest.raises(ValueError):
            await run(orch, bearer="  ")
        assert client.create_calls == []

    @pytest.mark.anyio
    async def test_failed_turn_does_not_pollute_session_history(self):
        store = SessionStore()
        orch, _client, _ = make_orchestrator([RuntimeError("api down")], store=store)
        with pytest.raises(RuntimeError):
            await run(orch)
        assert store.history(SID, PATIENT) == []
