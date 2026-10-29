"""Verify durable, attributed group memory without QQ or network access."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.core.agent.message import TextPart
from data.plugins.astrbot_plugin_dafeiyu_chat.group_memory import GroupMemory

TARGET = "qq-personal:GroupMessage:100000003"


class MemoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "group_memory.json"
        self.config = {
            "group_sessions": [TARGET],
            "group_memory_records": 50,
            "group_memory_summarize_every": 1000,
            "group_memory_daily_summary_budget": 24,
        }
        self.context = SimpleNamespace(
            get_current_chat_provider_id=AsyncMock(return_value="deepseek/test"),
            llm_generate=AsyncMock(
                return_value=LLMResponse(
                    role="assistant",
                    completion_text='{"summary":"群友甲明确说喜欢咖啡。"}',
                )
            ),
        )
        self.memory = GroupMemory(self.context, self.config, self.path)

    async def asyncTearDown(self):
        await self.memory.close()

    async def test_records_and_summary_survive_a_restart(self):
        await self.memory.record(TARGET, "member", "101", "群友甲", "我喜欢咖啡", "one")
        await self.memory.summarize(TARGET)
        reloaded = GroupMemory(self.context, self.config, self.path)
        self.assertEqual(reloaded.groups[TARGET]["records"][0]["sender_id"], "101")
        self.assertIn("喜欢咖啡", reloaded.render(TARGET))
        self.assertIn("群友甲", reloaded.groups[TARGET]["summary"])

    async def test_private_and_other_group_data_are_neither_saved_nor_retrieved(self):
        for origin in ["qq-personal:FriendMessage:101", "qq-personal:GroupMessage:123"]:
            await self.memory.record(
                origin, "member", "101", "name", "PRIVATE_SENTINEL", "one"
            )
            self.assertEqual(self.memory.render(origin), "")
        self.assertEqual(self.memory.groups, {})

    async def test_member_identity_is_not_merged_by_nickname(self):
        await self.memory.record(TARGET, "member", "101", "同名", "我喜欢咖啡", "one")
        await self.memory.record(TARGET, "member", "202", "同名", "我喜欢茶", "two")
        rendered = self.memory.render(TARGET)
        self.assertIn(self.memory.member_key(TARGET, "101"), rendered)
        self.assertIn(self.memory.member_key(TARGET, "202"), rendered)
        self.assertNotIn('"sender_id"', rendered)

    async def test_member_pseudonyms_survive_restart_and_are_group_specific(self):
        before = self.memory.member_key(TARGET, "101")
        reloaded = GroupMemory(self.context, self.config, self.path)
        self.assertEqual(before, reloaded.member_key(TARGET, "101"))
        self.assertNotEqual(
            before, reloaded.member_key("qq-personal:GroupMessage:123", "101")
        )

    async def test_saved_dates_remain_explicit_across_midnight(self):
        self.assertEqual(GroupMemory.time_label(1793026800), "2026-10-26T23:00+08:00")
        self.assertEqual(GroupMemory.time_label(None), "时间未记录")
        self.assertEqual(GroupMemory.time_label("bad"), "时间未记录")

    async def test_duplicate_message_ids_are_not_recorded_twice(self):
        await self.memory.record(TARGET, "member", "101", "name", "你好", "one")
        await self.memory.record(TARGET, "member", "101", "name", "你好", "one")
        self.assertEqual(len(self.memory.groups[TARGET]["records"]), 1)

    async def test_storage_and_injected_context_are_bounded(self):
        for index in range(70):
            await self.memory.record(
                TARGET, "member", "101", "name", f"message-{index}", str(index)
            )
        self.assertEqual(len(self.memory.groups[TARGET]["records"]), 50)
        rendered = self.memory.render(TARGET)
        self.assertNotIn('"text": "message-20"', rendered)
        self.assertIn('"text": "message-69"', rendered)
        self.assertEqual(rendered.count('"member_key"'), 12)

    async def test_bot_self_descriptions_are_not_fed_to_the_fact_summarizer(self):
        await self.memory.record(
            TARGET,
            "bot",
            "100000001",
            "旧机器人",
            "我叫旧名字，一天花很多钱",
            "bot:one",
        )
        await self.memory.record(TARGET, "member", "101", "name", "我喜欢咖啡", "two")
        await self.memory.summarize(TARGET)
        arguments = self.context.llm_generate.await_args.kwargs
        payload = json.loads(arguments["prompt"])
        self.assertEqual(len(payload["member_messages"]), 1)
        self.assertIn("member_key", payload["member_messages"][0])
        self.assertNotIn("sender_id", payload["member_messages"][0])
        self.assertNotIn("旧名字", arguments["prompt"])
        self.assertEqual(arguments["contexts"], [])

    async def test_old_quoted_bot_answers_and_keys_are_not_kept_as_member_text(self):
        text = (
            "我再问一句 <Quoted Message>我是旧角色，一天花十来块</Quoted Message> "
            + "sk-"
            + "abcdefghijklmnopqrstuv"
        )
        await self.memory.record(TARGET, "member", "101", "name", text, "one")
        saved = self.memory.groups[TARGET]["records"][0]["text"]
        self.assertNotIn("旧角色", saved)
        self.assertNotIn("sk-", saved)
        self.assertIn("密钥已隐去", saved)

    async def test_summary_failure_preserves_the_existing_facts(self):
        await self.memory.record(TARGET, "member", "101", "name", "我喜欢咖啡", "one")
        self.memory.groups[TARGET]["summary"] = "可靠的旧备忘"
        self.context.llm_generate.side_effect = RuntimeError("offline")
        await self.memory.summarize(TARGET)
        self.assertEqual(self.memory.groups[TARGET]["summary"], "可靠的旧备忘")

    async def test_daily_budget_blocks_additional_model_calls(self):
        self.config["group_memory_daily_summary_budget"] = 1
        await self.memory.record(TARGET, "member", "101", "name", "我喜欢咖啡", "one")
        await self.memory.summarize(TARGET)
        await self.memory.summarize(TARGET)
        self.context.llm_generate.assert_awaited_once()

    async def test_summary_is_background_batched_with_backoff(self):
        self.config["group_memory_summarize_every"] = 4
        for index in range(4):
            await self.memory.record(
                TARGET, "member", "101", "name", f"成员消息{index}", str(index)
            )
        tasks = list(self.memory.tasks.values())
        self.assertTrue(tasks)
        await asyncio.gather(*tasks)
        self.context.llm_generate.assert_awaited_once()
        for index in range(4, 8):
            await self.memory.record(
                TARGET, "member", "101", "name", f"成员消息{index}", str(index)
            )
        self.assertEqual(self.memory.tasks, {})
        self.context.llm_generate.assert_awaited_once()

    async def test_new_messages_during_summary_are_not_marked_as_already_summarized(
        self,
    ):
        started, release = asyncio.Event(), asyncio.Event()

        async def slow(**kwargs):
            started.set()
            await release.wait()
            return LLMResponse(
                role="assistant", completion_text='{"summary":"旧消息的摘要"}'
            )

        self.context.llm_generate.side_effect = slow
        await self.memory.record(TARGET, "member", "101", "name", "旧消息", "one")
        task = asyncio.create_task(self.memory.summarize(TARGET))
        await started.wait()
        await self.memory.record(TARGET, "member", "101", "name", "新更正", "two")
        release.set()
        await task
        self.assertEqual(self.memory.groups[TARGET]["pending"], 1)

    async def test_current_persona_and_fuller_memory_replace_only_the_short_scene_flow(
        self,
    ):
        from data.plugins.astrbot_plugin_dafeiyu_chat.main import DafeiyuChatPlugin

        await self.memory.record(TARGET, "member", "101", "群友甲", "我喜欢咖啡", "one")
        config = {**self.config, "enabled": True, "persona_id": "可爱鲸鱼娘小D"}
        plugin = DafeiyuChatPlugin(self.context, config)
        plugin.memory = self.memory
        event = SimpleNamespace(
            unified_msg_origin=TARGET,
            get_extra=lambda key: (
                "可爱鲸鱼娘小D" if key == "_trace_persona_id" else None
            ),
            get_platform_id=lambda: "qq-personal",
            is_private_chat=lambda: False,
            get_sender_id=lambda: "101",
            get_sender_name=lambda: "群友甲",
        )
        request = ProviderRequest(prompt="你好", system_prompt="PERSONA_SENTINEL")
        request.extra_user_content_parts.append(
            TextPart(
                text="<conversation_scene><talking_to>Bot</talking_to><recent_flow>short duplicate</recent_flow></conversation_scene>"
            )
        )
        await plugin.guide_reply(event, request)
        self.assertTrue(request.system_prompt.startswith("PERSONA_SENTINEL"))
        self.assertIn("当前使用原生角色卡", request.system_prompt)
        text = "\n".join(part.text for part in request.extra_user_content_parts)
        self.assertIn("talking_to", text)
        self.assertNotIn("short duplicate", text)
        self.assertIn("我喜欢咖啡", text)


if __name__ == "__main__":
    unittest.main()
