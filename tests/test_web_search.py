"""Unit tests for `SearchProvider`, `TavilyProvider`, and `WebSearchTool`."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from aldwin.core.home import ensure_home
from aldwin.core.settings import SecretsConfig, save_secrets
from aldwin.core.tools import ToolOutput
from aldwin.core.tools.web_search import (
    NO_KEY_MESSAGE,
    SearchProvider,
    SearchProviderError,
    SearchResult,
    TavilyProvider,
    build_web_search_tool,
)


class _SpySearchProvider:
    """In-memory `SearchProvider` recording calls and returning canned results."""

    def __init__(self, results: list[SearchResult] | None = None) -> None:
        self.results = list(results or [])
        self.calls: list[tuple[str, int]] = []

    async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        self.calls.append((query, limit))
        return self.results[:limit]


async def test_web_search_formats_results_and_summary(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-01: `web_search` with mocked Tavily returns numbered list and `3 results`."""
    ensure_home(aldwin_home)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key-12345678")

    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "What's New In Python 3.14",
                        "url": "https://docs.python.org/3.14/whatsnew/3.14.html",
                        "content": "Summary of release\nhighlights and PEP 649.",
                    },
                    {
                        "title": "PEP 745 – Python 3.14 Release Schedule",
                        "url": "https://peps.python.org/pep-0745/",
                        "content": "Release calendar and feature freeze dates.",
                    },
                    {
                        "title": "Python 3.14 Performance Guide",
                        "url": "https://realpython.com/python314/",
                        "content": "Tail-call interpreter and free-threading updates.",
                    },
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        provider = TavilyProvider(
            "tvly-test-key-12345678",
            client=http_client,
        )
        tool = build_web_search_tool(home=aldwin_home, provider=provider)
        res = await tool.call({"query": "Python 3.14 release notes", "limit": 3})

    assert isinstance(res, ToolOutput)
    assert res.ok is True
    assert res.summary == "3 results"

    expected = (
        "1. What's New In Python 3.14 — https://docs.python.org/3.14/whatsnew/3.14.html\n"
        "   Summary of release highlights and PEP 649.\n"
        "2. PEP 745 – Python 3.14 Release Schedule — https://peps.python.org/pep-0745/\n"
        "   Release calendar and feature freeze dates.\n"
        "3. Python 3.14 Performance Guide — https://realpython.com/python314/\n"
        "   Tail-call interpreter and free-threading updates."
    )
    assert str(res) == expected

    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.method == "POST"
    assert str(req.url) == "https://api.tavily.com/search"
    assert req.headers["authorization"] == "Bearer tvly-test-key-12345678"
    body = json.loads(req.content.decode("utf-8"))
    assert body == {
        "query": "Python 3.14 release notes",
        "max_results": 3,
        "search_depth": "basic",
    }


async def test_web_search_without_key_returns_setup_message(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-02: `web_search` with no Tavily key returns setup message without raising."""
    ensure_home(aldwin_home)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    tool = build_web_search_tool(home=aldwin_home)
    res = await tool.call({"query": "latest Python release"})

    assert isinstance(res, ToolOutput)
    assert str(res) == NO_KEY_MESSAGE
    assert res.summary == "not set up"
    assert res.ok is False


async def test_web_search_query_and_limit_boundaries(aldwin_home: Path) -> None:
    """TC-15: `web_search` validates query (1..400 chars) and `limit` (1..10) bounds."""
    ensure_home(aldwin_home)
    spy = _SpySearchProvider(
        [
            SearchResult(
                title=f"Item {i}",
                url=f"https://example.com/{i}",
                snippet=f"Snippet {i}",
            )
            for i in range(1, 12)
        ]
    )
    tool = build_web_search_tool(home=aldwin_home, provider=spy)

    for bad_query in ("", "   \n\t  ", "bad\x00query", "x" * 401):
        out = await tool.call({"query": bad_query})
        assert isinstance(out, ToolOutput)
        assert out.ok is False
        assert out.summary == "invalid query"
        assert "Invalid search query" in str(out)

    for bad_limit in (0, -1, 11):
        out = await tool.call({"query": "valid", "limit": bad_limit})
        assert isinstance(out, ToolOutput)
        assert out.ok is False
        assert out.summary == "invalid limit"
        assert "Invalid search limit" in str(out)

    # Direct forward() check with bool limit.
    bool_out = await tool.forward("valid", True)
    assert bool_out.ok is False
    assert bool_out.summary == "invalid limit"

    assert spy.calls == []

    # Valid boundary: 400-char query, limit=1, limit=10.
    q_400 = "q" * 400
    res_1 = await tool.call({"query": q_400, "limit": 1})
    assert res_1.ok is True
    assert res_1.summary == "1 result"

    res_10 = await tool.call({"query": "ten results", "limit": 10})
    assert res_10.ok is True
    assert res_10.summary == "10 results"
    assert spy.calls == [(q_400, 1), ("ten results", 10)]


async def test_tavily_provider_error_and_timeout_handling(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-16: Errors and timeouts return `search failed: ...` without leaking keys."""
    ensure_home(aldwin_home)
    secret_key = "tvly-supersecret99998888"
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    save_secrets(
        SecretsConfig(tavily_api_key=SecretStr(secret_key)),
        aldwin_home,
    )

    with pytest.raises(ValueError, match="must not be empty"):
        TavilyProvider("   ")

    cases: list[tuple[Any, str]] = [
        (lambda req: httpx.Response(401, text="Unauthorized"), "HTTP 401"),
        (lambda req: httpx.Response(429, text="Rate limit"), "HTTP 429"),
        (lambda req: httpx.Response(503, text="Down"), "HTTP 503"),
        (lambda req: httpx.Response(200, content=b"not-json{"), "invalid JSON"),
        (
            lambda req: httpx.Response(200, json={"unexpected": 123}),
            "unexpected response",
        ),
    ]

    for handler, expected_fragment in cases:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            provider = TavilyProvider(secret_key, client=http_client)
            tool = build_web_search_tool(home=aldwin_home, provider=provider)
            res = await tool.call({"query": "test error"})
            assert res.ok is False
            assert str(res).startswith("search failed: ")
            assert expected_fragment in str(res)
            assert secret_key not in str(res)
            assert secret_key not in res.summary

    # Transport raising ConnectError containing the secret key in the message.
    def raising_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"Failed with token {secret_key}", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(raising_handler)
    ) as http_client:
        provider = TavilyProvider(secret_key, client=http_client)
        tool = build_web_search_tool(home=aldwin_home, provider=provider)
        res_conn = await tool.call({"query": "connect error"})
        assert res_conn.ok is False
        assert secret_key not in str(res_conn)
        assert "[redacted]" in str(res_conn)

    # Timeout in provider / tool wrapper.
    class _SlowProvider:
        async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
            del query, limit
            await asyncio.sleep(5.0)
            return []

    slow_tool = build_web_search_tool(
        home=aldwin_home,
        provider=_SlowProvider(),
        timeout=0.05,
    )
    res_timeout = await slow_tool.call({"query": "slow query"})
    assert res_timeout.ok is False
    assert "timed out" in str(res_timeout)
    assert res_timeout.summary == "search failed: timed out"

    # httpx.TimeoutException inside TavilyProvider.
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(timeout_handler)
    ) as http_client:
        provider_to = TavilyProvider(secret_key, client=http_client)
        with pytest.raises(SearchProviderError, match="request timed out"):
            await provider_to.search("query")


async def test_web_search_zero_and_single_result_and_custom_provider(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-17: `web_search` handles 0 results, 1 result, and custom `SearchProvider`."""
    ensure_home(aldwin_home)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    empty_provider = _SpySearchProvider([])
    assert isinstance(empty_provider, SearchProvider)

    tool_empty = build_web_search_tool(home=aldwin_home, provider=empty_provider)
    res_0 = await tool_empty.call({"query": "nothing matches"})
    assert str(res_0) == "No results found."
    assert res_0.summary == "0 results"
    assert res_0.ok is True

    single_provider = _SpySearchProvider(
        [
            SearchResult(
                title="   ",
                url="https://agentling.dev",
                snippet="Lightweight Python agent framework.",
            )
        ]
    )
    tool_single = build_web_search_tool(home=aldwin_home, provider=single_provider)
    res_1 = await tool_single.call({"query": "agentling"})
    assert str(res_1) == (
        "1. https://agentling.dev — https://agentling.dev\n"
        "   Lightweight Python agent framework."
    )
    assert res_1.summary == "1 result"
    assert res_1.ok is True

    # Also test key resolution from SecretsConfig when passed directly.
    secrets = SecretsConfig(tavily_api_key=SecretStr("tvly-from-secrets-obj"))

    def ok_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tvly-from-secrets-obj"
        return httpx.Response(
            200,
            json={
                "results": [
                    "not-a-dict",
                    {"title": "No URL", "url": ""},
                    {"title": "Valid", "url": "https://example.com", "snippet": "Text"},
                ]
            },
        )

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(ok_handler),
            timeout=kwargs.get("timeout"),
        ),
    )
    tool_from_secrets = build_web_search_tool(home=aldwin_home, secrets=secrets)
    res_sec = await tool_from_secrets.call({"query": "test secrets"})
    assert res_sec.ok is True
    assert res_sec.summary == "1 result"
    assert "1. Valid — https://example.com\n   Text" == str(res_sec)
