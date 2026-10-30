"""Native search adapters; model discovery is never treated as search evidence."""
import json
from types import SimpleNamespace

from .evidence import Source


class MaiBackend:
    def __init__(self, ctx, config):
        self.ctx, self.config = ctx, config

    async def search(self, query, count):
        result = await self.ctx.web.search(query, max_results=count)
        return result.get("results", [])

    async def fetch(self, url):
        result = await self.ctx.web.fetch(url)
        if result.get("content_kind") != "page_text":
            raise ValueError("No native page text")
        return Source(result.get("url", url), result.get("title", ""), result.get("content", ""), "page")

    async def generate(self, system, prompt):
        result = await self.ctx.llm.generate(prompt=[{"role": "system", "content": system},
            {"role": "user", "content": prompt}], task_name=self.config.model_task, max_tokens=1200)
        if not result.get("success"):
            raise ValueError("Native generation failed")
        return result.get("response", "")


class AstrBackend:
    def __init__(self, context, event, config):
        self.context, self.event, self.config = context, event, config

    def native(self):
        settings = self.context.get_config(umo=self.event.unified_msg_origin).get("provider_settings", {})
        if not settings.get("web_search"):
            raise ValueError("Native search disabled")
        # Only verified structured tool contracts are used. Other providers can
        # still use URL discovery + public article retrieval without borrowing keys.
        from astrbot.core.tools import web_search_tools as web
        provider = settings.get("websearch_provider")
        if provider != "deepseek":
            raise ValueError("Native structured provider not supported by this adapter")
        return web

    def wrapper(self):
        # Native search tools read only context.context.{context,event}; no tool loop.
        return SimpleNamespace(context=SimpleNamespace(context=self.context, event=self.event))

    async def search(self, query, count):
        result = await self.native().DeepSeekWebSearchTool().call(self.wrapper(), query=query, count=count)
        return json.loads(result).get("results", [])

    async def fetch(self, url):
        result = await self.native().DeepSeekFetchWebPageTool().call(self.wrapper(), url=url)
        if not isinstance(result, str) or not result.startswith("URL: " + url + "\nTitle: ") or "\nContent: " not in result:
            raise ValueError("Native fetch returned no page")
        header, content = result.split("\nContent: ", 1)
        return Source(url, header.split("\nTitle: ", 1)[1], content, "page")

    async def generate(self, system, prompt):
        provider = self.config.provider_id or await self.context.get_current_chat_provider_id(self.event.unified_msg_origin)
        result = await self.context.llm_generate(chat_provider_id=provider, prompt=prompt,
            system_prompt=system, contexts=[], tools=None, request_max_retries=1)
        return result.completion_text
