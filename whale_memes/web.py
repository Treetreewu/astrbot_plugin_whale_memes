"""Bounded public article reads; no login, cookies or anti-bot workarounds."""
import asyncio
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .evidence import Source, article_url


class RestrictedRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        article_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ArticleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.content_depth = None
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"br", "img", "meta", "link", "input", "hr", "source", "wbr", "area", "base", "embed", "param", "track", "col"}:
            if tag == "br" and self.content_depth is not None:
                self.parts.append("\n")
            return
        self.depth += 1
        values = dict(attrs)
        if tag == "div" and "mw-parser-output" in values.get("class", "").split() and self.content_depth is None:
            self.content_depth = self.depth
        if tag in {"script", "style"}:
            self.skip += 1
        if tag in {"p", "div", "li", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.skip = max(0, self.skip - 1)
        if self.content_depth == self.depth:
            self.content_depth = None
        self.depth = max(0, self.depth - 1)

    def handle_data(self, value):
        if self.content_depth is not None and not self.skip:
            self.parts.append(value)


def read_article(url, timeout, limit):
    article_url(url)
    parts = urlsplit(url)
    # urllib requires ASCII request targets; keep existing percent escapes intact.
    target = urlunsplit((parts.scheme, parts.netloc, quote(parts.path, safe="/%:@"), "", ""))
    request = Request(target, headers={"User-Agent": "WhaleMemeNotebook/2.0 (+https://github.com/Treetreewu/astrbot_plugin_whale_memes)"})
    with build_opener(RestrictedRedirect()).open(request, timeout=timeout) as response:
        article_url(response.url)
        if response.headers.get_content_type() != "text/html":
            raise ValueError("Expected public article HTML")
        data = response.read(600001)
        if len(data) > 600000:
            raise ValueError("Article exceeds transfer budget")
        parser = ArticleText()
        parser.feed(data.decode("utf-8", errors="replace"))
        content = " ".join("".join(parser.parts).split())[:limit]
        if len(content) < 24:
            raise ValueError("No readable article text")
        return Source(response.url, "", content, "page")


async def public_fetch(url, config):
    return await asyncio.to_thread(read_article, url, config.operation_timeout_seconds, config.max_source_chars)
