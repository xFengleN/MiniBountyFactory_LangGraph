from langgraph.graph import StateGraph, END

from .state import BountyState
from .nodes import (
    precheck_node,
    classify_node,
    simple_agent_node,
    complex_agent_node,
    validate_node,
    review_node,
    enqueue_review_node,
)
from ..utils.logger import get_logger

logger = get_logger(__name__)


def route_by_complexity(state: BountyState) -> str:
    classification = state.get("classification", "").lower()
    if classification == "simple":
        return "simple_agent"
    return "complex_agent"


def route_after_agent(state: BountyState) -> str:
    if state.get("error"):
        return "failed"
    return "validate"


def route_by_validation(state: BountyState) -> str:
    if state.get("validation_passed", False):
        return "review"
    return "failed"


def route_by_review(state: BountyState) -> str:
    if state.get("review_approved", False):
        return "enqueue_review"
    return "failed"


def build_graph() -> StateGraph:
    graph = StateGraph(BountyState)

    graph.add_node("precheck", precheck_node)
    graph.add_node("classify", classify_node)
    graph.add_node("simple_agent", simple_agent_node)
    graph.add_node("complex_agent", complex_agent_node)
    graph.add_node("validate", validate_node)
    graph.add_node("review", review_node)
    graph.add_node("enqueue_review", enqueue_review_node)

    graph.set_entry_point("precheck")
    graph.add_edge("precheck", "classify")
    graph.add_conditional_edges("classify", route_by_complexity, {
        "simple_agent": "simple_agent",
        "complex_agent": "complex_agent",
    })
    graph.add_conditional_edges("simple_agent", route_after_agent, {
        "validate": "validate",
        "failed": END,
    })
    graph.add_conditional_edges("complex_agent", route_after_agent, {
        "validate": "validate",
        "failed": END,
    })
    graph.add_conditional_edges("validate", route_by_validation, {
        "review": "review",
        "failed": END,
    })
    graph.add_conditional_edges("review", route_by_review, {
        "enqueue_review": "enqueue_review",
        "failed": END,
    })
    graph.add_edge("enqueue_review", END)

    return graph


_compiled_graph = None


def get_compiled_graph():
    global _compiled_graph
    if _compiled_graph is None:
        from langgraph.checkpoint.memory import MemorySaver
        _compiled_graph = build_graph().compile(checkpointer=MemorySaver())
        logger.info("LangGraph compiled and ready")
    return _compiled_graph
