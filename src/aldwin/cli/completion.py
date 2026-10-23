"""Merged slash + skill completers for the Aldwin chat prompt."""

from __future__ import annotations

from collections.abc import Iterable

from prompt_toolkit.completion import (
    CompleteEvent,
    Completer,
    Completion,
    merge_completers,
)
from prompt_toolkit.document import Document

from aldwin.cli.commands import SlashCompleter
from aldwin.cli.skill_completer import SkillCache, SkillCompleter


def build_chat_completer(
    cache: SkillCache,
    *,
    enable_skills: bool = True,
) -> Completer:
    """Return slash-only or slash+`@` merged completer for the chat prompt."""
    slash = SlashCompleter()
    if not enable_skills:
        return slash
    return merge_completers([slash, SkillCompleter(cache)])


class RefreshableCompleter(Completer):
    """Completer wrapper that can swap the inner completer after hello negotiation."""

    def __init__(self, inner: Completer) -> None:
        self.inner = inner

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterable[Completion]:
        return self.inner.get_completions(document, complete_event)
