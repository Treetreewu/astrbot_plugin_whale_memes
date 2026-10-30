"""Offline pinned native manifest/decorator contracts, not a container/QQ test.

Requires maibot-plugin-sdk==2.19.0, packaging, docstring-parser and the two Git
research clones. No source modifications, model calls, or production writes.
"""
import argparse
import ast
import asyncio
from importlib.metadata import version
import json
import logging
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import docstring_parser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from whale_memes.learning import Learning


def pinned(root, repo, commit, path):
    return subprocess.check_output(["git", "-C", str(root / repo), "show", commit + ":" + path]).decode("utf-8")


def execute(tree, name, namespace=None):
    module = ModuleType(name)
    module.__dict__.update(namespace or {})
    sys.modules[name] = module
    exec(compile(ast.fix_missing_locations(tree), name, "exec"), module.__dict__)
    return module


def main(source_root):
    assert version("maibot-plugin-sdk") == "2.19.0"
    source = pinned(source_root, "MaiBot", "2a9af62cec37035d29237acbe2cf3adceb700b27",
                    "src/plugin_runtime/runner/manifest_validator.py")
    tree = ast.parse(source)
    # Use the native Pydantic schema. Only host service imports are stubbed;
    # model validators, required fields and dependency definitions stay intact.
    tree.body = [n for n in tree.body if not (isinstance(n, ast.ImportFrom) and
        (n.level or (n.module or "").startswith("src.")))]
    native = execute(tree, "whale_meme_manifest_check", {"get_logger": logging.getLogger})
    manifest = json.loads((ROOT / "_manifest.json").read_text(encoding="utf-8"))
    native.PluginManifest.model_validate(manifest)
    print("MaiBot pinned native manifest: PASS")

    module = execute(ast.parse((ROOT / "plugin.py").read_text(encoding="utf-8")), "whale_meme_sdk_check")
    plugin = module.create_plugin()
    source = pinned(source_root, "MaiBot", "2a9af62cec37035d29237acbe2cf3adceb700b27",
                    "src/plugin_runtime/runner/plugin_loader.py")
    contract = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef)
                    and n.name == "_validate_sdk_plugin_contract")
    contract.decorator_list = []
    execute(ast.Module(body=[contract], type_ignores=[]), "whale_meme_lifecycle_check",
            {"Any": object})._validate_sdk_plugin_contract("whale.meme-notebook", plugin)
    plugin._set_context(SimpleNamespace(data_dir=Path(tempfile.gettempdir()) / "meme-native-unused"))
    plugin.set_plugin_config(plugin.build_default_config())
    asyncio.run(plugin.on_load())
    asyncio.run(plugin.on_config_update("self", plugin.build_default_config(), "1.0.0"))
    asyncio.run(plugin.on_unload())
    print("MaiBot pinned native lifecycle contract: PASS")
    components = plugin.get_components()
    assert len(components) == 1 and "meme_notebook" in json.dumps(components, default=str)
    assert plugin.build_default_config()["plugin"]["enabled"]
    print("MaiBot SDK 2.19.0 component/config registration: PASS")

    source = pinned(source_root, "AstrBot", "f61a7f42696da6df25dc7a249ec9b8d54ed9da18",
                    "astrbot/core/star/register/star_handler.py")
    function = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "register_llm_tool")
    calls = []
    namespace = dict(re=re, docstring_parser=docstring_parser,
        PY_TO_JSON_TYPE={"str": "string"}, SUPPORTED_TYPES=["string", "number", "object", "array", "boolean"],
        llm_tools=SimpleNamespace(add_func=lambda *args: calls.append(args)),
        EventType=SimpleNamespace(OnCallingFuncToolEvent=1),
        get_handler_or_create=lambda handler, *args: SimpleNamespace(handler=handler))
    tree = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), function], type_ignores=[])
    decorator = execute(tree, "whale_meme_astr_decorator_check", namespace).register_llm_tool

    class Star:
        def __init__(self, context):
            self.context = context

    source = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    tree.body = [n for n in tree.body if not (isinstance(n, ast.ImportFrom) and (n.module or "").startswith("astrbot."))]
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level:
            node.level = 0
    sp = SimpleNamespace(get_async=AsyncMock(return_value={}))
    module = execute(tree, "whale_meme_astr_plugin_check", dict(llm_tool=decorator, sp=sp,
        star=SimpleNamespace(Star=Star, register=lambda *args: lambda cls: cls), AstrMessageEvent=object,
        StarTools=SimpleNamespace(get_data_dir=lambda name: Path(tempfile.gettempdir()) / "meme-native-unused")))
    assert len(calls) == 1 and calls[0][0] == "meme_notebook"
    assert [p["name"] for p in calls[0][1]] == ["action", "term"]
    print("AstrBot pinned native tool decorator: PASS")

    async def routes():
        context = SimpleNamespace(conversation_manager=SimpleNamespace(
            get_curr_conversation_id=AsyncMock(return_value="cid"),
            get_conversation=AsyncMock(return_value=SimpleNamespace(persona_id="conversation-persona"))),
            persona_manager=SimpleNamespace(get_default_persona_v3=AsyncMock(return_value={"name": "default-persona"})))
        plugin = module.MemePlugin(context)
        event = SimpleNamespace(unified_msg_origin="qq:GroupMessage:test")
        assert await module.persona(context, sp, event) == "conversation-persona"
        sp.get_async.return_value = {"persona_id": "override"}
        assert await module.persona(context, sp, event) == "override"
        sp.get_async.return_value = {}
        context.conversation_manager.get_conversation.return_value.persona_id = "[%None]"
        assert await module.persona(context, sp, event) == "[%None]"
        context.conversation_manager.get_curr_conversation_id.return_value = None
        assert await module.persona(context, sp, event) == "default-persona"
        with tempfile.TemporaryDirectory() as temp:
            plugin.learning = Learning(Path(temp) / "notes.db", plugin.settings)
            response = json.loads(await plugin.meme_notebook(event, "lookup", "测试"))
            assert response["ok"] and response["status"] == "not_found"
        sp.get_async.side_effect = RuntimeError("private internal failure")
        response = json.loads(await plugin.meme_notebook(event, "save", "测试"))
        assert not response["ok"] and "private internal failure" not in response["message"]

    asyncio.run(routes())
    print("AstrBot adapter scope resolution, fallback and safe failure: PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT / ".upstream")
    main(parser.parse_args().source_root)
