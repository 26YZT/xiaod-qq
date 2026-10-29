"""Exercise real AstrBot events without network or QQ sends."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import At, Plain, Reply
from astrbot.api.platform import (
    AstrBotMessage,
    MessageMember,
    MessageType,
    PlatformMetadata,
)
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.core.agent.message import TextPart
from astrbot.core.agent.tool import FunctionTool, ToolSet
from data.plugins.astrbot_plugin_dafeiyu_chat import main as plugin_module

TARGET = "qq-personal:GroupMessage:100000003"


class ChatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sequence = 0
        self.context = SimpleNamespace(
            get_config=lambda **kwargs: {"wake_prefix": ["/"]},
            get_current_chat_provider_id=AsyncMock(return_value="deepseek/test"),
            llm_generate=AsyncMock(
                return_value=LLMResponse(
                    role="assistant", completion_text='{"reply":true}'
                )
            ),
            conversation_manager=SimpleNamespace(
                get_curr_conversation_id=AsyncMock(return_value="conversation"),
                get_conversation=AsyncMock(
                    return_value=SimpleNamespace(persona_id=None)
                ),
            ),
        )
        self.config = {
            "enabled": True,
            "group_sessions": [TARGET],
            "decision_interval_seconds": 10,
            "bot_names": ["小D", "大肥鱼"],
            "related_topics": ["雨天休闲", "海玻璃收藏"],
            "cooldown_seconds": 90,
            "max_proactive_per_hour": 8,
            "persona_id": "可爱鲸鱼娘小D",
            "casual_min_chars": 15,
            "casual_max_chars": 60,
        }
        self.plugin = plugin_module.DafeiyuChatPlugin(self.context, self.config)
        self.clock = patch.object(plugin_module, "monotonic", return_value=10000.0)
        self.time = self.clock.start()
        self.addCleanup(self.clock.stop)

    async def test_search_guidance_preserves_persona_and_requires_real_sources(self):
        request = ProviderRequest(
            prompt="联网查一下",
            system_prompt="PERSONA_SENTINEL",
            func_tool=ToolSet(
                [
                    FunctionTool(
                        name="web_search_deepseek",
                        description="Search public pages",
                        parameters={"type": "object"},
                    )
                ]
            ),
        )
        await self.plugin.guide_reply(self.event(), request)
        self.assertTrue(request.system_prompt.startswith("PERSONA_SENTINEL"))
        self.assertIn("先查再答", request.system_prompt)
        self.assertIn("不可信资料", request.system_prompt)
        self.assertIn("不把普通朗读", request.system_prompt)
        self.assertEqual(
            [tool.name for tool in request.func_tool.tools], ["web_search_deepseek"]
        )

    def event(
        self,
        text="大家觉得雨天适合做什么？",
        *,
        group="100000003",
        sender="101",
        wake=False,
    ):
        self.sequence += 1
        message = AstrBotMessage()
        message.type = (
            MessageType.GROUP_MESSAGE if group else MessageType.FRIEND_MESSAGE
        )
        message.self_id = "100000001"
        message.session_id = group or sender
        message.message_id = str(self.sequence)
        message.group_id = group
        message.sender = MessageMember(user_id=sender, nickname="测试群友")
        message.message = [Plain(text)] if text else []
        message.message_str = text
        message.raw_message = {}
        event = AstrMessageEvent(
            text,
            message,
            PlatformMetadata("aiocqhttp", "test", "qq-personal"),
            group or sender,
        )
        event.is_at_or_wake_command = wake
        event.set_extra("_trace_persona_id", "可爱鲸鱼娘小D")
        return event

    async def consume(self, event):
        return [request async for request in self.plugin.observe_group(event)]

    async def test_first_related_message_can_use_the_normal_agent_pipeline(self):
        event = self.event()
        requests = await self.consume(event)
        self.assertEqual(len(requests), 1)
        self.assertIsInstance(requests[0], ProviderRequest)
        self.assertFalse(event.is_at_or_wake_command)
        self.assertFalse(event.is_stopped())
        self.context.llm_generate.assert_awaited_once()
        self.assertIsNotNone(requests[0].conversation)

    async def test_negative_or_malformed_gate_stays_quiet(self):
        for index, response in enumerate(
            ['{"reply":false}', '{"reply":"true"}', "我来接话", "[]"]
        ):
            with self.subTest(response=response):
                self.time.return_value = 10000 + index * 11
                self.context.llm_generate.return_value = LLMResponse(
                    role="assistant", completion_text=response
                )
                self.assertEqual(await self.consume(self.event()), [])
                self.assertFalse(self.plugin.states[TARGET].busy)

    async def test_private_other_groups_self_media_and_commands_do_not_call_the_model(
        self,
    ):
        for event in [
            self.event(group="123"),
            self.event(group=""),
            self.event(sender="100000001"),
            self.event(text=""),
            self.event(text="/help"),
        ]:
            self.assertEqual(await self.consume(event), [])
        self.assertEqual(len(self.plugin.states), 1)
        self.context.llm_generate.assert_not_awaited()

    async def test_duplicate_message_id_is_not_evaluated_or_recorded_twice(self):
        event = self.event()
        await self.consume(event)
        self.time.return_value = 10011
        self.assertEqual(await self.consume(event), [])
        self.assertEqual(len(self.plugin.states[TARGET].history), 1)
        self.context.llm_generate.assert_awaited_once()

    async def test_mention_other_person_stays_in_context_without_triggering(self):
        event = self.event()
        event.message_obj.message.insert(0, At(qq="202"))
        self.assertEqual(await self.consume(event), [])
        self.context.llm_generate.assert_not_awaited()
        self.assertTrue(self.plugin.states[TARGET].history[-1]["addressed_elsewhere"])

    async def test_cooldown_keeps_mentions_immediate_and_resumes_without_a_quota(self):
        event = self.event()
        await self.consume(event)
        await self.plugin.remember_reply(
            event, LLMResponse(role="assistant", completion_text="接上一句。")
        )
        await self.plugin.remember_sent_group_reply(event)
        self.time.return_value = 10020
        self.assertEqual(await self.consume(self.event()), [])
        mention = self.event(wake=True)
        self.assertEqual(await self.consume(mention), [])
        self.assertTrue(mention.is_at_or_wake_command)
        self.assertFalse(mention.is_stopped())
        self.time.return_value = 10111
        self.assertEqual(len(await self.consume(self.event())), 1)

    async def test_explicit_request_cancels_an_inflight_gate(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def slow_gate(**kwargs):
            started.set()
            await release.wait()
            return LLMResponse(role="assistant", completion_text='{"reply":true}')

        self.context.llm_generate.side_effect = slow_gate
        task = asyncio.create_task(self.consume(self.event()))
        await started.wait()
        await self.consume(self.event(wake=True))
        release.set()
        self.assertEqual(await task, [])

    async def test_busy_gate_discards_old_input_and_evaluates_the_latest_event(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def slow_gate(**kwargs):
            started.set()
            await release.wait()
            return LLMResponse(role="assistant", completion_text='{"reply":true}')

        self.context.llm_generate.side_effect = slow_gate
        task = asyncio.create_task(self.consume(self.event()))
        await started.wait()
        self.time.return_value = 10011
        newest = asyncio.create_task(self.consume(self.event()))
        await asyncio.sleep(0)
        self.context.llm_generate.assert_awaited_once()
        release.set()
        old_result, new_result = await asyncio.gather(task, newest)
        self.assertEqual(old_result, [])
        self.assertEqual(len(new_result), 1)
        self.assertEqual(self.context.llm_generate.await_count, 2)

    async def test_hour_budget_prevents_more_model_calls(self):
        self.config["max_proactive_per_hour"] = 1
        self.assertEqual(len(await self.consume(self.event())), 1)
        self.time.return_value = 10100
        self.assertEqual(await self.consume(self.event()), [])
        self.context.llm_generate.assert_awaited_once()

    async def test_long_silence_drops_stale_group_context(self):
        self.context.llm_generate.return_value = LLMResponse(
            role="assistant", completion_text='{"reply":false}'
        )
        await self.consume(self.event(text="很久以前的话题"))
        self.time.return_value = 10601
        await self.consume(self.event(text="新的话题"))
        payload = json.loads(self.context.llm_generate.await_args.kwargs["prompt"])
        self.assertEqual([item["text"] for item in payload["messages"]], ["新的话题"])

    async def test_length_guidance_preserves_persona_and_allows_long_tasks(self):
        for event in [self.event(), self.event(group="")]:
            request = ProviderRequest(prompt="你好", system_prompt="PERSONA_SENTINEL")
            await self.plugin.guide_reply(event, request)
            self.assertTrue(request.system_prompt.startswith("PERSONA_SENTINEL"))
            self.assertIn("15～60", request.system_prompt)
            self.assertIn("不截断信息或代码", request.system_prompt)
        request = ProviderRequest(prompt="你好", system_prompt="OTHER_GROUP")
        await self.plugin.guide_reply(self.event(group="123"), request)
        self.assertEqual(request.system_prompt, "OTHER_GROUP")

    async def test_stale_autonomous_result_is_never_sent(self):
        event = self.event()
        await self.consume(event)
        await self.consume(self.event(wake=True))
        event.set_result(event.plain_result("过时的主动回复"))
        await self.plugin.cancel_obsolete_reply(event)
        self.assertTrue(event.is_stopped())
        self.assertFalse(event.get_result().chain)

    async def test_api_failure_does_not_emit_error_to_group(self):
        self.context.llm_generate.side_effect = RuntimeError("offline")
        self.assertEqual(await self.consume(self.event()), [])
        self.assertFalse(self.plugin.states[TARGET].busy)

    async def test_cancelled_generation_does_not_enter_the_scene_history(self):
        event = self.event()
        await self.consume(event)
        await self.consume(self.event(wake=True))
        response = LLMResponse(role="assistant", completion_text="已经过时的生成内容")
        await self.plugin.remember_reply(event, response)
        self.assertTrue(event.is_stopped())
        self.assertEqual(response.completion_text, "")
        self.assertFalse(
            any(
                item["text"] == "已经过时的生成内容"
                for item in self.plugin.states[TARGET].history
            )
        )

    async def test_valid_response_is_not_cancelled_by_its_own_revision_update(self):
        event = self.event()
        await self.consume(event)
        response = LLMResponse(
            role="assistant", completion_text="这天气适合慢慢听首歌。"
        )
        await self.plugin.remember_reply(event, response)
        event.set_result(event.plain_result(response.completion_text))
        await self.plugin.cancel_obsolete_reply(event)
        self.assertFalse(event.is_stopped())
        self.assertTrue(event.get_result().chain)

    async def test_disabled_configuration_preserves_normal_message_behavior(self):
        self.config["enabled"] = False
        self.assertEqual(await self.consume(self.event()), [])
        self.assertEqual(self.plugin.states, {})
        self.context.llm_generate.assert_not_awaited()
        request = ProviderRequest(prompt="你好", system_prompt="ORIGINAL")
        await self.plugin.guide_reply(self.event(group=""), request)
        self.assertEqual(request.system_prompt, "ORIGINAL")

    async def test_prior_bot_turn_and_topic_hints_reach_the_semantic_judge(self):
        event = self.event()
        await self.consume(event)
        await self.plugin.remember_reply(
            event, LLMResponse(role="assistant", completion_text="听雨点敲窗很舒服。")
        )
        await self.plugin.remember_sent_group_reply(event)
        self.time.return_value = 10091
        await self.consume(self.event(text="你刚说的那个还有别的玩法吗？"))
        payload = json.loads(self.context.llm_generate.await_args.kwargs["prompt"])
        self.assertEqual(payload["related_topics"], self.config["related_topics"])
        self.assertEqual(payload["bot_names"], self.config["bot_names"])
        self.assertTrue(any(item["role"] == "bot" for item in payload["messages"]))
        self.assertEqual(
            payload["messages"][-1]["text"], "你刚说的那个还有别的玩法吗？"
        )

    async def test_decision_throttle_evaluates_the_last_message_after_the_delay(self):
        self.context.llm_generate.return_value = LLMResponse(
            role="assistant", completion_text='{"reply":false}'
        )
        await self.consume(self.event(text="第一句"))
        self.time.return_value = 10005
        original_sleep = asyncio.sleep

        async def advance(seconds):
            self.time.return_value += seconds
            await original_sleep(0)

        with patch.object(plugin_module.asyncio, "sleep", side_effect=advance) as delay:
            await self.consume(self.event(text="第二句"))
        delay.assert_awaited_once_with(5)
        self.assertEqual(self.context.llm_generate.await_count, 2)
        self.time.return_value = 10021
        await self.consume(self.event(text="第三句"))
        self.assertEqual(self.context.llm_generate.await_count, 3)
        payload = json.loads(self.context.llm_generate.await_args.kwargs["prompt"])
        self.assertEqual(len(payload["messages"]), 3)

    async def test_quoted_other_member_is_not_automatically_answered(self):
        for sender in (202, 0):
            event = self.event(text="这个你再看一下")
            event.message_obj.message.insert(0, Reply(id=str(sender), sender_id=sender))
            self.assertEqual(await self.consume(event), [])
        self.context.llm_generate.assert_not_awaited()

    async def test_single_decision_cannot_conflict_with_a_separate_reply_flag(self):
        for index, (choice, expected) in enumerate(
            (
                ("quiet", False),
                ("to_bot", True),
                ("public_question", True),
                ("shared_topic", True),
                ("character_banter", True),
                ("celebration", True),
                ("unsupported", False),
            )
        ):
            self.time.return_value = 10000 + index * 11
            self.context.llm_generate.return_value.completion_text = json.dumps(
                {"decision": choice}
            )
            result = await self.consume(self.event())
            self.assertEqual(bool(result), expected)

    async def test_a_burst_evaluates_the_last_message_instead_of_dropping_it(self):
        self.context.llm_generate.return_value.completion_text = '{"decision":"quiet"}'
        await self.consume(self.event(text="第一句"))
        waiting, release = asyncio.Event(), asyncio.Event()
        original_sleep = asyncio.sleep

        async def hold_delay(seconds):
            waiting.set()
            await release.wait()

        with patch.object(plugin_module.asyncio, "sleep", side_effect=hold_delay):
            self.time.return_value = 10005
            old = asyncio.create_task(self.consume(self.event(text="第二句")))
            await waiting.wait()
            self.time.return_value = 10006
            newest = asyncio.create_task(self.consume(self.event(text="最后一句")))
            await original_sleep(0)
            self.time.return_value = 10010
            release.set()
            await asyncio.gather(old, newest)
        self.assertEqual(self.context.llm_generate.await_count, 2)
        payload = json.loads(self.context.llm_generate.await_args.kwargs["prompt"])
        self.assertEqual(payload["messages"][-1]["text"], "最后一句")

    async def test_participation_uses_the_same_selected_persona_as_the_native_runner(
        self,
    ):
        resolver = AsyncMock(
            return_value=(
                "selected-card",
                {"prompt": "SELECTED_CHARACTER_REFERENCE"},
                None,
                False,
            )
        )
        self.context.persona_manager = SimpleNamespace(
            resolve_selected_persona=resolver
        )
        await self.consume(self.event())
        payload = json.loads(self.context.llm_generate.await_args.kwargs["prompt"])
        self.assertEqual(
            payload["persona_reference"],
            {"id": "selected-card", "character": "SELECTED_CHARACTER_REFERENCE"},
        )
        self.assertIn("speaker_key", payload["messages"][0])
        self.assertIn("age_seconds", payload["messages"][0])
        self.assertNotIn("at", payload["messages"][0])

    async def test_failed_or_suppressed_generation_does_not_become_a_bot_turn(self):
        event = self.event()
        await self.consume(event)
        await self.plugin.remember_reply(
            event, LLMResponse(role="assistant", completion_text="Never sent")
        )
        self.assertFalse(
            any(item["role"] == "bot" for item in self.plugin.states[TARGET].history)
        )
        await self.consume(self.event(wake=True))
        event.set_result(event.plain_result("Never sent"))
        await self.plugin.cancel_obsolete_reply(event)
        await self.plugin.remember_sent_group_reply(event)
        self.assertFalse(
            any(item["role"] == "bot" for item in self.plugin.states[TARGET].history)
        )

    async def test_group_style_preserves_the_card_without_changing_private_guidance(
        self,
    ):
        group = ProviderRequest(prompt="普通群聊", system_prompt="PERSONA_SENTINEL")
        private = ProviderRequest(prompt="普通私聊", system_prompt="PERSONA_SENTINEL")
        await self.plugin.guide_reply(self.event(), group)
        await self.plugin.guide_reply(self.event(group=""), private)
        self.assertTrue(group.system_prompt.startswith("PERSONA_SENTINEL"))
        self.assertIn("同一个角色", group.system_prompt)
        self.assertIn("平台、套餐、价格、额度", group.system_prompt)
        self.assertNotIn("120～300", group.system_prompt)
        self.assertIn("120～300", private.system_prompt)
        self.assertNotIn("同一个角色", private.system_prompt)

    async def test_scene_attribution_remains_after_removing_the_second_participation_judge(
        self,
    ):
        event = self.event()
        await self.consume(event)
        scene = "<conversation_scene><sender>GROUP_MEMBER</sender><instruction>OLD_SECOND_JUDGE</instruction><talking_to>group</talking_to></conversation_scene>"
        request = ProviderRequest(
            prompt="你好",
            system_prompt="PERSONA_SENTINEL"
            + scene
            + "<instruction>UNRELATED</instruction>",
        )
        request.extra_user_content_parts.append(TextPart(text=scene))
        await self.plugin.guide_reply(event, request)
        self.assertNotIn("OLD_SECOND_JUDGE", request.system_prompt)
        self.assertIn("GROUP_MEMBER", request.system_prompt)
        self.assertIn("UNRELATED", request.system_prompt)
        self.assertNotIn("OLD_SECOND_JUDGE", request.extra_user_content_parts[0].text)

    async def test_a_more_recent_message_cancels_an_old_unaddressed_response(self):
        event = self.event()
        await self.consume(event)
        self.time.return_value = 10011
        self.context.llm_generate.return_value.completion_text = '{"reply":false}'
        await self.consume(self.event(text="换个话题，我去取快递了"))
        response = LLMResponse(role="assistant", completion_text="Old topic answer")
        await self.plugin.remember_reply(event, response)
        self.assertTrue(event.is_stopped())
        self.assertEqual(response.completion_text, "")

    async def test_sent_bot_turns_keep_their_addressee_and_are_committed_once(self):
        event = self.event()
        await self.consume(event)
        await self.plugin.remember_reply(
            event,
            LLMResponse(role="assistant", completion_text="Original generated body"),
        )
        event.set_result(event.plain_result("Actually sent body"))
        await self.plugin.remember_sent_group_reply(event)
        await self.plugin.remember_sent_group_reply(event)
        bots = [
            item for item in self.plugin.states[TARGET].history if item["role"] == "bot"
        ]
        self.assertEqual(len(bots), 1)
        self.assertEqual(bots[0]["text"], "Actually sent body")
        self.assertIn("to_speaker_key", bots[0])


if __name__ == "__main__":
    unittest.main()
