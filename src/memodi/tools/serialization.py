"""Serialization boundary between database rows and MCP tool responses.

Allowlist-based, not a denylist: a field only reaches the wire if it is
listed below. Adding a new internal column to a table (a future FK, an
index helper, an embedding variant, ...) stays hidden by default — someone
has to deliberately widen one of these sets to expose it.
"""

_OBSERVATION_SAVE_FIELDS = {
    "id",
    "title",
    "type",
    "topic_key",
    "revision_count",
    "duplicate_count",
    "created_at",
    "updated_at",
    "_deduplicated",
}

_OBSERVATION_READ_FIELDS = {
    "id",
    "type",
    "title",
    "content",
    "topic_key",
    "metadata",
    "occurred_at",
    "created_at",
    "updated_at",
    "revision_count",
    "duplicate_count",
    "project_name",
    "project",
    "rank",
    "similarity",
    "rrf_score",
    "_deduplicated",
    "superseded_by",
    "supersedes",
}

_SESSION_SUMMARY_FIELDS = {
    "id",
    "started_at",
    "ended_at",
    "summary",
    "project",
}

_WORKFLOW_FIELDS = {
    "id",
    "name",
    "phase",
    "objective",
    "acceptance_criteria",
    "tasks",
    "result",
    "created_at",
    "updated_at",
    "completed_at",
    "_scope",
    "_warnings",
}


def _allow(row: dict, fields: set[str]) -> dict:
    return {k: v for k, v in row.items() if k in fields}


def serialize_observation_save(obs: dict) -> dict:
    slim = _allow(obs, _OBSERVATION_SAVE_FIELDS)
    if obs.get("metadata"):
        slim["metadata"] = obs["metadata"]
    return slim


def serialize_observation(obs: dict) -> dict:
    slim = _allow(obs, _OBSERVATION_READ_FIELDS)
    if not obs.get("superseded_by"):
        slim.pop("superseded_by", None)
    if not obs.get("supersedes"):
        slim.pop("supersedes", None)
    return slim


def serialize_observations(observations: list[dict]) -> list[dict]:
    return [serialize_observation(obs) for obs in observations]


def serialize_session_summary(session: dict) -> dict:
    return _allow(session, _SESSION_SUMMARY_FIELDS)


def serialize_workflow(workflow: dict) -> dict:
    return _allow(workflow, _WORKFLOW_FIELDS)
