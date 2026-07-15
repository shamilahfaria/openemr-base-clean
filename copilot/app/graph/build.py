"""Compile the multi-agent graph.

    START -> supervisor -> {intake | evidence | answer}
             intake  -> supervisor
             evidence -> supervisor
             answer  -> END

The supervisor is re-entered after each worker, so routing is a real loop with
logged decisions, not a fixed pipeline. The store and retriever are injected so
the graph has no global state and is trivially testable.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ..documents.ingest import DocumentStore
from ..rag.retriever import HybridRetriever, default_retriever
from .nodes import answerer, make_evidence_retriever, make_intake_extractor, supervisor
from .state import AgentState


def build_graph(store: DocumentStore, retriever: HybridRetriever | None = None):
    graph = StateGraph(AgentState)
    graph.add_node("supervisor", supervisor)
    graph.add_node("intake", make_intake_extractor(store))
    graph.add_node("evidence", make_evidence_retriever(retriever or default_retriever()))
    graph.add_node("answer", answerer)

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        lambda state: state.next,
        {"intake": "intake", "evidence": "evidence", "answer": "answer"},
    )
    graph.add_edge("intake", "supervisor")
    graph.add_edge("evidence", "supervisor")
    graph.add_edge("answer", END)
    return graph.compile()
