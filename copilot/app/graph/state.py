"""Typed graph state (Pydantic) — the supervisor's single source of truth.

Every worker reads and writes this state; the supervisor routes on it. Keeping
it typed and explicit is what makes the graph inspectable rather than a black
box (FR-4.3).
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..documents.schemas import DocumentCitation, LabResult


class RoutingDecision(BaseModel):
    worker: str
    reason: str


class AgentState(BaseModel):
    # inputs
    patient_id: str
    question: str
    # gathered material
    facts: list[LabResult] = Field(default_factory=list)
    citations: list[DocumentCitation] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)      # guideline snippets (Early-sub)
    # outputs
    answer: str = ""
    degraded: bool = False
    # control / audit
    extracted: bool = False
    retrieved: bool = False
    routing: list[RoutingDecision] = Field(default_factory=list)
    next: str = ""
