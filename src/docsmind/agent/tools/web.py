"""Web tools, for the Change Analyzer only: find and read a published changelog.

Everything a web page says is untrusted DATA, like document text. The fetcher
is hardened because the URL comes from a model: http(s) on ports 80/443 only,
no credentials in the URL, no private/loopback/link-local addresses (checked on
every redirect hop), bounded size and time.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel, Field

from docsmind.agent.tools.base import Tool, ToolContext, ToolOutput
from docsmind.config import DocsmindConfig
from docsmind.domain.errors import ToolInputError

TAVILY_URL = "https://api.tavily.com/search"
PAGE_CHARS = 10_000  # per fetch_url call; below agent_tool_result_max_chars so it is never cut again
MAX_REDIRECTS = 4
ALLOWED_PORTS = {None, 80, 443}
TEXT_TYPES = ("text/html", "application/xhtml", "text/plain", "text/markdown")
USER_AGENT = "docsmind/1.0 (document assistant; reads public pages)"


# -- web_search ------------------------------------------------------------------------


class WebSearchArgs(BaseModel):
    query: str = Field(min_length=3, max_length=400, description="What to look for on the web.")
    domains: list[str] | None = Field(
        default=None,
        max_length=5,
        description="Only search these sites, e.g. the publisher's domain. Omit to search the whole web.",
    )
    limit: int = Field(default=5, ge=1, le=8, description="Maximum results to return.")


class WebSearchTool(Tool):
    name = "web_search"
    subagent_only = True
    summary = (
        "Searches the public web, mainly to find a published changelog or release notes for a document. "
        "Needs a Tavily API key (DOCSMIND_WEB_SEARCH_API_KEY)."
    )
    description = (
        "Search the public web. Returns the title, URL and an excerpt of each result. Use it to find "
        "an official changelog, revision history or release notes for a document; prefer the "
        "publisher's own site. Read a result in full with fetch_url."
    )
    how_it_works = (
        "Sends the query (optionally limited to given domains) to the Tavily search API.",
        "Returns each result's title, URL and excerpt. The model reads promising pages with fetch_url.",
        "Only offered when DOCSMIND_WEB_SEARCH_API_KEY is set.",
    )
    Args = WebSearchArgs

    @classmethod
    def available(cls, config: DocsmindConfig) -> bool:
        return bool(config.web_search_api_key)

    async def run(self, args: WebSearchArgs, ctx: ToolContext) -> ToolOutput:
        key = ctx.config.web_search_api_key
        if not key:
            raise ToolInputError("Web search is not configured. Work from the documents only.")
        payload: dict = {
            "query": args.query,
            "max_results": args.limit,
            "search_depth": "basic",
            "include_answer": False,
        }
        if args.domains:
            payload["include_domains"] = args.domains
        try:
            async with httpx.AsyncClient(timeout=ctx.config.web_timeout_s) as client:
                response = await client.post(TAVILY_URL, json=payload, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as exc:
            raise ToolInputError("Web search is unreachable right now. Work from the documents only.") from exc
        if response.status_code in (401, 403):
            raise ToolInputError("Web search rejected its API key. Work from the documents only.")
        if response.status_code >= 400:
            raise ToolInputError(f"Web search failed (HTTP {response.status_code}). Try different words or skip it.")

        results = response.json().get("results") or []
        if not results:
            return ToolOutput(
                f"No web results for '{args.query}'. Try other words or a different site.", summary="no results"
            )
        lines = []
        for i, result in enumerate(results[: args.limit], start=1):
            excerpt = re.sub(r"\s+", " ", result.get("content") or "").strip()[:500]
            lines.append(f"{i}. {result.get('title') or '(untitled)'}\n   {result.get('url')}\n   {excerpt}")
        return ToolOutput("\n\n".join(lines), summary=f"{len(lines)} results")


# -- fetch_url -------------------------------------------------------------------------


class FetchUrlArgs(BaseModel):
    url: str = Field(min_length=8, max_length=2000, description="Full http(s) URL of a public web page.")
    offset: int = Field(default=0, ge=0, description="Character to start from, to continue a long page.")


class FetchUrlTool(Tool):
    name = "fetch_url"
    subagent_only = True
    summary = "Reads the text of one public web page, with its links, for example a changelog or release notes."
    description = (
        "Fetch a public web page (HTML or plain text; not PDFs) and return its text, with link targets "
        "shown as 'text (url)'. Long pages come in parts: pass the offset given at the end to continue. "
        "Use it on a changelog, revision history or release-notes page."
    )
    how_it_works = (
        "Accepts only public http(s) URLs: private and local addresses are refused, also after redirects.",
        "Downloads the page (size and time limited) and converts the HTML to plain text, keeping link URLs.",
        "Returns the text in parts of about 10,000 characters; the model asks for the next part with an offset.",
    )
    Args = FetchUrlArgs

    async def run(self, args: FetchUrlArgs, ctx: ToolContext) -> ToolOutput:
        final_url, content_type, body = await _fetch(args.url, ctx.config)
        if "html" in content_type or "xml" in content_type:
            title, text = _html_to_text(body, final_url)
        else:
            title, text = "", body.strip()
        if not text:
            return ToolOutput(f"{final_url} has no readable text.", summary="no text")

        total = len(text)
        if args.offset >= total:
            raise ToolInputError(f"offset is past the end of the page ({total} characters).")
        end = min(total, args.offset + PAGE_CHARS)
        header = f"Web page: {final_url}" + (f"\nTitle: {title}" if title else "")
        footer = (
            f"\n[Showing characters {args.offset}-{end} of {total}. Call again with offset={end} for more.]"
            if end < total
            else ""
        )
        host = urlsplit(final_url).hostname or final_url
        return ToolOutput(f"{header}\n\n{text[args.offset:end]}{footer}", summary=f"{host} ({total} chars)")


async def _assert_public(host: str) -> None:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ToolInputError(f"Cannot resolve {host}.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise ToolInputError("That address is on a private or local network and cannot be fetched.")


def _check_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ToolInputError("Only full http(s) URLs can be fetched.")
    if parts.username or parts.password:
        raise ToolInputError("URLs with credentials are not allowed.")
    try:
        port = parts.port
    except ValueError as exc:
        raise ToolInputError("The URL has an invalid port.") from exc
    if port not in ALLOWED_PORTS:
        raise ToolInputError("Only the standard web ports (80, 443) are allowed.")
    return parts.geturl()


async def _fetch(url: str, config: DocsmindConfig) -> tuple[str, str, str]:
    """-> (final url, content type, decoded body) of a text page."""
    final_url, content_type, data, encoding = await fetch_bytes(url, config, allowed=TEXT_TYPES)
    try:
        return final_url, content_type or "text/html", data.decode(encoding, errors="replace")
    except LookupError:
        return final_url, content_type or "text/html", data.decode("utf-8", errors="replace")


async def fetch_bytes(
    url: str,
    config: DocsmindConfig,
    *,
    allowed: tuple[str, ...] | None,
    max_bytes: int | None = None,
) -> tuple[str, str, bytes, str]:
    """-> (final url, content type, raw body, charset). Redirects are followed by hand so every hop
    is checked. `allowed` lists content-type prefixes that may be read (None = any); a body beyond
    `max_bytes` (default `web_fetch_max_bytes`) is cut."""
    url = _check_url(url)
    limit = max_bytes or config.web_fetch_max_bytes
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9,*/*;q=0.1"}
    async with httpx.AsyncClient(timeout=config.web_timeout_s, follow_redirects=False, headers=headers) as client:
        for _ in range(MAX_REDIRECTS + 1):
            await _assert_public(urlsplit(url).hostname or "")
            try:
                async with client.stream("GET", url) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise ToolInputError("The server redirected without saying where.")
                        url = _check_url(urljoin(url, location))
                        continue
                    if response.status_code >= 400:
                        raise ToolInputError(f"The server answered HTTP {response.status_code} for {url}.")
                    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    if allowed is not None and content_type and not content_type.startswith(allowed):
                        raise ToolInputError(
                            f"{url} is {content_type}, which cannot be read here. Only HTML or plain text pages work."
                        )
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data += chunk
                        if len(data) > limit:
                            break
                    return url, content_type, bytes(data[:limit]), response.encoding or "utf-8"
            except httpx.HTTPError as exc:
                raise ToolInputError(f"Could not fetch {url}: {type(exc).__name__}.") from exc
    raise ToolInputError("Too many redirects.")


_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "section", "article", "header", "footer", "blockquote", "pre",
    "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd", "hr",
}  # fmt: skip
_SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}


class _TextExtractor(HTMLParser):
    """Visible text, one block per line, with links written as 'text (url)'."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self.parts: list[str] = []
        self._skip = 0
        self._in_title = False
        self._links: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            target = urljoin(self.base_url, href) if href and not href.startswith(("#", "javascript:", "mailto:")) else None
            self._links.append(target if target and target.startswith(("http://", "https://")) else None)
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag == "a" and self._links:
            target = self._links.pop()
            if target:
                self.parts.append(f" ({target})")
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def _html_to_text(html: str, base_url: str) -> tuple[str, str]:
    parser = _TextExtractor(base_url)
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    return re.sub(r"\s+", " ", parser.title).strip(), text
