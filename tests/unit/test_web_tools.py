"""Web tools: URL safety, HTML-to-text, and which agent may use them."""

from __future__ import annotations

import pytest
from test_agent_turn import make_agent

from docsmind.agent.tools import build_registry
from docsmind.agent.tools.web import _assert_public, _check_url, _html_to_text
from docsmind.config import DocsmindConfig
from docsmind.domain.errors import ToolInputError


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/x",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://user:pass@example.com/",
        "https://example.com:8443/",
        "not a url",
    ],
)
def test_check_url_refuses_unsafe_urls(url):
    with pytest.raises(ToolInputError):
        _check_url(url)


def test_check_url_accepts_plain_https():
    assert _check_url("https://csrc.nist.gov/pubs/sp/800/171/r3/final") == "https://csrc.nist.gov/pubs/sp/800/171/r3/final"


@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.5", "192.168.1.10", "169.254.169.254", "::1", "::ffff:127.0.0.1"])
async def test_private_and_local_addresses_are_refused(host):
    with pytest.raises(ToolInputError):
        await _assert_public(host)


async def test_public_address_is_allowed():
    await _assert_public("93.184.216.34")


def test_html_to_text_keeps_title_and_links_and_drops_scripts():
    html = (
        "<html><head><title> SP 800-171 Rev. 3 </title><style>p{}</style></head><body>"
        "<script>alert(1)</script><h1>Revision history</h1>"
        '<p>See the <a href="/changes.pdf">redline</a> and <a href="#top">top</a>.</p>'
        "<ul><li>May 2024: final</li><li>Nov 2023: draft</li></ul></body></html>"
    )
    title, text = _html_to_text(html, "https://example.org/pubs/x")
    assert title == "SP 800-171 Rev. 3"
    assert "alert" not in text and "p{}" not in text
    assert "redline (https://example.org/changes.pdf)" in text
    assert "top (" not in text  # in-page anchors carry no useful URL
    assert "Revision history\n" in text
    assert "May 2024: final\nNov 2023: draft" in text


def test_web_tools_are_core_tools_of_every_agent(config, llm, repo):
    registry = build_registry()
    core = {"web_search", "read_url", "page_vision", "load_table", "create_file"}
    assert core <= set(registry.names())
    assert core <= set(registry.for_executor().names())  # the Mind gets them too

    info = make_agent(config, llm, repo).describe()
    assert "read_url" in {t["name"] for t in info["agent"]["tools"]}
    by_name = {s["name"]: {t["name"] for t in s["tools"]} for s in info["subagents"]}
    assert set(by_name) == {"change_analyzer"}
    assert {"web_search", "read_url"} <= by_name["change_analyzer"]


def test_web_search_is_offered_only_when_configured():
    registry = build_registry().subset(["search", "web_search", "read_url"])
    off = DocsmindConfig(_env_file=None)
    on = DocsmindConfig(_env_file=None, web_search_api_key="tvly-test")
    assert set(registry.configured(off).names()) == {"search", "read_url"}
    assert set(registry.configured(on).names()) == {"search", "web_search", "read_url"}


# -- read_url: HTML, text, PDF, Word ------------------------------------------------------------------------


def _pdf(pages: list[str]) -> bytes:
    import pymupdf

    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    return document.tobytes()


def _docx() -> bytes:
    import io

    from docx import Document

    document = Document()
    document.add_heading("Revision history", level=1)
    document.add_paragraph("Version 2 adds encryption of backups.")
    table = document.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Clause", "Change"
    table.rows[1].cells[0].text, table.rows[1].cells[1].text = "5.2", "Backups must be encrypted"
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _serve(monkeypatch, content_type: str, data: bytes):
    from docsmind.agent.tools import web

    async def fake(url, config, *, allowed, max_bytes=None):
        return url, content_type, data, "utf-8"

    monkeypatch.setattr(web, "fetch_bytes", fake)


def _tool_ctx(config, cache=None, vision=None):
    from docsmind.agent.catalog import Catalog
    from docsmind.agent.sources import SourceRegistry
    from docsmind.agent.tools.base import ToolContext

    return ToolContext(
        repo=None, catalog=Catalog([]), sources=SourceRegistry(), config=config, emit=lambda e: None,
        cache=cache if cache is not None else {}, vision=vision,
    )  # fmt: skip


async def _read(ctx, **args):
    from docsmind.domain.agent import ToolCall

    return await build_registry().execute(ToolCall(id="1", name="read_url", arguments=args), ctx)


async def test_read_url_reads_a_pdf_with_page_labels_and_lists_pages_without_text(config, monkeypatch):
    long_text = "Requirement: backups shall be encrypted at rest and in transit. " * 3
    _serve(monkeypatch, "application/pdf", _pdf([long_text, "", long_text]))
    out = await _read(_tool_ctx(config), url="https://example.org/changes.pdf")
    assert not out.is_error
    assert "PDF (3 pages)" in out.content and "[page 1]" in out.content and "[page 3]" in out.content
    assert "backups shall be encrypted" in out.content
    assert "little or no readable text" in out.content and "vision_pages" in out.content and ": 2." in out.content


async def test_read_url_reads_a_word_file_with_headings_and_tables(config, monkeypatch):
    _serve(monkeypatch, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", _docx())
    out = await _read(_tool_ctx(config), url="https://example.org/history.docx")
    assert not out.is_error and "Word document" in out.content
    assert "# Revision history" in out.content and "Clause | Change" in out.content
    assert "5.2 | Backups must be encrypted" in out.content


async def test_read_url_still_reads_html_and_plain_text(config, monkeypatch):
    _serve(monkeypatch, "text/html", b"<html><title>History</title><body><p>May 2024: final</p></body></html>")
    html = await _read(_tool_ctx(config), url="https://example.org/history")
    assert "Web page" in html.content and "Title: History" in html.content and "May 2024: final" in html.content
    _serve(monkeypatch, "text/plain", b"plain changelog")
    assert "plain changelog" in (await _read(_tool_ctx(config), url="https://example.org/c.txt")).content


async def test_read_url_names_the_alternatives_for_a_file_it_cannot_read(config, monkeypatch):
    _serve(monkeypatch, "application/zip", b"PK\x03\x04zip")
    out = await _read(_tool_ctx(config), url="https://example.org/all.zip")
    # a zip is sniffed as a possible .docx and fails with a message that says what works
    assert out.is_error and ("Word" in out.content or "cannot be read" in out.content)


async def test_read_url_reads_pdf_pages_as_images(config, monkeypatch):
    _serve(monkeypatch, "application/pdf", _pdf(["", ""]))

    class Vision:
        async def read(self, images, *, question, mime_type, max_tokens):
            assert len(images) == 1 and mime_type == "image/png"
            return "Clause 4.1 now requires MFA"

    ctx = _tool_ctx(config, vision=Vision())
    out = await _read(ctx, url="https://example.org/scan.pdf", vision_pages=[2], question="Transcribe")
    assert not out.is_error and "Clause 4.1 now requires MFA" in out.content and "p.2" in out.content
    assert ctx.vision_log == ["Clause 4.1 now requires MFA"]  # a verifier can confirm proof from it
    without = await _read(_tool_ctx(config), url="https://example.org/scan.pdf", vision_pages=[1])
    assert without.is_error and "not configured" in without.content


async def test_the_old_fetch_url_name_still_works(config, monkeypatch):
    from docsmind.domain.agent import ToolCall

    _serve(monkeypatch, "text/plain", b"hello")
    out = await build_registry().execute(
        ToolCall(id="1", name="fetch_url", arguments={"url": "https://example.org/a.txt"}), _tool_ctx(config)
    )
    assert not out.is_error and "hello" in out.content
