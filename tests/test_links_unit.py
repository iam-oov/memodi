"""Unit tests for memodi.tools.memory.parse_links that don't require a DB
connection."""

from __future__ import annotations

import os

# memodi.config builds Settings() at import time and requires DB env vars.
# These unit tests don't touch the DB, so dummy values are enough to let
# the import chain resolve.
os.environ.setdefault("MEMODI_DB_USER", "test_user")
os.environ.setdefault("MEMODI_DB_PASSWORD", "test_password")

from memodi.database.graph_repository import TOPIC_LINK_KEY_RE
from memodi.tools.memory import MAX_LINKS, parse_links


def test_parse_links_extracts_valid_keys():
    links, skipped = parse_links(
        "depends on [[infra/hnsw-index-bloat]] and [[architecture/related-on-save]]",
        None,
    )
    assert links == ["infra/hnsw-index-bloat", "architecture/related-on-save"]
    assert skipped == 0


def test_parse_links_accepts_the_three_real_wild_keys():
    content = (
        "backlog item #1 of [[project/backlog]], see also "
        "[[infra/hnsw-index-bloat]] and [[architecture/related-on-save]]"
    )
    links, skipped = parse_links(content, None)
    assert links == [
        "project/backlog",
        "infra/hnsw-index-bloat",
        "architecture/related-on-save",
    ]
    assert skipped == 0


def test_parse_links_strips_whitespace():
    links, skipped = parse_links("[[ spaced-key ]]", None)
    assert links == ["spaced-key"]
    assert skipped == 0


def test_parse_links_rejects_injection_charset():
    """THE injection pin: none of these chars may ever reach a Cypher
    string, since AGE has no parameterized queries."""
    content = "[[a'b]] [[c$d]] [[e\\f]] [[g h]] [[i\"j]]"
    links, skipped = parse_links(content, None)
    assert links == []
    assert skipped == 5


def test_parse_links_rejects_key_over_128_chars():
    long_key = "a" * 129
    links, skipped = parse_links(f"[[{long_key}]]", None)
    assert links == []
    assert skipped == 1


def test_parse_links_accepts_key_at_128_chars():
    key = "a" * 128
    links, skipped = parse_links(f"[[{key}]]", None)
    assert links == [key]
    assert skipped == 0


def test_parse_links_drops_self_link():
    links, skipped = parse_links("[[my/own-key]] and [[other/key]]", "my/own-key")
    assert links == ["other/key"]
    assert skipped == 0


def test_parse_links_dedups_first_wins():
    links, skipped = parse_links("[[a/b]] [[a/b]] [[a/b]]", None)
    assert links == ["a/b"]
    assert skipped == 0


def test_parse_links_caps_at_max_links():
    content = " ".join(f"[[key/{i}]]" for i in range(MAX_LINKS + 5))
    links, skipped = parse_links(content, None)
    assert len(links) == MAX_LINKS
    assert links == [f"key/{i}" for i in range(MAX_LINKS)]
    assert skipped == 0


def test_parse_links_empty_content():
    links, skipped = parse_links("", None)
    assert links == []
    assert skipped == 0


def test_parse_links_linkless_content():
    links, skipped = parse_links("just plain text, no wiki links here", None)
    assert links == []
    assert skipped == 0


def test_parse_links_empty_brackets_ignored():
    links, skipped = parse_links("see [[]] here", None)
    assert links == []
    assert skipped == 0


def test_parse_links_nested_brackets_extract_innermost():
    links, skipped = parse_links("[[a[[b]]c]]", None)
    assert links == ["b"]
    assert skipped == 0


# --- TOPIC_LINK_KEY_RE, the invariant every Cypher interpolation relies on ---


def test_topic_link_key_re_is_end_anchored():
    """`$` also matches just before a final newline, which would let a key
    smuggle one past every guard site into an interpolated Cypher string."""
    assert TOPIC_LINK_KEY_RE.match("probe/newline\n") is None


def test_topic_link_key_re_accepts_the_documented_charset():
    for key in ("a", "0", "project/backlog", "infra/hnsw-index-bloat", "a.b_c-d/e"):
        assert TOPIC_LINK_KEY_RE.match(key) is not None


def test_topic_link_key_re_rejects_a_leading_separator():
    for key in ("/leading", ".leading", "-leading", "_leading"):
        assert TOPIC_LINK_KEY_RE.match(key) is None
