"""`SearchProvider` protocol, `TavilyProvider`, and `web_search` tool (`FR-1`)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

import httpx
from agentling import Tool

from aldwin.core.settings import SecretsConfig, load_secrets, resolve_key
from aldwin.core.tools import ToolOutput

TAVILY_SEARCH_URL: Final[str] = "https://api.tavily.com/search"
SEARCH_TIMEOUT_SECONDS: Final[float] = 15.0
MAX_QUERY_CHARS: Final[int] = 400
MIN_LIMIT: Final[int] = 1
MAX_LIMIT: Final[int] = 10
DEFAULT_LIMIT: Final[int] = 5

NO_KEY_MESSAGE: Final[str] = (
    "Web search isn't set up. Run `aldwin setup` to add a Tavily key."
)


@dataclass(frozen=True, slots=True)
class SearchResult:
    """A single web search result (`FR-1`)."""

    title: str
    url: str
    snippet: str


class SearchProviderError(Exception):
    """Raised by a `SearchProvider` when a search request fails."""


@runtime_checkable
class SearchProvider(Protocol):
    """Protocol for pluggable web search backends (`FR-1`)."""

    async def search(
        self,
        query: str,
        limit: int = DEFAULT_LIMIT,
    ) -> list[SearchResult]:
        """Return up to `limit` search results for `query`."""
        ...


def _scrub_key(text: str, api_key: str) -> str:
    """Remove `api_key` from `text` if present and collapse whitespace."""
    cleaned = text.replace(api_key, "[redacted]") if api_key else text
    return " ".join(cleaned.split())


class TavilyProvider:
    """Tavily Search API implementation of `SearchProvider` using `httpx` (`FR-1`)."""

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = SEARCH_TIMEOUT_SECONDS,
        endpoint: str = TAVILY_SEARCH_URL,
    ) -> None:
        cleaned_key = api_key.strip()
        if not cleaned_key:
            raise ValueError("Tavily API key must not be empty.")
        self._api_key = cleaned_key
        self._client = client
        self._timeout = timeout
        self._endpoint = endpoint

    async def _post_search(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
    ) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        return await client.post(
            self._endpoint,
            headers=headers,
            json=payload,
            timeout=self._timeout,
        )

    async def search(
        self,
        query: str,
        limit: int = DEFAULT_LIMIT,
    ) -> list[SearchResult]:
        """Query Tavily's basic search endpoint and map results (`FR-1`)."""
        payload: dict[str, Any] = {
            "query": query,
            "max_results": limit,
            "search_depth": "basic",
        }
        try:
            if self._client is not None:
                response = await self._post_search(self._client, payload)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await self._post_search(client, payload)
        except httpx.TimeoutException as exc:
            raise SearchProviderError("request timed out") from exc
        except httpx.HTTPError as exc:
            reason = _scrub_key(str(exc), self._api_key) or type(exc).__name__
            raise SearchProviderError(reason) from exc

        if response.status_code in {401, 403}:
            raise SearchProviderError(
                f"invalid or unauthorized Tavily API key (HTTP {response.status_code})"
            )
        if response.status_code == 429:
            raise SearchProviderError(
                "Tavily rate limit or monthly credit limit reached (HTTP 429)"
            )
        if response.status_code >= 400:
            raise SearchProviderError(f"HTTP {response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise SearchProviderError("invalid JSON response from Tavily") from exc

        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise SearchProviderError("unexpected response format from Tavily")

        results: list[SearchResult] = []
        for raw in data["results"][:limit]:
            if not isinstance(raw, dict):
                continue
            url = str(raw.get("url") or "").strip()
            if not url:
                continue
            title = " ".join(str(raw.get("title") or "").split()) or url
            snippet = " ".join(
                str(raw.get("content") or raw.get("snippet") or "").split()
            )
            results.append(SearchResult(title=title, url=url, snippet=snippet))
        return results


def _resolve_tavily_key(
    home: Path | None,
    secrets: SecretsConfig | None,
) -> str | None:
    """Resolve the Tavily API key from environment first, then `secrets` or disk."""
    resolved = resolve_key("tavily", home)
    if resolved.key:
        return resolved.key
    if secrets is not None:
        direct = secrets.get_secret("tavily")
        if direct:
            return direct
    if home is not None:
        loaded = load_secrets(home)
        return loaded.get_secret("tavily")
    return None


class WebSearchTool(Tool):
    """Search the web and return numbered results with titles, URLs, and snippets."""

    name = "web_search"
    description = (
        "Search the web for current information and return up to 10 numbered "
        "results with title, URL, and snippet."
    )
    parallel_safe = True
    timeout = None
    max_output_chars = 10_000
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (1-400 characters).",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return (1-10, default 5).",
                "default": DEFAULT_LIMIT,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        home: Path | None = None,
        secrets: SecretsConfig | None = None,
        *,
        provider: SearchProvider | None = None,
        timeout: float = SEARCH_TIMEOUT_SECONDS,
    ) -> None:
        self._home = home
        self._secrets = secrets
        self._provider = provider
        self._timeout = timeout

    async def forward(  # type: ignore[override]
        self,
        query: str,
        limit: int = DEFAULT_LIMIT,
    ) -> ToolOutput:
        if not isinstance(query, str) or "\x00" in query:
            return ToolOutput(
                "Invalid search query: query must be a valid string.",
                summary="invalid query",
                ok=False,
            )

        cleaned_query = " ".join(query.split())
        if not cleaned_query or len(cleaned_query) > MAX_QUERY_CHARS:
            return ToolOutput(
                f"Invalid search query: must be between 1 and {MAX_QUERY_CHARS} "
                f"characters (got {len(cleaned_query)}).",
                summary="invalid query",
                ok=False,
            )

        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < MIN_LIMIT
            or limit > MAX_LIMIT
        ):
            return ToolOutput(
                f"Invalid search limit: must be an integer between {MIN_LIMIT} and "
                f"{MAX_LIMIT} (got {limit!r}).",
                summary="invalid limit",
                ok=False,
            )

        api_key: str | None = None
        provider = self._provider
        if provider is None:
            api_key = _resolve_tavily_key(self._home, self._secrets)
            if not api_key:
                return ToolOutput(
                    NO_KEY_MESSAGE,
                    summary="not set up",
                    ok=False,
                )
            provider = TavilyProvider(api_key, timeout=self._timeout)

        try:
            results = await asyncio.wait_for(
                provider.search(cleaned_query, limit),
                timeout=self._timeout,
            )
        except TimeoutError:
            return ToolOutput(
                f"search failed: request timed out after {self._timeout:g}s",
                summary="search failed: timed out",
                ok=False,
            )
        except Exception as exc:
            reason = _scrub_key(str(exc), api_key or "") or type(exc).__name__
            return ToolOutput(
                f"search failed: {reason}",
                summary=f"search failed: {reason}",
                ok=False,
            )

        if not results:
            return ToolOutput(
                "No results found.",
                summary="0 results",
                ok=True,
            )

        formatted: list[str] = []
        for idx, item in enumerate(results[:limit], start=1):
            title = " ".join(item.title.split()) or item.url.strip()
            url = item.url.strip()
            snippet = " ".join(item.snippet.split())
            formatted.append(f"{idx}. {title} — {url}\n   {snippet}")

        count = len(formatted)
        summary = "1 result" if count == 1 else f"{count} results"
        return ToolOutput(
            "\n".join(formatted),
            summary=summary,
            ok=True,
        )


def build_web_search_tool(
    home: Path | None = None,
    secrets: SecretsConfig | None = None,
    *,
    provider: SearchProvider | None = None,
    timeout: float = SEARCH_TIMEOUT_SECONDS,
) -> WebSearchTool:
    """Construct the `WebSearchTool` (`FR-2`)."""
    return WebSearchTool(
        home=home,
        secrets=secrets,
        provider=provider,
        timeout=timeout,
    )


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "MAX_QUERY_CHARS",
    "MIN_LIMIT",
    "NO_KEY_MESSAGE",
    "SEARCH_TIMEOUT_SECONDS",
    "SearchProvider",
    "SearchProviderError",
    "SearchResult",
    "TAVILY_SEARCH_URL",
    "TavilyProvider",
    "WebSearchTool",
    "build_web_search_tool",
]
