from langgraph.graph import StateGraph, END

from .state import BountyState
from .nodes import (
    precheck_node,
    dispatcher_node,
    coder_node,
    cicd_specialist_node,
    enqueue_review_node,
)
from ..utils.logger import get_logger

logger = get_logger(__name__)


def route_after_dispatcher(state: BountyState) -> str:
    if state.get("error"):
        return "failed"
    return "coder"


def route_after_coder(state: BountyState) -> str:
    if state.get("error"):
        return "failed"
    return "cicd_specialist"


def route_after_cicd(state: BountyState) -> str:
    if state.get("error"):
        return "failed"
    if state.get("review_approved", False):
        return "enqueue_review"
    return "failed"


def build_graph() -> StateGraph:
    graph = StateGraph(BountyState)

    graph.add_node("precheck", precheck_node)
    graph.add_node("dispatcher", dispatcher_node)
    graph.add_node("coder", coder_node)
    graph.add_node("cicd_specialist", cicd_specialist_node)
    graph.add_node("enqueue_review", enqueue_review_node)

    graph.set_entry_point("precheck")
    graph.add_edge("precheck", "dispatcher")
    graph.add_conditional_edges("dispatcher", route_after_dispatcher, {
        "coder": "coder",
        "failed": END,
    })
    graph.add_conditional_edges("coder", route_after_coder, {
        "cicd_specialist": "cicd_specialist",
        "failed": END,
    })
    graph.add_conditional_edges("cicd_specialist", route_after_cicd, {
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
