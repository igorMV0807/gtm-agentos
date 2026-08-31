from typing import Literal

from app.core.exceptions import AgentRouteInvalidError
from app.schemas.orchestration import AgentRoute
from app.schemas.qualification import LeadClassification


RouteNode = Literal["research_state", "nurture_state", "stop_state"]


_CLASSIFICATION_ROUTES = {
    LeadClassification.HOT: AgentRoute.RESEARCH,
    LeadClassification.WARM: AgentRoute.NURTURE,
    LeadClassification.COLD: AgentRoute.STOP,
}

_ROUTE_NODES: dict[AgentRoute, RouteNode] = {
    AgentRoute.RESEARCH: "research_state",
    AgentRoute.NURTURE: "nurture_state",
    AgentRoute.STOP: "stop_state",
}


def select_route(classification: LeadClassification) -> AgentRoute:
    try:
        return _CLASSIFICATION_ROUTES[classification]
    except KeyError as exc:
        raise AgentRouteInvalidError("Unsupported classification route") from exc


def route_to_node(route: AgentRoute | str) -> RouteNode:
    try:
        normalized_route = AgentRoute(route)
        return _ROUTE_NODES[normalized_route]
    except (KeyError, ValueError) as exc:
        raise AgentRouteInvalidError("Unsupported agent route") from exc
