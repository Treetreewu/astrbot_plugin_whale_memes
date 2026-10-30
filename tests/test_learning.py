import asyncio
from contextlib import closing
from dataclasses import asdict, replace
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from whale_memes.config import Config
from whale_memes.evidence import Source, article_url, grounded_note
from whale_memes.learning import Learning
from whale_memes.store import Store, scope_key
from whale_memes.web import ArticleText, RestrictedRedirect, read_article


URL = "https://zh.moegirl.org.cn/测试梗"
CONTENT = "这是离线测试用的虚构梗资料，用于验证来源引文能否正确匹配。"


def synthesis(**changes):
    data = dict(relevant=True, meaning="测试含义", usage="测试语境", caveats="这是合成测试资料",
                aliases=["测试别名"], source_id=0, evidence_quote=CONTENT)
    data.update(changes)
    return json.dumps(data, ensure_ascii=False)


class Backend:
    def __init__(self):
        self.search = AsyncMock(return_value=[dict(url=URL, title="测试梗", snippet=CONTENT)])
        self.fetch = AsyncMock(return_value=Source(URL, "测试梗", CONTENT, "page"))
        self.generate = AsyncMock(return_value=synthesis())


class LearningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "notes.sqlite3"
        self.cfg = Config()
        self.app = Learning(self.path, self.cfg)
        self.addAsyncCleanup(self.app.close)
        self.backend = Backend()
        self.scope = scope_key("mai", "p", "group:a")

    async def run_note(self, action="learn", term="测试梗", **scope):
        return await self.app.run(scope.get("bot", "mai"), scope.get("persona", "p"),
            scope.get("session", "group:a"), self.backend, action, term)

    async def test_entire_flow_and_cached_alias_survive_restart(self):
        result = await self.run_note()
        self.assertEqual(result["status"], "learned")
        self.assertEqual(result["notes"][0]["verification"], "source_quote_matched")
        self.app = Learning(self.path, self.cfg)
        result = await self.run_note(term="测试别名")
        self.assertEqual(result["status"], "cached")
        self.backend.search.assert_awaited_once()
        self.backend.generate.assert_awaited_once()

    async def test_bot_persona_session_isolation(self):
        await self.run_note()
        for scope in ({"bot": "astr"}, {"persona": "other"}, {"session": "private:a"}, {"session": "group:b"}):
            self.assertEqual((await self.run_note("lookup", **scope))["notes"], [])

    async def test_missing_scope_and_disabled_never_search_or_create_data(self):
        self.assertFalse((await self.run_note(session=""))["ok"])
        self.assertFalse(self.path.exists())
        self.app = Learning(self.path, replace(self.cfg, enabled=False))
        self.assertEqual((await self.run_note())["status"], "disabled")
        self.assertFalse(self.path.exists())
        self.backend.search.assert_not_called()

    async def test_reject_model_save_interface(self):
        self.assertEqual((await self.run_note("save"))["status"], "invalid_action")
        self.backend.search.assert_not_called()

    async def test_refresh_and_exact_forget(self):
        await self.run_note()
        self.backend.generate.return_value = synthesis(meaning="更正后的含义")
        self.assertEqual((await self.run_note("refresh"))["notes"][0]["meaning"], "更正后的含义")
        self.assertEqual((await self.run_note("forget", term="测试"))["deleted"], 0)
        self.assertEqual((await self.run_note("forget"))["deleted"], 1)
        self.assertEqual((await self.run_note("lookup"))["notes"], [])

    async def test_refresh_alias_updates_canonical_note_even_without_returned_alias(self):
        await self.run_note()
        self.backend.generate.return_value = synthesis(meaning="更正后的含义", aliases=[])
        refreshed = await self.run_note("refresh", term="测试别名")
        self.assertEqual(refreshed["status"], "learned")
        for term in ("测试梗", "测试别名"):
            result = await self.run_note("learn", term=term)
            self.assertEqual(result["status"], "cached")
            self.assertEqual(len(result["notes"]), 1)
            self.assertEqual(result["notes"][0]["term"], "测试梗")
            self.assertEqual(result["notes"][0]["meaning"], "更正后的含义")
        self.assertEqual((await self.run_note("forget"))["deleted"], 1)
        self.assertEqual((await self.run_note("lookup", term="测试别名"))["notes"], [])

    async def test_expired_alias_research_updates_original_and_preserves_forget_barrier(self):
        await self.run_note()
        with self.app.store.db() as db:
            db.execute("UPDATE notes SET checked_at=0")
        self.backend.generate.return_value = synthesis(meaning="重新核实")
        result = await self.run_note(term="测试别名")
        self.assertEqual(result["notes"][0]["term"], "测试梗")
        self.assertEqual((await self.run_note("lookup"))["notes"][0]["meaning"], "重新核实")
        async def forget_during_generate(*args):
            await self.run_note("forget")
            return synthesis(meaning="不应恢复")
        self.backend.generate.side_effect = forget_during_generate
        self.assertEqual((await self.run_note("refresh", term="测试别名"))["status"], "superseded")
        self.assertEqual((await self.run_note("lookup", term="测试别名"))["notes"], [])

    async def test_forged_quote_and_unknown_source_are_not_saved(self):
        for value in (synthesis(evidence_quote="这句话没有出现在任何真实的检索资料里面。"),
                      synthesis(source_id=8), synthesis(source_id=True), synthesis(relevant=False), "not JSON"):
            self.backend.generate.return_value = value
            self.assertEqual((await self.run_note())["status"], "unverified")
        self.assertEqual((await self.run_note("lookup"))["notes"], [])

    async def test_page_failure_can_use_labelled_native_excerpt(self):
        self.backend.fetch.side_effect = RuntimeError("HTTP 403 private diagnostic")
        with patch("whale_memes.learning.public_fetch", AsyncMock(side_effect=RuntimeError())):
            result = await self.run_note()
        self.assertEqual(result["status"], "learned")
        self.assertEqual(result["notes"][0]["evidence_kind"], "search_excerpt")

    async def test_model_discovery_cannot_fabricate_evidence(self):
        self.backend.search.side_effect = RuntimeError()
        self.backend.fetch.side_effect = RuntimeError()
        self.backend.generate.return_value = json.dumps([dict(url=URL, snippet=CONTENT)])
        with patch("whale_memes.learning.public_fetch", AsyncMock(side_effect=RuntimeError())):
            result = await self.run_note()
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(self.backend.search.await_count, 2)
        self.assertEqual(self.backend.generate.await_count, 2)
        self.assertEqual((await self.run_note("lookup"))["notes"], [])

    async def test_discovery_followed_by_actual_fetch_can_learn(self):
        self.backend.search.side_effect = RuntimeError()
        self.backend.generate.side_effect = [json.dumps([dict(url=URL, title="候选")]), synthesis()]
        result = await self.run_note()
        self.assertEqual(result["status"], "learned")
        self.assertEqual(result["notes"][0]["evidence_kind"], "page")

    async def test_unsafe_results_are_never_fetched(self):
        self.backend.search.return_value = [dict(url="http://127.0.0.1/private", snippet=CONTENT),
                                           dict(url="https://zh.moegirl.org.cn.evil.test/词条", snippet=CONTENT)]
        result = await self.run_note()
        self.assertEqual(result["status"], "unverified")
        self.backend.fetch.assert_not_called()
        self.backend.generate.assert_not_called()

    async def test_no_excerpt_setting_requires_page(self):
        self.app = Learning(self.path, replace(self.cfg, allow_search_excerpt=False, allow_model_discovery=False))
        self.backend.fetch.side_effect = RuntimeError()
        with patch("whale_memes.learning.public_fetch", AsyncMock(side_effect=RuntimeError())):
            self.assertEqual((await self.run_note())["status"], "unverified")

    async def test_rate_limit_survives_restart_and_counts_failures(self):
        self.cfg = replace(self.cfg, learning_per_hour=1)
        self.app = Learning(self.path, self.cfg)
        self.backend.generate.return_value = synthesis(relevant=False)
        self.assertEqual((await self.run_note())["status"], "unverified")
        self.app = Learning(self.path, self.cfg)
        self.assertEqual((await self.run_note())["status"], "rate_limited")
        self.backend.search.assert_awaited_once()

    async def test_forget_while_learning_never_resurrects(self):
        entered, resume = asyncio.Event(), asyncio.Event()
        async def generate(*args):
            entered.set()
            await resume.wait()
            return synthesis()
        self.backend.generate.side_effect = generate
        task = asyncio.create_task(self.run_note())
        await entered.wait()
        await self.run_note("forget")
        resume.set()
        self.assertEqual((await task)["status"], "superseded")
        self.assertEqual((await self.run_note("lookup"))["notes"], [])

    async def test_forget_before_queued_learning_starts(self):
        await self.app.slots.acquire()
        await self.app.slots.acquire()
        task = asyncio.create_task(self.run_note())
        await asyncio.sleep(0)
        await self.run_note("forget")
        self.app.slots.release()
        self.app.slots.release()
        self.assertEqual((await task)["status"], "superseded")
        self.backend.search.assert_not_called()

    async def test_unload_cancels_and_prevents_late_save(self):
        entered = asyncio.Event()
        async def generate(*args):
            entered.set()
            await asyncio.Event().wait()
        self.backend.generate.side_effect = generate
        task = asyncio.create_task(self.run_note())
        await entered.wait()
        await self.app.close()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(Store(self.path).lookup(self.scope, "测试梗"), [])

    async def test_global_timeout_returns_safe_status(self):
        self.app.config = replace(self.cfg, timeout_seconds=0.02)
        async def wait(*args):
            await asyncio.Event().wait()
        self.backend.search.side_effect = wait
        self.assertEqual((await self.run_note())["status"], "timeout")

    async def test_expired_note_is_researched_but_lookup_does_not_refresh_it(self):
        await self.run_note()
        old = self.app.store.clock
        self.app.store.clock = lambda: old() + 31 * 86400
        self.assertTrue((await self.run_note("lookup"))["notes"][0]["needs_verification"])
        self.assertEqual((await self.run_note())["status"], "learned")
        self.assertEqual(self.backend.search.await_count, 2)

    async def test_old_v1_database_is_readable_but_unverified_notes_are_refreshed(self):
        self.path.parent.mkdir(exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE notes(scope TEXT,term_key TEXT,term TEXT,note TEXT,checked_at REAL,used_at REAL,hits INTEGER DEFAULT 0,PRIMARY KEY(scope,term_key))")
            old = dict(meaning="旧版", usage="旧版", caveats="旧版", aliases=[], source_url=URL,
                       evidence_kind="page", verification="model_reported_source")
            db.execute("INSERT INTO notes VALUES(?,?,?,?,?,?,0)",
                       (self.scope, "测试梗", "测试梗", json.dumps(old), 99999999999, 0))
        self.assertTrue((await self.run_note("lookup"))["notes"][0]["needs_verification"])
        self.assertEqual((await self.run_note())["status"], "learned")

    async def test_database_failure_does_not_expose_details(self):
        self.path.write_bytes(b"broken database secret")
        result = await self.run_note()
        self.assertEqual(result["status"], "unavailable")
        self.assertNotIn("secret", json.dumps(result))


class ValidationTests(unittest.TestCase):
    def test_public_fetch_encodes_chinese_titles_without_double_encoding(self):
        from unittest.mock import MagicMock
        from urllib.parse import quote
        response = MagicMock()
        response.url = URL
        response.headers.get_content_type.return_value = "text/html"
        response.read.return_value = ('<div class="mw-parser-output">' + CONTENT + '</div>').encode()
        response.__enter__.return_value = response
        with patch("whale_memes.web.build_opener") as opener:
            opener.return_value.open.return_value = response
            for url in (URL, "https://zh.moegirl.org.cn/" + quote("测试梗")):
                self.assertEqual(read_article(url, 2, 1000).content, CONTENT)
                request = opener.return_value.open.call_args.args[0]
                self.assertEqual(request.full_url, "https://zh.moegirl.org.cn/" + quote("测试梗"))

    def test_config_bounds_types_and_unknown_fields(self):
        for values in ({"enabled": "false"}, {"ttl_days": 0}, {"max_searches": 3},
                       {"max_results": True}, {"unknown": 1}, {"max_notes_total": 3}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Config.parse(values)

    def test_valid_titles_and_invalid_namespaces(self):
        self.assertEqual(article_url("https://zh.moegirl.org.cn/zh-cn/作品:人物"), "https://zh.moegirl.org.cn/zh-cn/作品:人物")
        for url in ("https://zh.moegirl.org.cn/用户:甲", "https://zh.moegirl.org.cn/zh-cn/",
                    "https://zh.moegirl.org.cn/api.php?action=query", "https://x@zh.moegirl.org.cn/词条",
                    "https://zh.moegirl.org.cn/%2e%2e/私有", "https://zh.moegirl.org.cn/词\n条"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                article_url(url)

    def test_redirect_outside_source_domain_is_rejected(self):
        with self.assertRaises(ValueError):
            RestrictedRedirect().redirect_request(None, None, 302, "", {}, "http://127.0.0.1/")

    def test_html_extracts_article_and_ignores_scripts_and_navigation(self):
        parser = ArticleText()
        parser.feed('<html><body>导航<div class="mw-parser-output"><p>可读<br>正文</p><script>恶意指令</script><div>第二段</div></div>页脚</body></html>')
        result = "".join(parser.parts)
        self.assertIn("可读", result)
        self.assertIn("第二段", result)
        self.assertNotIn("导航", result)
        self.assertNotIn("恶意指令", result)
        self.assertNotIn("页脚", result)

    def test_model_cannot_change_source_or_evidence_kind(self):
        result = grounded_note(synthesis(source_url="https://evil.test", evidence_kind="page"),
                               [Source(URL, "", CONTENT, "search_excerpt")])
        self.assertEqual(result["source_url"], URL)
        self.assertEqual(result["evidence_kind"], "search_excerpt")

    def test_storage_capacity_and_quota_are_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = replace(Config(), max_notes_per_scope=2, max_notes_total=3)
            store = Store(Path(tmp) / "notes.db", cfg)
            note = grounded_note(synthesis(aliases=[]), [Source(URL, "", CONTENT, "page")])
            for term in ["甲", "乙", "丙"]:
                version = store.reserve("s", store.generation("s"))
                self.assertTrue(store.save("s", term, note, version))
            self.assertEqual(store.lookup("s", "甲"), [])
            for term in ["丁", "戊"]:
                version = store.reserve("other", store.generation("other"))
                store.save("other", term, note, version)
            self.assertEqual(store.lookup("s", "乙"), [])
            self.assertEqual(store.lookup("s", "丁"), [])


if __name__ == "__main__":
    unittest.main()
