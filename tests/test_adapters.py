import asyncio
from dataclasses import asdict, replace
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from whale_memes.astr_scope import persona
from whale_memes.backends import AstrBackend, MaiBackend
from whale_memes.config import Config

ROOT = Path(__file__).resolve().parents[1]


class BackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_mai_uses_native_search_page_kind_and_configured_task(self):
        ctx = SimpleNamespace(web=SimpleNamespace(search=AsyncMock(return_value={"results": [{"url": "u"}]}),
            fetch=AsyncMock(return_value={"url": "u", "title": "t", "content": "c", "content_kind": "page_text"})),
            llm=SimpleNamespace(generate=AsyncMock(return_value={"success": True, "response": "answer"})))
        backend = MaiBackend(ctx, Config(model_task="replyer"))
        self.assertEqual(await backend.search("q", 3), [{"url": "u"}])
        ctx.web.search.assert_awaited_once_with("q", max_results=3)
        self.assertEqual((await backend.fetch("u")).kind, "page")
        ctx.web.fetch.return_value["content_kind"] = "summary"
        with self.assertRaises(ValueError):
            await backend.fetch("u")
        self.assertEqual(await backend.generate("system", "prompt"), "answer")
        self.assertEqual(ctx.llm.generate.call_args.kwargs["task_name"], "replyer")
        self.assertEqual(len(ctx.llm.generate.call_args.kwargs["prompt"]), 2)

    async def test_astr_generation_resolves_current_provider_without_chat_history(self):
        context = SimpleNamespace(get_current_chat_provider_id=AsyncMock(return_value="selected"),
            llm_generate=AsyncMock(return_value=SimpleNamespace(completion_text="answer")))
        backend = AstrBackend(context, SimpleNamespace(unified_msg_origin="group:a"), Config())
        self.assertEqual(await backend.generate("system", "public keyword"), "answer")
        context.get_current_chat_provider_id.assert_awaited_once_with("group:a")
        kwargs = context.llm_generate.call_args.kwargs
        self.assertEqual(kwargs["chat_provider_id"], "selected")
        self.assertEqual(kwargs["contexts"], [])
        self.assertIsNone(kwargs["tools"])
        backend.config = replace(Config(), provider_id="override")
        await backend.generate("system", "public keyword")
        self.assertEqual(context.llm_generate.call_args.kwargs["chat_provider_id"], "override")

    async def test_astr_native_tools_receive_correct_host_scope(self):
        search = AsyncMock(return_value=json.dumps({"results": [{"url": "u", "snippet": "s"}]}))
        fetch = AsyncMock(return_value="URL: u\nTitle: t\nContent: actual text")
        native = SimpleNamespace(DeepSeekWebSearchTool=lambda: SimpleNamespace(call=search),
                                 DeepSeekFetchWebPageTool=lambda: SimpleNamespace(call=fetch))
        context = SimpleNamespace(get_config=lambda **kwargs: {"provider_settings": {"web_search": True, "websearch_provider": "deepseek"}})
        event = SimpleNamespace(unified_msg_origin="group:a")
        backend = AstrBackend(context, event, Config())
        with patch.object(backend, "native", return_value=native):
            self.assertEqual(await backend.search("q", 2), [{"url": "u", "snippet": "s"}])
            self.assertIs(search.call_args.args[0].context.event, event)
            self.assertIs(search.call_args.args[0].context.context, context)
            self.assertEqual((await backend.fetch("u")).content, "actual text")
            fetch.return_value = "Error: credential details"
            with self.assertRaises(ValueError):
                await backend.fetch("u")

    async def test_astr_persona_priority_and_explicit_none(self):
        sp = SimpleNamespace(get_async=AsyncMock(return_value={"persona_id": "override"}))
        context = SimpleNamespace(conversation_manager=SimpleNamespace(
            get_curr_conversation_id=AsyncMock(return_value="cid"),
            get_conversation=AsyncMock(return_value=SimpleNamespace(persona_id="conversation"))),
            persona_manager=SimpleNamespace(get_default_persona_v3=AsyncMock(return_value={"name": "default"})))
        event = SimpleNamespace(unified_msg_origin="group:a")
        self.assertEqual(await persona(context, sp, event), "override")
        sp.get_async.return_value = {}
        self.assertEqual(await persona(context, sp, event), "conversation")
        context.conversation_manager.get_conversation.return_value.persona_id = "[%None]"
        self.assertEqual(await persona(context, sp, event), "[%None]")
        context.conversation_manager.get_curr_conversation_id.return_value = None
        self.assertEqual(await persona(context, sp, event), "default")
        sp.get_async.side_effect = RuntimeError("unavailable")
        with self.assertRaises(RuntimeError):
            await persona(context, sp, event)


class PackageTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(importlib.util.find_spec("maibot_sdk"), "install pinned SDK for native plugin checks")
    async def test_maibot_actual_package_loader_lifecycle_and_config_reload(self):
        spec = importlib.util.spec_from_file_location("_meme_mai_package", ROOT / "plugin.py", submodule_search_locations=[str(ROOT)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        plugin = module.create_plugin()
        defaults = plugin.build_default_config()
        self.assertEqual(defaults["learning"], asdict(Config()))
        with tempfile.TemporaryDirectory() as tmp:
            plugin._set_context(SimpleNamespace(data_dir=Path(tmp)))
            plugin.set_plugin_config(defaults)
            await plugin.on_load()
            old = plugin.learning
            response = json.loads((await plugin.meme_notebook("lookup", "测试", stream_id="group:a"))["content"])
            self.assertEqual(response["status"], "not_found")
            defaults["learning"]["enabled"] = False
            plugin.set_plugin_config(defaults)
            await plugin.on_config_update("self", defaults, "2.0.0")
            self.assertTrue(old.closed)
            response = json.loads((await plugin.meme_notebook("learn", "测试", stream_id="group:a"))["content"])
            self.assertEqual(response["status"], "disabled")
            await plugin.on_unload()
            self.assertTrue(plugin.learning.closed)

    async def test_astr_package_relative_import_and_termination(self):
        class Star:
            def __init__(self, context):
                self.context = context
        api = ModuleType("astrbot.api")
        api.llm_tool = lambda name: lambda fn: fn
        api.star = SimpleNamespace(Star=Star, register=lambda *args: lambda cls: cls)
        api.sp = SimpleNamespace(get_async=AsyncMock(return_value={"persona_id": "p"}))
        events = ModuleType("astrbot.api.event")
        events.AstrMessageEvent = object
        stars = ModuleType("astrbot.api.star")
        stars.StarTools = SimpleNamespace(get_data_dir=lambda name: None)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules,
                {"astrbot": ModuleType("astrbot"), "astrbot.api": api, "astrbot.api.event": events, "astrbot.api.star": stars}):
            spec = importlib.util.spec_from_file_location("_meme_astr_package", ROOT / "main.py", submodule_search_locations=[str(ROOT)])
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            plugin = module.MemePlugin(SimpleNamespace(), {"storage_path": str(Path(tmp) / "notes.db")})
            event = SimpleNamespace(unified_msg_origin="group:a")
            result = json.loads(await plugin.meme_notebook(event, "lookup", "测试"))
            self.assertEqual(result["status"], "not_found")
            api.sp.get_async.side_effect = RuntimeError("secret")
            result = json.loads(await plugin.meme_notebook(event, "learn", "测试"))
            self.assertEqual(result["status"], "unavailable")
            self.assertNotIn("secret", json.dumps(result))
            await plugin.terminate()
            self.assertTrue(plugin.learning.closed)

    def test_default_and_astr_config_schema_cover_all_settings(self):
        defaults = asdict(Config())
        self.assertEqual(json.loads((ROOT / "whale_memes/default_config.json").read_text()), defaults)
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        self.assertEqual({k: v["default"] for k, v in schema.items()}, defaults)


if __name__ == "__main__":
    unittest.main()
