"""Exercise public-update boundaries with an inert QQ client."""

import asyncio
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

from astrbot.api.provider import LLMResponse
from data.plugins.astrbot_plugin_dafeiyu_presence import main as presence


class PresenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        schema = json.loads(
            (Path(presence.__file__).parent / "_conf_schema.json").read_text()
        )
        self.config = {key: value["default"] for key, value in schema.items()}
        self.config.update(
            enabled=True,
            dry_run=False,
            signature_enabled=True,
            signature_sync_off_confirmed=True,
            account_id="100000001",
            owner_id="100000002",
        )
        self.original = "原来的签名"
        self.signature = self.original
        self.account = "100000001"
        self.writes = []
        self.bot = SimpleNamespace(call_action=AsyncMock(side_effect=self.action))
        platform = SimpleNamespace(
            meta=lambda: SimpleNamespace(id="qq-personal"), get_client=lambda: self.bot
        )
        self.context = SimpleNamespace(
            platform_manager=SimpleNamespace(get_insts=lambda: [platform]),
            persona_manager=SimpleNamespace(
                get_persona_v3_by_id=lambda name: {"prompt": "PUBLIC_PERSONA"}
            ),
            get_current_chat_provider_id=AsyncMock(return_value="deepseek/test"),
            llm_generate=AsyncMock(
                return_value=LLMResponse(
                    role="assistant", completion_text='{"text":"脑子上班，尾巴休假。"}'
                )
            ),
            conversation_manager=SimpleNamespace(
                get_conversation=Mock(
                    side_effect=AssertionError("Private chat must not be read")
                )
            ),
        )
        with patch.object(presence.StarTools, "get_data_dir", return_value=self.root):
            self.plugin = presence.DafeiyuPresence(self.context, self.config)
        self.drafts = {
            "qzone": "有些题绕来绕去，想通的时候却只剩一句原来如此。白饭都得往后排一会儿。",
            "signature": "脑子上班，尾巴休假。",
        }
        self.plugin.draft = AsyncMock(side_effect=lambda kind: self.drafts[kind])
        self.clock = patch.object(presence, "datetime", wraps=datetime)
        self.time = self.clock.start()
        self.time.now.return_value = datetime(
            2026, 10, 27, 19, 30, tzinfo=ZoneInfo("Asia/Shanghai")
        )
        self.addCleanup(self.clock.stop)

    async def action(self, name, **kwargs):
        if name == "get_login_info":
            return {"user_id": self.account}
        if name == "get_stranger_info":
            return {"long_nick": self.signature}
        if name in ("send_qzone_msg", "set_self_longnick"):
            state = json.loads(self.plugin.state_path.read_text())
            self.assertEqual(state["attempts"][-1]["status"], "pending")
            self.writes.append((name, kwargs))
            if name == "send_qzone_msg":
                return {"tid": "synthetic-tid"}
            self.assertEqual(state["original_signature"], self.original)
            self.signature = kwargs["longNick"]
            return None
        raise AssertionError(f"Unexpected action: {name}")

    async def test_moment_is_friends_only_and_reservation_precedes_the_api(self):
        await self.plugin.run("qzone")
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.writes[0][0], "send_qzone_msg")
        self.assertEqual(self.writes[0][1]["ugc_right"], 4)
        self.assertEqual(self.writes[0][1]["images"], [])
        self.assertEqual(self.plugin.state["attempts"][-1]["status"], "success")

    async def test_concurrent_runs_publish_only_once(self):
        await asyncio.gather(self.plugin.run("qzone"), self.plugin.run("qzone"))
        self.assertEqual(len(self.writes), 1)

    async def test_dry_run_does_not_write_to_qq(self):
        self.config["dry_run"] = True
        await self.plugin.run("qzone")
        self.assertEqual(self.writes, [])
        self.assertEqual(self.plugin.state["attempts"], [])
        self.assertIn("qzone", self.plugin.state["drafts"])

    async def test_wrong_account_is_never_modified(self):
        self.account = "123"
        await self.plugin.run("qzone")
        self.plugin.draft.assert_not_awaited()
        self.assertEqual(self.writes, [])

    async def test_weekly_cap_includes_uncertain_attempts(self):
        self.plugin.state["attempts"] = [
            {"kind": "qzone", "date": day, "week": "2026-W44", "status": "unknown"}
            for day in ["2026-10-26", "2026-10-28", "2026-10-29"]
        ]
        self.time.now.return_value = datetime(
            2026, 10, 31, 19, 30, tzinfo=self.plugin.timezone
        )
        await self.plugin.run("qzone")
        self.bot.call_action.assert_not_awaited()

    async def test_new_week_allows_a_new_moment(self):
        self.plugin.state["attempts"] = [
            {
                "kind": "qzone",
                "date": day,
                "week": "2026-W44",
                "status": "success",
                "text": day,
            }
            for day in ["2026-10-27", "2026-10-29", "2026-10-31"]
        ]
        self.time.now.return_value = datetime(
            2026, 11, 3, 19, 30, tzinfo=self.plugin.timezone
        )
        await self.plugin.run("qzone")
        self.assertEqual(len(self.writes), 1)

    async def test_unknown_api_outcome_is_not_retried_after_reload(self):
        async def uncertain(name, **kwargs):
            if name == "get_login_info":
                return {"user_id": self.account}
            if name == "send_qzone_msg":
                self.writes.append((name, kwargs))
                raise TimeoutError("Response lost")
            raise AssertionError(name)

        self.bot.call_action.side_effect = uncertain
        await self.plugin.run("qzone")
        with patch.object(presence.StarTools, "get_data_dir", return_value=self.root):
            reloaded = presence.DafeiyuPresence(self.context, self.config)
        await reloaded.run("qzone")
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(reloaded.state["attempts"][-1]["status"], "unknown")

    async def test_signature_is_backed_up_verified_and_updated_at_most_once(self):
        await self.plugin.run("signature")
        await self.plugin.run("signature")
        self.assertEqual(self.signature, self.drafts["signature"])
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.plugin.state["original_signature"], self.original)
        self.assertEqual(self.plugin.state["attempts"][-1]["status"], "success")

    async def test_signature_waits_for_sync_setting_confirmation(self):
        self.config["signature_sync_off_confirmed"] = False
        await self.plugin.run("signature")
        self.bot.call_action.assert_not_awaited()

    async def test_signature_readback_delay_does_not_repeat_the_write(self):
        old_action = self.bot.call_action.side_effect
        stale_reads = 0

        async def delayed(name, **kwargs):
            nonlocal stale_reads
            if (
                name == "get_stranger_info"
                and self.signature != self.original
                and stale_reads == 0
            ):
                stale_reads += 1
                return {"long_nick": self.original}
            return await old_action(name, **kwargs)

        self.bot.call_action.side_effect = delayed
        with patch.object(presence.asyncio, "sleep", AsyncMock()):
            await self.plugin.run("signature")
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(stale_reads, 1)
        self.assertEqual(self.plugin.state["attempts"][-1]["status"], "success")

    async def test_draft_reads_only_allowlisted_public_lore(self):
        directory = self.root / "config"
        directory.mkdir()
        entries = [
            {
                "name": "海玻璃的小收藏",
                "content": "PUBLIC_LORE",
                "enabled": True,
                "scope": [],
            },
            {
                "name": "海玻璃的小收藏",
                "content": "PRIVATE_SENTINEL",
                "enabled": True,
                "scope": ["someone"],
            },
            {
                "name": "私人约定",
                "content": "PRIVATE_SENTINEL",
                "enabled": True,
                "scope": [],
            },
        ]
        (directory / "astrbot_plugin_worldbook_config.json").write_text(
            json.dumps({"entry_storage": entries})
        )
        self.plugin.draft = presence.DafeiyuPresence.draft.__get__(self.plugin)
        with patch.object(
            presence, "get_astrbot_data_path", return_value=str(self.root)
        ):
            text = await self.plugin.draft("signature")
        self.assertEqual(text, self.drafts["signature"])
        arguments = self.context.llm_generate.await_args.kwargs
        self.assertIn("PUBLIC_LORE", arguments["prompt"])
        self.assertNotIn("PRIVATE_SENTINEL", arguments["prompt"])
        self.assertEqual(arguments["contexts"], [])
        self.context.conversation_manager.get_conversation.assert_not_called()

    async def test_invalid_generated_markup_is_not_a_public_draft(self):
        self.context.llm_generate.return_value = LLMResponse(
            role="assistant", completion_text='{"text":"&&happy&&"}'
        )
        self.plugin.draft = presence.DafeiyuPresence.draft.__get__(self.plugin)
        with self.assertRaises(ValueError):
            await self.plugin.draft("signature")

    async def test_startup_registers_future_slots_without_writing(self):
        await self.plugin.initialize()
        try:
            self.assertEqual(
                {job.id for job in self.plugin.scheduler.get_jobs()},
                {"qzone", "signature"},
            )
            self.assertEqual(self.writes, [])
            self.plugin.draft.assert_not_awaited()
        finally:
            await self.plugin.terminate()

    async def test_non_owner_cannot_preview_or_change_qq(self):
        event = SimpleNamespace(
            is_private_chat=lambda: True, get_sender_id=lambda: "123", stop_event=Mock()
        )
        self.assertEqual([reply async for reply in self.plugin.preview(event)], [])
        event.stop_event.assert_called_once()
        self.plugin.draft.assert_not_awaited()
        self.assertEqual(self.writes, [])


if __name__ == "__main__":
    unittest.main()
