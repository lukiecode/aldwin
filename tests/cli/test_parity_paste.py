"""Paste collapse / expand / resolve."""

from __future__ import annotations

from aldwin.cli.paste import PasteStore


def test_short_paste_not_collapsed() -> None:
    store = PasteStore()
    assert not store.should_collapse("hi")
    assert not store.should_collapse("a\nb")
    assert store.collapse("hi") == "hi"
    assert store.expand_last() is None


def test_collapse_expand_resolve() -> None:
    store = PasteStore()
    body = "line1\nline2\nline3\nline4"
    assert store.should_collapse(body)
    token = store.collapse(body)
    assert "Pasted text #1" in token
    assert store.expand_last() == body
    assert store.resolve(f"prefix {token} suffix") == f"prefix {body} suffix"
    assert store.resolve("no tokens here") == "no tokens here"
