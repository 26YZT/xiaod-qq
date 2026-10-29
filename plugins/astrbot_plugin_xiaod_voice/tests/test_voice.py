"""Check single-channel replies, immutable bodies, and concurrent turn ordering."""

import asyncio
import math
import struct
import tempfile
import unittest
import wave
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from astrbot.api.event import AstrMessageEvent, ResultContentType
from astrbot.api.message_components import Plain, Record
from astrbot.api.platform import (
    AstrBotMessage,
    MessageMember,
    MessageType,
    PlatformMetadata,
)
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from data.plugins.astrbot_plugin_xiaod_voice import main as voice

PRIVATE = "qq-personal:FriendMessage:100000002"
GROUP = "qq-personal:GroupMessage:100000003"


class VoiceOnlyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.audio = self.root / "voice.wav"
        with wave.open(str(self.audio), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24000)
            output.writeframes(
                b"".join(
                    struct.pack(
                        "<h", int(4000 * math.sin(index / 24000 * 300 * math.tau))
                    )
                    for index in range(2400)
                )
            )
        self.config = {
            "enabled": True,
            "account_id": "100000001",
            "owner_id": "100000002",
            "private_sessions": [PRIVATE],
            "group_sessions": [GROUP],
            "private_moments_enabled": True,
            "private_auto_cooldown_minutes": 30,
            "private_auto_daily_limit": 3,
        }
        self.backend = SimpleNamespace(
            plugin_config=SimpleNamespace(
                api_key="test-placeholder",
                model="clone",
                base_url="test://local",
                default_context="Stable voice",
                emotion_contexts={},
            ),
            resolve_voice_id=lambda **_: "fixed-L",
            synthesize_text=AsyncMock(return_value=self.audio),
            voice_store=SimpleNamespace(
                get_voice=lambda _: SimpleNamespace(
                    type="clone",
                    audio_path=str(self.audio),
                    to_dict=lambda: {"id": "fixed-L", "style_context": "Fixed"},
                )
            ),
            studio_store=SimpleNamespace(settings=lambda _: {}),
        )
        self.context = SimpleNamespace(
            get_registered_star=lambda _: SimpleNamespace(
                activated=True, star_cls=self.backend
            ),
            get_current_chat_provider_id=AsyncMock(return_value="deepseek/test"),
            llm_generate=AsyncMock(
                return_value=LLMResponse(
                    role="assistant", completion_text='{"mode":"explicit"}'
                )
            ),
        )
        with patch.object(voice.StarTools, "get_data_dir", return_value=self.root):
            self.plugin = voice.XiaodVoice(self.context, self.config)
        self.counter = 0

    async def asyncTearDown(self):
        await self.plugin.terminate()

    def event(
        self,
        text="用语音说一句你好",
        *,
        private=True,
        target="",
        reply="你好，我是小D。",
    ):
        self.counter += 1
        message = AstrBotMessage()
        message.type = (
            MessageType.FRIEND_MESSAGE if private else MessageType.GROUP_MESSAGE
        )
        message.self_id = "100000001"
        message.session_id = target or ("100000002" if private else "100000003")
        message.group_id = "" if private else message.session_id
        message.message_id = str(self.counter)
        message.sender = MessageMember(user_id="100000002", nickname="Test")
        message.message = [Plain(text)]
        message.message_str = text
        message.raw_message = {}
        event = AstrMessageEvent(
            text,
            message,
            PlatformMetadata("aiocqhttp", "test", "qq-personal"),
            message.session_id,
        )
        result = event.plain_result(reply)
        result.set_result_content_type(ResultContentType.LLM_RESULT)
        event.set_result(result)
        return event

    async def test_explicit_reply_is_one_voice_without_early_text(self):
        event = self.event()
        await self.plugin.choose_voice(event)
        chain = event.get_result().chain
        self.assertEqual(len(chain), 1)
        self.assertIsInstance(chain[0], Record)
        self.assertEqual(chain[0].text, "你好，我是小D。")
        self.backend.synthesize_text.assert_awaited_once_with(
            "你好，我是小D。",
            voice_id="fixed-L",
            user_id="100000002",
            group_id=PRIVATE,
            emotion="neutral",
            style_director_enabled=False,
        )
        await self.plugin.mark_reply_sent(event)
        self.assertNotIn(PRIVATE, self.plugin.pending)

    async def test_ordinary_greeting_remains_text(self):
        self.context.llm_generate.return_value.completion_text = '{"mode":"text"}'
        event = self.event("你好。")
        await self.plugin.choose_voice(event)
        self.assertEqual(event.get_result().get_plain_text(), "你好，我是小D。")
        self.backend.synthesize_text.assert_not_awaited()

    async def test_music_progress_stays_text_without_tts(self):
        event = self.event("用语音唱这首歌", reply="正在准备歌曲")
        event.set_extra("_xiaod_music_text_only", True)
        await self.plugin.choose_voice(event)
        self.assertEqual(event.get_result().get_plain_text(), "正在准备歌曲")
        self.context.llm_generate.assert_not_awaited()
        self.backend.synthesize_text.assert_not_awaited()

    async def test_synthesis_failure_uses_the_same_body_without_regeneration(self):
        self.backend.synthesize_text.side_effect = RuntimeError("Synthetic error")
        event = self.event(reply="原文，只能是这一句。")
        await self.plugin.choose_voice(event)
        self.assertTrue(
            event.get_result().get_plain_text().endswith("原文，只能是这一句。")
        )
        self.backend.synthesize_text.assert_awaited_once()
        self.context.llm_generate.assert_awaited_once()
        self.assertFalse(
            any(isinstance(item, Record) for item in event.get_result().chain)
        )

    async def test_new_text_cancels_unsent_old_voice_without_old_text_fallback(self):
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def slow(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        self.backend.synthesize_text.side_effect = slow
        first = self.event(reply="第一轮内容")
        task = asyncio.create_task(self.plugin.choose_voice(first))
        await entered.wait()
        self.context.llm_generate.return_value.completion_text = '{"mode":"text"}'
        second = self.event("聊点别的", reply="第二轮内容")
        await self.plugin.choose_voice(second)
        await task
        self.assertTrue(cancelled.is_set())
        self.assertTrue(first.is_stopped())
        self.assertFalse(first.get_result().chain)
        self.assertEqual(second.get_result().get_plain_text(), "第二轮内容")

    async def test_new_voice_replaces_old_pending_voice(self):
        entered = asyncio.Event()

        async def synthesize(text, **kwargs):
            if text == "旧内容":
                entered.set()
                await asyncio.Event().wait()
            return self.audio

        self.backend.synthesize_text.side_effect = synthesize
        first = self.event(reply="旧内容")
        task = asyncio.create_task(self.plugin.choose_voice(first))
        await entered.wait()
        second = self.event("用语音说新的内容", reply="新内容")
        await self.plugin.choose_voice(second)
        await task
        self.assertFalse(first.get_result().chain)
        self.assertEqual(second.get_result().chain[0].text, "新内容")
        self.assertIsInstance(second.get_result().chain[0], Record)

    async def test_synthesis_that_ignores_cancel_still_cannot_deliver_stale_audio(self):
        entered = asyncio.Event()

        async def slow(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return self.audio

        self.backend.synthesize_text.side_effect = slow
        old = self.event(reply="不能再发的旧内容")
        task = asyncio.create_task(self.plugin.choose_voice(old))
        await entered.wait()
        self.context.llm_generate.return_value.completion_text = '{"mode":"text"}'
        current = self.event("换个话题", reply="当前内容")
        await self.plugin.choose_voice(current)
        await task
        self.assertFalse(old.get_result().chain)
        self.assertEqual(current.get_result().get_plain_text(), "当前内容")

    async def test_progress_question_keeps_the_original_voice(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow(*args, **kwargs):
            entered.set()
            await release.wait()
            return self.audio

        self.backend.synthesize_text.side_effect = slow
        first = self.event(reply="正在等待的正文")
        task = asyncio.create_task(self.plugin.choose_voice(first))
        await entered.wait()
        status = self.event("语音好了吗", reply="语音还在生成呢。")
        request = ProviderRequest(
            system_prompt="PERSONA",
            contexts=[{"role": "assistant", "content": "正在等待的正文"}],
        )
        await self.plugin.keep_voice_delivery_controlled(status, request)
        self.assertEqual(request.contexts, [])
        self.assertIn("仍在生成", request.system_prompt)
        self.context.llm_generate.return_value.completion_text = '{"mode":"status"}'
        await self.plugin.choose_voice(status)
        await self.plugin.mark_reply_sent(status)
        self.assertFalse(task.done())
        self.assertEqual(status.get_result().get_plain_text(), "语音还在生成呢。")
        self.backend.synthesize_text.assert_awaited_once()
        release.set()
        await task
        self.assertEqual(first.get_result().chain[0].text, "正在等待的正文")

    async def test_late_model_reply_cannot_overwrite_a_newer_turn(self):
        old, current = (
            self.event(reply="旧回复"),
            self.event("聊点别的", reply="新回复"),
        )
        await self.plugin.keep_voice_delivery_controlled(old, ProviderRequest())
        await self.plugin.keep_voice_delivery_controlled(current, ProviderRequest())
        self.context.llm_generate.return_value.completion_text = '{"mode":"text"}'
        await self.plugin.choose_voice(current)
        await self.plugin.choose_voice(old)
        self.assertFalse(old.get_result().chain)
        self.assertEqual(current.get_result().get_plain_text(), "新回复")
        self.backend.synthesize_text.assert_not_awaited()

    async def test_next_formal_reply_waits_for_ready_voice_delivery(self):
        first = self.event()
        await self.plugin.choose_voice(first)
        self.context.llm_generate.return_value.completion_text = '{"mode":"text"}'
        second = self.event("新问题", reply="新答案")
        task = asyncio.create_task(self.plugin.choose_voice(second))
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        await self.plugin.mark_reply_sent(first)
        await task
        self.assertEqual(second.get_result().get_plain_text(), "新答案")

    async def test_different_sessions_do_not_block_each_other(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def synthesize(text, **kwargs):
            if kwargs["group_id"] == PRIVATE:
                entered.set()
                await release.wait()
            return self.audio

        self.backend.synthesize_text.side_effect = synthesize
        private = self.event()
        task = asyncio.create_task(self.plugin.choose_voice(private))
        await entered.wait()
        group = self.event(private=False)
        await self.plugin.choose_voice(group)
        self.assertIsInstance(group.get_result().chain[0], Record)
        release.set()
        await task

    async def test_group_without_explicit_request_stays_text(self):
        event = self.event("聊聊今天吃什么", private=False)
        await self.plugin.choose_voice(event)
        self.context.llm_generate.assert_not_awaited()
        self.backend.synthesize_text.assert_not_awaited()

    async def test_group_moment_is_never_selected(self):
        self.context.llm_generate.return_value.completion_text = '{"mode":"moment"}'
        await self.plugin.choose_voice(self.event("这条语音真可爱", private=False))
        self.backend.synthesize_text.assert_not_awaited()

    async def test_other_groups_and_proactive_messages_are_ignored(self):
        await self.plugin.choose_voice(self.event(private=False, target="999"))
        event = self.event()
        event.message_obj.message_id = "proactive-test"
        await self.plugin.choose_voice(event)
        self.context.llm_generate.assert_not_awaited()

    async def test_automatic_voice_quota_is_persisted(self):
        self.context.llm_generate.return_value.completion_text = '{"mode":"moment"}'
        event = self.event("今天终于考上了", reply="这可得好好庆祝呀。")
        await self.plugin.choose_voice(event)
        self.assertEqual(self.plugin.state[PRIVATE]["count"], 1)
        with patch.object(voice.StarTools, "get_data_dir", return_value=self.root):
            reloaded = voice.XiaodVoice(self.context, self.config)
        self.context.llm_generate.reset_mock()
        await reloaded.choose_voice(self.event("继续聊聊"))
        self.context.llm_generate.assert_not_awaited()
        await reloaded.terminate()

    async def test_explicit_voice_bypasses_automatic_quota(self):
        self.plugin.state[PRIVATE] = {
            "day": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
            "count": 3,
            "last_attempt": datetime.now().timestamp(),
        }
        await self.plugin.choose_voice(self.event())
        self.backend.synthesize_text.assert_awaited_once()
        self.assertEqual(self.plugin.state[PRIVATE]["count"], 3)

    async def test_daily_cap_blocks_only_automatic_voice(self):
        self.plugin.state[PRIVATE] = {
            "day": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
            "count": 3,
            "last_attempt": 0,
        }
        await self.plugin.choose_voice(self.event("今天心情不错"))
        self.context.llm_generate.assert_not_awaited()
        self.backend.synthesize_text.assert_not_awaited()

    async def test_duplicate_hook_does_not_synthesize_twice(self):
        event = self.event()
        await self.plugin.choose_voice(event)
        await self.plugin.choose_voice(event)
        self.backend.synthesize_text.assert_awaited_once()

    async def test_wrong_capability_history_is_filtered_without_mutating_storage(self):
        contexts = [
            {
                "role": "assistant",
                "content": "我这会儿没有语音功能，只能打字问你一声好，抱歉。",
            },
            {
                "role": "assistant",
                "content": "你好，我是小弟。 （不过我现在发不了语音，只能把话打给你。）",
            },
            {"role": "assistant", "content": "我喜欢白饭。"},
        ]
        request = ProviderRequest(system_prompt="PERSONA_SENTINEL", contexts=contexts)
        await self.plugin.keep_voice_delivery_controlled(self.event(), request)
        self.assertTrue(request.system_prompt.startswith("PERSONA_SENTINEL"))
        self.assertEqual(request.contexts, [contexts[-1]])
        self.assertEqual(len(contexts), 3)
        self.assertIn("只发送一条语音", request.system_prompt)

    async def test_direct_voice_tools_remain_removed_per_request(self):
        tools = ToolSet(
            [
                FunctionTool(name=name, description=name, parameters={"type": "object"})
                for name in ["mimo_tts_speak", "mimo_tts_sing", "other_tool"]
            ]
        )
        request = ProviderRequest(func_tool=tools)
        await self.plugin.keep_voice_delivery_controlled(self.event(), request)
        self.assertEqual(
            [tool.name for tool in request.func_tool.tools], ["other_tool"]
        )
        self.assertEqual(len(tools.tools), 3)

    async def test_disabled_or_missing_voice_does_not_claim_capability(self):
        self.backend.resolve_voice_id = lambda **_: None
        request = ProviderRequest(system_prompt="PERSONA")
        await self.plugin.keep_voice_delivery_controlled(self.event(), request)
        self.assertEqual(request.system_prompt, "PERSONA")

    async def test_already_sent_text_is_not_followed_by_voice_or_a_stuck_fence(self):
        event = self.event()
        event.set_extra(
            "_send_message_to_user_current_session_plain_texts", ["你好，我是小D。"]
        )
        await self.plugin.choose_voice(event)
        self.assertNotIn(PRIVATE, self.plugin.pending)
        self.backend.synthesize_text.assert_not_awaited()

    async def test_voice_record_uses_supported_qq_transport(self):
        event = self.event()
        await self.plugin.choose_voice(event)
        segment = await AiocqhttpMessageEvent._from_segment_to_dict(
            event.get_result().chain[0]
        )
        self.assertEqual(segment["type"], "record")
        self.assertTrue(segment["data"]["file"].startswith("base64://"))

    async def test_channel_decision_starts_before_main_response_finishes(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def classify(**kwargs):
            entered.set()
            await release.wait()
            return LLMResponse(role="assistant", completion_text='{"mode":"explicit"}')

        self.context.llm_generate.side_effect = classify
        event = self.event()
        await self.plugin.keep_voice_delivery_controlled(event, ProviderRequest())
        await asyncio.wait_for(entered.wait(), timeout=1)
        self.backend.synthesize_text.assert_not_awaited()
        release.set()
        await self.plugin.choose_voice(event)
        self.context.llm_generate.assert_awaited_once()
        self.assertIsInstance(event.get_result().chain[0], Record)

    async def test_identical_reply_reuses_identical_audio_without_an_api_call(self):
        first = self.event()
        await self.plugin.choose_voice(first)
        await self.plugin.mark_reply_sent(first)
        second = self.event()
        await self.plugin.choose_voice(second)
        self.backend.synthesize_text.assert_awaited_once()
        self.assertEqual(
            first.get_result().chain[0].file, second.get_result().chain[0].file
        )
        self.assertTrue(self.plugin.pending[PRIVATE].cache_hit)

    async def test_changing_voice_style_invalidates_cached_audio(self):
        first = self.event()
        await self.plugin.choose_voice(first)
        await self.plugin.mark_reply_sent(first)
        self.backend.plugin_config.default_context = "New delivery instructions"
        await self.plugin.choose_voice(self.event())
        self.assertEqual(self.backend.synthesize_text.await_count, 2)

    async def test_changing_reference_invalidates_cached_audio(self):
        first = self.event()
        await self.plugin.choose_voice(first)
        await self.plugin.mark_reply_sent(first)
        with wave.open(str(self.audio), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24000)
            output.writeframes(b"\x00\x08" * 2400)
        await self.plugin.choose_voice(self.event())
        self.assertEqual(self.backend.synthesize_text.await_count, 2)

    async def test_changing_session_style_invalidates_cached_audio(self):
        first = self.event()
        await self.plugin.choose_voice(first)
        await self.plugin.mark_reply_sent(first)
        self.backend.studio_store.settings = lambda category: (
            {PRIVATE: {"style_id": "gentle"}}
            if category == "sessions"
            else {"gentle": {"context": "A different style"}}
        )
        await self.plugin.choose_voice(self.event())
        self.assertEqual(self.backend.synthesize_text.await_count, 2)

    async def test_long_silent_gap_is_rejected_without_another_generation(self):
        with wave.open(str(self.audio), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24000)
            output.writeframes(b"\x00\x08" * 12000 + b"\x00\x00" * 96000)
        event = self.event(reply="保持同一份回复正文")
        await self.plugin.choose_voice(event)
        self.assertTrue(
            event.get_result().get_plain_text().endswith("保持同一份回复正文")
        )
        self.backend.synthesize_text.assert_awaited_once()
        self.assertFalse(self.plugin.audio_cache)

    async def test_abnormally_long_audio_for_short_text_is_rejected(self):
        with wave.open(str(self.audio), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24000)
            output.writeframes(b"\x00\x08" * (24000 * 29))
        event = self.event()
        await self.plugin.choose_voice(event)
        self.assertFalse(
            any(isinstance(item, Record) for item in event.get_result().chain)
        )
        self.backend.synthesize_text.assert_awaited_once()

    async def test_silent_audio_is_never_delivered_or_cached(self):
        with wave.open(str(self.audio), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24000)
            output.writeframes(b"\x00\x00" * 24000)
        event = self.event()
        await self.plugin.choose_voice(event)
        self.assertFalse(
            any(isinstance(item, Record) for item in event.get_result().chain)
        )
        self.assertFalse(self.plugin.audio_cache)

    async def test_total_deadline_cancels_synthesis_and_keeps_original_text(self):
        original_wait = asyncio.wait_for
        budgets = []
        cancelled = asyncio.Event()

        async def synthesize(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        async def bounded(awaitable, timeout):
            if timeout == 25:
                budgets.append(timeout)
                return await original_wait(awaitable, timeout=0.01)
            return await original_wait(awaitable, timeout=timeout)

        self.backend.synthesize_text.side_effect = synthesize
        event = self.event(reply="超时也保留的原文")
        with patch.object(voice.asyncio, "wait_for", side_effect=bounded):
            await self.plugin.choose_voice(event)
        self.assertEqual(budgets, [25])
        self.assertTrue(cancelled.is_set())
        self.assertTrue(
            event.get_result().get_plain_text().endswith("超时也保留的原文")
        )


if __name__ == "__main__":
    unittest.main()
