"""Tests for the `@` skill completer (ENG-432)."""

from __future__ import annotations

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from aldwin.cli.skill_completer import (
    NO_MATCH_TMPL,
    SkillCache,
    SkillCompleter,
    extract_skill_tokens,
)
from aldwin.protocol import SkillCatalogItem


def _item(
    name: str,
    description: str = "",
    *,
    last_used: str | None = None,
    flags: list[str] | None = None,
) -> SkillCatalogItem:
    return SkillCatalogItem(
        name=name,
        description=description or f"{name} Load when testing.",
        source="default",
        flags=flags or [],
        last_used=last_used,
    )


def _completions(text: str, cache: SkillCache) -> list[str]:
    completer = SkillCompleter(cache)
    doc = Document(text, len(text))
    return [
        c.display[0][1] if isinstance(c.display, list) else str(c.display)
        for c in completer.get_completions(doc, CompleteEvent())
    ]


def test_at_start_opens_list_recent_first() -> None:
    cache = SkillCache(
        items=[
            _item("memory", last_used="2026-01-01T00:00:00+00:00"),
            _item("files", last_used="2026-10-01T00:00:00+00:00"),
            _item("terminal"),
        ]
    )
    displays = _completions("@", cache)
    assert displays[0] == "files"
    assert "memory" in displays
    assert "terminal" in displays


def test_email_does_not_open_list() -> None:
    cache = SkillCache(items=[_item("files")])
    assert _completions("email fola@cuvama.com", cache) == []


def test_mid_word_at_no_trigger() -> None:
    cache = SkillCache(items=[_item("files")])
    assert _completions("foo@bar", cache) == []


def test_after_whitespace_triggers() -> None:
    cache = SkillCache(items=[_item("files")])
    assert "files" in _completions("tidy @", cache)


def test_prefix_filter_fi() -> None:
    cache = SkillCache(items=[_item("files"), _item("memory"), _item("terminal")])
    displays = _completions("@fi", cache)
    assert displays[0] == "files"


def test_description_substring() -> None:
    cache = SkillCache(
        items=[
            _item("markdown-notes", "How to work with notes Load when."),
            _item("files", "File tools Load when."),
        ]
    )
    displays = _completions("@notes", cache)
    assert "markdown-notes" in displays


def test_no_match_row() -> None:
    cache = SkillCache(items=[_item("files")])
    displays = _completions("@xyz", cache)
    assert displays == [NO_MATCH_TMPL.format(text="xyz")]


def test_hides_error_skills_and_caps_eight() -> None:
    items = [_item(f"skill-{i:02d}") for i in range(12)]
    items.append(_item("broken", flags=["error: missing description"]))
    cache = SkillCache(items=items)
    displays = _completions("@", cache)
    assert len(displays) == 8
    assert "broken" not in displays


def test_extract_skill_tokens_order() -> None:
    known = {"files", "memory"}
    text = "A @files B @not-a-skill C @memory"
    assert extract_skill_tokens(text, known) == ["files", "memory"]
