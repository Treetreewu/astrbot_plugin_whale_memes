"""Source admission and quote checks; no claim of semantic truth verification."""
from dataclasses import dataclass
import hashlib
import json
import re
import unicodedata
from urllib.parse import unquote, urlsplit


def text(value, limit, field):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("Invalid " + field)
    if any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in value):
        raise ValueError("Invalid " + field)
    return value.strip()


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def article_url(value):
    value = text(value, 1500, "source URL")
    url = urlsplit(value)
    if (url.scheme != "https" or url.netloc not in {"zh.moegirl.org.cn", "mzh.moegirl.org.cn"}
            or url.query or "\\" in value or any(c.isspace() for c in value)):
        raise ValueError("Source must be a Moegirl Chinese article")
    title = unquote(url.path).strip("/")
    first, _, rest = title.partition("/")
    if first.lower() in {"zh", "zh-cn", "zh-tw", "zh-hk", "zh-sg", "zh-hans", "zh-hant"}:
        title = rest
    namespace = title.split(":", 1)[0].casefold() if ":" in title else ""
    if (not title or title.lower() == "mainpage" or ".php" in title.lower()
            or any(p in {".", ".."} for p in title.split("/"))
            or namespace in {"special", "user", "user talk", "talk", "category", "template", "file", "help",
                "特殊", "用户", "使用者", "用户讨论", "使用者討論", "讨论", "討論", "分类", "分類",
                "模板", "文件", "檔案", "帮助", "幫助", "萌娘百科", "萌娘百科 talk"}):
        raise ValueError("Source must be an article, not an administrative page")
    return value


def parse_json(raw):
    raw = text(raw, 12000, "model response")
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    return json.loads(raw)


@dataclass(frozen=True)
class Source:
    url: str
    title: str
    content: str
    kind: str

    def admitted(self, limit):
        article_url(self.url)
        if self.kind not in {"page", "search_excerpt"}:
            raise ValueError("Unsupported evidence kind")
        return Source(self.url, str(self.title)[:120], text(self.content[:limit], limit, "source"), self.kind)


def grounded_note(raw, sources):
    item = parse_json(raw)
    if not isinstance(item, dict) or item.get("relevant") is not True:
        raise ValueError("No supported interpretation")
    idx = item.get("source_id")
    if type(idx) is not int or not 0 <= idx < len(sources):
        raise ValueError("Unknown source")
    source = sources[idx]
    quote = text(item.get("evidence_quote"), 240, "evidence quote")
    # Only normalize whitespace, never punctuation or case when checking evidence.
    quote_normal = " ".join(quote.split())
    if len(quote_normal) < 12 or quote_normal not in " ".join(source.content.split()):
        raise ValueError("Quote is not present in retrieved source")
    aliases = item.get("aliases", [])
    if not isinstance(aliases, list) or len(aliases) > 5:
        raise ValueError("Invalid aliases")
    return dict(meaning=text(item.get("meaning"), 300, "meaning"),
        usage=text(item.get("usage"), 300, "usage"), caveats=text(item.get("caveats"), 200, "caveats"),
        aliases=list(dict.fromkeys(text(a, 80, "alias") for a in aliases)),
        source_url=source.url, source_title=source.title, evidence_kind=source.kind,
        evidence_quote=quote, source_sha256=hashlib.sha256(source.content.encode()).hexdigest(),
        verification="source_quote_matched")
