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


def test_web_tools_belong_to_the_change_analyzer_only(config, llm, repo):
    registry = build_registry()
    assert {"web_search", "fetch_url"} <= set(registry.names())
    assert not {"web_search", "fetch_url"} & set(registry.for_executor().names())

    info = make_agent(config, llm, repo).describe()
    assert not {"web_search", "fetch_url"} & {t["name"] for t in info["agent"]["tools"]}
    by_name = {s["name"]: {t["name"] for t in s["tools"]} for s in info["subagents"]}
    assert {"web_search", "fetch_url"} <= by_name["change_analyzer"]
    assert not {"web_search", "fetch_url"} & by_name["researcher"]


def test_web_search_is_offered_only_when_configured():
    registry = build_registry().subset(["search", "web_search", "fetch_url"])
    off = DocsmindConfig(_env_file=None)
    on = DocsmindConfig(_env_file=None, web_search_api_key="tvly-test")
    assert set(registry.configured(off).names()) == {"search", "fetch_url"}
    assert set(registry.configured(on).names()) == {"search", "web_search", "fetch_url"}
