import json

from memodi.database import graph_repository
from memodi.database.connection import ensure_schema
from memodi.database.graph import ensure_graph
from memodi.tools.errors import handle_errors
from memodi.tools.scope import require_workspace


def _ensure() -> None:
    ensure_schema()
    ensure_graph()


@handle_errors
def relate(
    from_type: str,
    from_name: str,
    to_type: str,
    to_name: str,
    relation: str,
    properties: dict | None = None,
    valid_at: str | None = None,
) -> str:
    _ensure()
    graph_repository.add_edge(
        from_label=from_type,
        from_name=from_name,
        to_label=to_type,
        to_name=to_name,
        edge_label=relation,
        properties=properties,
        valid_at=valid_at,
    )
    return json.dumps(
        {"created": True, "relation": relation, "from": from_name, "to": to_name},
        default=str,
    )


@handle_errors
def dependencies(
    name: str,
    user_id: str | None = None,
    machine: str | None = None,
    path: str | None = None,
) -> str:
    _ensure()
    deps = graph_repository.get_dependencies(name)
    dependents = graph_repository.get_dependents(name)
    result = {"depends_on": deps, "depended_by": dependents}
    if path is not None:
        workspace = require_workspace(user_id, machine, path)
        result["links_to"] = graph_repository.get_topic_links_out(
            workspace["id"], name
        )
        result["linked_from"] = graph_repository.get_topic_links_in(
            workspace["id"], name
        )
    return json.dumps(result, default=str)


@handle_errors
def impact_analysis(
    name: str,
    max_depth: int = 5,
    user_id: str | None = None,
    machine: str | None = None,
    path: str | None = None,
) -> str:
    _ensure()
    workspace_id = None
    if path is not None:
        workspace_id = require_workspace(user_id, machine, path)["id"]
    affected = graph_repository.get_impact(name, max_depth, workspace_id=workspace_id)
    return json.dumps(
        {"target": name, "affected": affected, "depth": max_depth}, default=str
    )


@handle_errors
def graph_overview() -> str:
    _ensure()
    overview = graph_repository.get_graph_overview()
    return json.dumps(overview, default=str)


@handle_errors
def remove_relation(from_name: str, to_name: str, relation: str) -> str:
    """Soft delete: marks relationship as invalid (sets invalid_at)."""
    _ensure()
    invalidated = graph_repository.remove_edge(from_name, to_name, relation)
    return json.dumps(
        {
            "invalidated": invalidated,
            "from": from_name,
            "to": to_name,
            "relation": relation,
        }
    )


@handle_errors
def delete_relation(from_name: str, to_name: str, relation: str) -> str:
    """Hard delete: physically removes all relationships of this type between nodes."""
    _ensure()
    deleted = graph_repository.hard_delete_edge(from_name, to_name, relation)
    return json.dumps(
        {"deleted": deleted, "from": from_name, "to": to_name, "relation": relation}
    )
