"""Host integration tests for document and download row summaries."""

from __future__ import annotations

import shutil
from pathlib import Path

import httpx
import pytest
from conftest import auto_approve
from fakes.model import FakeModel, FakeTurn

from aldwin.client import AldwinClient
from aldwin.core.agent import build_agent
from aldwin.core.tools import download as download_mod
from aldwin.host import AldwinHost, InProcessTransport
from aldwin.protocol import ApprovalEvent, ToolCallEvent, ToolResultEvent

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "docs"


@pytest.fixture
async def docs_host(
    fake_model: FakeModel,
    aldwin_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """In-process host with a temp workspace, fake model, and mocked download HTTP."""
    root = tmp_path / "ws"
    root.mkdir()
    shutil.copy(FIXTURES / "bill.pdf", root / "bill.pdf")
    real_build = build_agent

    def _build(config, secrets, ask, **kwargs):
        kwargs["model"] = fake_model
        return real_build(config, secrets, ask, **kwargs)

    monkeypatch.setattr("aldwin.host.runner.build_agent", _build)

    payload = b"%PDF-demo"
    headers = {
        "Content-Type": "application/pdf",
        "Content-Disposition": 'attachment; filename="report.pdf"',
        "Content-Length": str(len(payload)),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers, request=request)
        return httpx.Response(200, headers=headers, content=payload, request=request)

    client_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def public_resolver(host: str, port: int) -> list[str]:
        del host, port
        return ["93.184.216.34"]

    real_build_download = download_mod.build_download_tool

    def _build_download(workspace, policy_session, **kwargs):
        kwargs.setdefault("client", client_http)
        kwargs.setdefault("resolver", public_resolver)
        return real_build_download(workspace, policy_session, **kwargs)

    monkeypatch.setattr(download_mod, "build_download_tool", _build_download)

    transport = InProcessTransport()
    host = AldwinHost(transport, home=aldwin_home, workspace=root)
    await host.start()
    client = AldwinClient(transport.connect())
    await client.connect()
    try:
        yield client, fake_model, root
    finally:
        await client.close()
        await host.stop_host()
        await client_http.aclose()


async def test_host_rows_read_then_download(
    docs_host: tuple[AldwinClient, FakeModel, Path],
) -> None:
    """Host emits Read/Download pending and result summaries; download not grantable."""
    client, model, root = docs_host
    model.add_turns(
        FakeTurn.tool_call("read_document", {"path": "bill.pdf"}, call_id="c_read"),
        FakeTurn.tool_call(
            "download_file",
            {"url": "https://example.com/report.pdf"},
            call_id="c_dl",
        ),
        FakeTurn.text(["done"]),
    )
    events = await auto_approve(client, client.turn("go"))
    calls = [e for e in events if isinstance(e, ToolCallEvent)]
    results = [e for e in events if isinstance(e, ToolResultEvent)]
    approvals = [e for e in events if isinstance(e, ApprovalEvent)]

    assert any(
        c.name == "read_document" and c.summary == "Read bill.pdf" for c in calls
    )
    assert any(
        c.name == "download_file" and c.summary.startswith("Download ") for c in calls
    )
    assert any(r.summary.startswith("Read bill.pdf · pages ") for r in results if r.ok)
    assert any(r.summary.startswith("Download report.pdf · ") for r in results if r.ok)
    assert any(a.tool == "download_file" and a.grantable is False for a in approvals)
    assert (root / "downloads" / "report.pdf").exists()
