"""Check explicit song requests, bounded delivery, cancellation, and isolation."""

import asyncio
import hashlib
import json
import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot.api.event import AstrMessageEvent, ResultContentType
from astrbot.api.message_components import Plain, Record
from astrbot.api.platform import (
    AstrBotMessage,
    MessageMember,
    MessageType,
    PlatformMetadata,
)
from astrbot.api.provider import LLMResponse
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.star_handler import star_handlers_registry
from data.plugins.astrbot_plugin_mimo_tts_clone.main import MimoTTSClonePlugin
from data.plugins.astrbot_plugin_xiaod_music import main as music
from data.plugins.astrbot_plugin_xiaod_voice import main as voice

PRIVATE = "qq-personal:FriendMessage:100000002"
GROUP = "qq-personal:GroupMessage:100000003"


class MusicTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.reference = self.root / "reference.wav"
        self.source = self.root / "source.wav"
        self.write_audio(self.reference)
        self.write_audio(self.source, frequency=400)
        self.catalog = {
            "voice_id": "test-L",
            "reference": str(self.reference),
            "reference_sha256": hashlib.sha256(self.reference.read_bytes()).hexdigest(),
            "preset": "L-singing-scheme-2",
            "songs": [
                {
                    "title": "琵琶曲",
                    "source": str(self.source),
                    "duration": 3,
                    "source_sha256": hashlib.sha256(
                        self.source.read_bytes()
                    ).hexdigest(),
                    "clip_start": 0,
                }
            ],
        }
        (self.root / "catalog.json").write_text(json.dumps(self.catalog))
        self.backend = SimpleNamespace(resolve_voice_id=lambda **_: "test-L")
        self.context = SimpleNamespace(
            get_registered_star=lambda _: SimpleNamespace(
                activated=True, star_cls=self.backend
            ),
            get_current_chat_provider_id=AsyncMock(return_value="test/model"),
            llm_generate=AsyncMock(
                return_value=LLMResponse(
                    role="assistant", completion_text='{"mode":"default"}'
                )
            ),
            send_message=AsyncMock(return_value=True),
            get_config=lambda **_: {"provider_settings": {"web_search": False}},
        )
        self.config = {
            "enabled": True,
            "account_id": "100000001",
            "owner_id": "100000002",
            "private_sessions": [PRIVATE],
            "group_sessions": [GROUP],
            "default_full_source": True,
        }
        with patch.object(music.StarTools, "get_data_dir", return_value=self.root):
            self.plugin = music.XiaodMusic(self.context, self.config)
        self.plugin.project = self.root
        self.sequence = 0

    async def asyncTearDown(self):
        await self.plugin.terminate()

    def write_audio(self, path, seconds=1, frequency=300):
        """Create audible PCM fixtures used by cache and native transport checks.

        Args:
            path: Fixture destination.
            seconds: Exact fixture duration.
            frequency: Tone frequency used to distinguish source hashes.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(
                b"".join(
                    struct.pack(
                        "<h", int(3000 * math.sin(i / 8000 * frequency * math.tau))
                    )
                    for i in range(int(8000 * seconds))
                )
            )

    def event(
        self, text="小D，唱一首琵琶曲", group=False, target=None, sender="100000002"
    ):
        """Build a real framework event without a real QQ sender.

        Args:
            text: Incoming text.
            group: Whether this is a group conversation.
            target: Optional alternate conversation id.
            sender: Requesting QQ id.

        Returns:
            AstrMessageEvent: An unsent synthetic event.
        """
        self.sequence += 1
        message = AstrBotMessage()
        message.type = (
            MessageType.GROUP_MESSAGE if group else MessageType.FRIEND_MESSAGE
        )
        message.self_id = "100000001"
        message.session_id = target or ("100000003" if group else sender)
        message.group_id = message.session_id if group else ""
        message.message_id = str(self.sequence)
        message.sender = MessageMember(user_id=sender, nickname="Test")
        message.message_str, message.message = text, [Plain(text)]
        message.raw_message = {}
        event = AstrMessageEvent(
            text,
            message,
            PlatformMetadata("aiocqhttp", "test", "qq-personal"),
            message.session_id,
        )
        result = event.plain_result("模型额外写的正文，不应被播放")
        result.set_result_content_type(ResultContentType.LLM_RESULT)
        event.set_result(result)
        return event

    def make_ready(self, full=True, online=False):
        """Create a complete content-addressed cache using short valid records.

        Args:
            full: Whether to prepare full-source or excerpt fixtures.
            online: Whether to prepare an independently keyed accompanied cover.

        Returns:
            Path: Completed cache directory.
        """
        key = hashlib.sha256(
            json.dumps(
                {
                    "song": "online:琵琶曲"
                    if online
                    else self.catalog["songs"][0]["source_sha256"],
                    "reference": self.catalog["reference_sha256"],
                    "preset": self.catalog["preset"]
                    + ("+accompaniment-v1" if online else ""),
                    "full": full,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        directory = self.root / "cache" / key
        directory.mkdir(parents=True, exist_ok=True)
        names = ["part001.wav", "part002.wav"] if full else ["part001.wav"]
        for name in names:
            self.write_audio(directory / name)
        ready = {
            "cache_key": key,
            "reference_sha256": self.catalog["reference_sha256"],
            "duration": len(names),
            "parts": names,
            "title": "琵琶曲",
            "full_source": full,
            "has_accompaniment": online,
        }
        (directory / "ready.json").write_text(json.dumps(ready))
        return directory

    async def test_accompanied_cover_never_silently_uses_the_old_dry_cache(self):
        dry = self.make_ready()
        mixed = self.make_ready(online=True)
        self.config["online_accompaniment_enabled"] = True
        event = self.event()
        result = json.loads(await self.plugin.request_song(event, "琵琶曲"))
        self.assertTrue(result["has_accompaniment"])
        self.assertNotEqual(dry, mixed)
        self.assertEqual(Path(event.get_extra(music.DELIVERY)["directory"]), mixed)
        await self.plugin.deliver_cached_song(event)
        self.assertIn("带伴奏版", event.get_result().get_plain_text())
        self.assertEqual(
            sum(isinstance(c, Record) for c in event.get_result().chain), 2
        )

    async def test_explicit_a_cappella_keeps_the_accepted_local_source_cache(self):
        dry = self.make_ready()
        self.config["online_accompaniment_enabled"] = True
        event = self.event("清唱琵琶曲")
        result = json.loads(
            await self.plugin.request_song(event, "琵琶曲", with_accompaniment=False)
        )
        self.assertEqual(result["status"], "ready_to_send")
        self.assertFalse(result["has_accompaniment"])
        self.assertEqual(Path(event.get_extra(music.DELIVERY)["directory"]), dry)

    async def test_new_song_is_queued_for_automatic_source_preparation(self):
        self.config["online_accompaniment_enabled"] = True
        with patch.object(self.plugin, "_run_job", new_callable=AsyncMock):
            result = json.loads(
                await self.plugin.request_song(self.event("唱一首茉莉花"), "茉莉花")
            )
            self.assertEqual(result["status"], "queued")
            request = self.plugin.jobs[PRIVATE].request
            self.assertTrue(request["online_source"])
            self.assertTrue(request["full_source"])
            self.assertEqual(request["voice_id"], "test-L")
            self.assertEqual(
                request["reference_sha256"], self.catalog["reference_sha256"]
            )
            self.assertNotIn("source", request)
        self.context.send_message.assert_not_awaited()

    async def test_online_path_still_checks_the_unchanged_L_reference(self):
        self.config["online_accompaniment_enabled"] = True
        self.write_audio(self.reference, frequency=900)
        result = json.loads(await self.plugin.request_song(self.event(), "琵琶曲"))
        self.assertEqual(result["status"], "reference_changed")
        self.assertFalse(self.plugin.jobs)

    async def test_online_music_rejects_links_and_unrequested_group_discussion(self):
        self.config["online_accompaniment_enabled"] = True
        result = json.loads(
            await self.plugin.request_song(self.event(), "https://127.0.0.1/private")
        )
        self.assertEqual(result["status"], "invalid_title")
        self.context.llm_generate.return_value.completion_text = '{"mode":"none"}'
        result = json.loads(
            await self.plugin.request_song(
                self.event("茉莉花是什么歌", group=True), "茉莉花"
            )
        )
        self.assertEqual(result["status"], "not_requested")
        self.assertFalse(self.plugin.jobs)

    async def test_source_failure_does_not_fall_back_to_dry_audio_or_start_rendering(
        self,
    ):
        self.config["online_accompaniment_enabled"] = True
        self.make_ready()
        process = SimpleNamespace(wait=AsyncMock(return_value=1), returncode=1)
        with patch.object(
            music.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
        ) as spawn:
            event = self.event()
            await self.plugin.request_song(event, "琵琶曲")
            job = self.plugin.jobs[PRIVATE]
            job.acknowledged.set()
            await asyncio.wait_for(job.task, timeout=2)
        spawn.assert_awaited_once()
        self.assertEqual(self.plugin.last[PRIVATE]["stage"], "preparing_source")
        calls = self.context.send_message.await_args_list
        self.assertEqual(len(calls), 1)
        self.assertIn("带伴奏音源", calls[0].args[1].chain[0].text)
        self.assertFalse(any(isinstance(c, Record) for c in calls[0].args[1].chain))

    async def test_online_pipeline_runs_source_then_render_and_waits_for_ack(self):
        self.config["online_accompaniment_enabled"] = True
        owner = self
        stages = []

        class Process:
            returncode = None

            def __init__(self, args):
                self.args = args

            async def wait(self):
                job = owner.plugin.jobs[PRIVATE]
                if self.args[1].endswith("prepare_online_song.py"):
                    stages.append("source")
                    source = owner.root / "sources/recording/vocals.wav"
                    backing = source.with_name("instrumental.wav")
                    owner.write_audio(source)
                    owner.write_audio(backing, frequency=800)
                    prepared = {
                        "source": str(source),
                        "source_sha256": "source-hash",
                        "accompaniment": str(backing),
                        "accompaniment_sha256": "backing-hash",
                        "duration": 2,
                        "clip_start": 0,
                        "public_source": {"provider": "synthetic"},
                    }
                    (job.directory / "prepared_source.json").write_text(
                        json.dumps(prepared)
                    )
                else:
                    stages.append("render")
                    owner.make_ready(online=True)
                self.returncode = 0
                return 0

        async def spawn(*args, **kwargs):
            return Process(args)

        with patch.object(music.asyncio, "create_subprocess_exec", side_effect=spawn):
            event = self.event()
            await self.plugin.request_song(event, "琵琶曲")
            job = self.plugin.jobs[PRIVATE]
            job.acknowledged.set()
            await asyncio.wait_for(job.task, timeout=2)
        self.assertEqual(stages, ["source", "render"])
        self.assertEqual(self.plugin.last[PRIVATE]["status"], "completed")
        self.assertEqual(len(self.context.send_message.await_args_list), 3)
        self.assertIn(
            "带伴奏版",
            self.context.send_message.await_args_list[0].args[1].chain[0].text,
        )

    async def test_progress_reports_only_known_processing_stages(self):
        directory = self.root / "cache/progress"
        directory.mkdir(parents=True)
        job = music.SingingJob(PRIVATE, "100000002", "琵琶曲", True, directory, {})
        self.plugin.jobs[PRIVATE] = job
        for stage in ("separating", "singing", "mixing", "untrusted arbitrary output"):
            (directory / "progress.json").write_text(json.dumps({"stage": stage}))
            result = json.loads(
                await self.plugin.request_song(self.event("点歌进度"), action="status")
            )
            self.assertEqual(
                result["stage"], None if stage.startswith("untrusted") else stage
            )

    async def test_cached_full_source_uses_native_records_without_generating_again(
        self,
    ):
        self.make_ready()
        event = self.event()
        result = json.loads(await self.plugin.request_song(event, "琵琶曲"))
        self.assertEqual(result["status"], "ready_to_send")
        self.assertTrue(result["full_source"])
        self.context.send_message.assert_not_awaited()
        await self.plugin.deliver_cached_song(event)
        records = [c for c in event.get_result().chain if isinstance(c, Record)]
        self.assertEqual(len(records), 2)
        self.assertNotIn("模型额外写的正文", event.get_result().get_plain_text())
        for record in records:
            payload = await AiocqhttpMessageEvent._from_segment_to_dict(record)
            self.assertTrue(payload["data"]["file"].startswith("base64://"))
        with patch.object(
            voice.StarTools, "get_data_dir", return_value=self.root / "voice-state"
        ):
            voice_controller = voice.XiaodVoice(self.context, self.config)
        await voice_controller.choose_voice(event)
        self.assertEqual(self.context.llm_generate.await_count, 1)
        self.assertEqual(
            len([c for c in event.get_result().chain if isinstance(c, Record)]), 2
        )
        await voice_controller.mark_reply_sent(event)
        await voice_controller.terminate()
        self.assertEqual(self.plugin.last[PRIVATE]["status"], "prepared")
        await self.plugin.mark_cached_sent(event)
        self.assertEqual(self.plugin.last[PRIVATE]["status"], "completed")

    async def test_explicit_excerpt_overrides_full_source_default(self):
        self.make_ready(False)
        self.context.llm_generate.return_value.completion_text = '{"mode":"excerpt"}'
        result = json.loads(
            await self.plugin.request_song(self.event("唱一小段琵琶曲"), "琵琶曲")
        )
        self.assertFalse(result["full_source"])
        self.assertEqual(result["parts"], 1)

    async def test_music_discussion_does_not_trigger_audio(self):
        self.make_ready()
        self.context.llm_generate.return_value.completion_text = '{"mode":"none"}'
        event = self.event("琵琶曲的歌词是什么意思", group=True)
        result = json.loads(await self.plugin.request_song(event, "琵琶曲"))
        self.assertEqual(result["status"], "not_requested")
        self.assertIsNone(event.get_extra(music.DELIVERY))
        self.context.send_message.assert_not_awaited()

    async def test_other_sessions_and_proactive_events_are_not_allowed(self):
        for event in [self.event(group=True, target="999"), self.event(sender="999")]:
            self.assertEqual(
                json.loads(await self.plugin.request_song(event, "琵琶曲"))["status"],
                "disabled",
            )
        event = self.event()
        event.message_obj.message_id = "proactive-test"
        self.assertEqual(
            json.loads(await self.plugin.request_song(event, "琵琶曲"))["status"],
            "disabled",
        )
        self.context.llm_generate.assert_not_awaited()

    async def test_current_voice_and_reference_must_still_match_L(self):
        self.make_ready()
        self.backend.resolve_voice_id = lambda **_: "another-voice"
        self.assertEqual(
            json.loads(await self.plugin.request_song(self.event(), "琵琶曲"))[
                "status"
            ],
            "voice_unavailable",
        )
        self.backend.resolve_voice_id = lambda **_: "test-L"
        self.write_audio(self.reference, frequency=600)
        self.assertEqual(
            json.loads(await self.plugin.request_song(self.event(), "琵琶曲"))[
                "status"
            ],
            "reference_changed",
        )

    async def test_changed_source_does_not_serve_stale_music(self):
        self.make_ready()
        self.write_audio(self.source, frequency=600)
        result = json.loads(await self.plugin.request_song(self.event(), "琵琶曲"))
        self.assertEqual(result["status"], "source_changed")
        self.assertFalse(self.plugin.jobs)

    async def test_unknown_song_is_not_faked_and_respects_disabled_web_search(self):
        event = self.event("唱另一首歌")
        result = json.loads(await self.plugin.request_song(event, "另一首"))
        self.assertEqual(result["status"], "needs_source")
        self.assertEqual(result["search_status"], "disabled")
        self.assertFalse(self.plugin.jobs)
        self.context.send_message.assert_not_awaited()

    async def test_cache_cannot_escape_its_directory_or_use_long_records(self):
        directory = self.make_ready()
        path = directory / "ready.json"
        ready = json.loads(path.read_text())
        ready["parts"] = [str(self.source)]
        path.write_text(json.dumps(ready))
        self.assertIsNone(self.plugin._read_ready(directory, directory.name))
        self.make_ready()
        self.write_audio(directory / "part001.wav", seconds=46)
        self.assertIsNone(self.plugin._read_ready(directory, directory.name))

    async def test_decorator_does_not_claim_delivery_without_native_song_send(self):
        self.make_ready()
        event = self.event()
        await self.plugin.request_song(event, "琵琶曲")
        await self.plugin.mark_cached_sent(event)
        self.assertEqual(self.plugin.last[PRIVATE]["status"], "prepared")

    async def test_background_delivery_waits_for_ack_and_keeps_the_destination(self):
        owner = self

        class Process:
            returncode = None

            async def wait(self):
                owner.make_ready()
                self.returncode = 0
                return 0

        event = self.event()
        with patch.object(
            music.asyncio, "create_subprocess_exec", AsyncMock(return_value=Process())
        ):
            result = json.loads(await self.plugin.request_song(event, "琵琶曲"))
            self.assertEqual(result["status"], "queued")
            job = self.plugin.jobs[PRIVATE]
            for _ in range(8):
                await asyncio.sleep(0)
            self.context.send_message.assert_not_awaited()
            await self.plugin.deliver_cached_song(event)
            self.assertNotIn("模型额外写的正文", event.get_result().get_plain_text())
            await self.plugin.mark_cached_sent(event)
            await asyncio.wait_for(job.task, timeout=2)
        calls = self.context.send_message.await_args_list
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(call.args[0] == PRIVATE for call in calls))
        self.assertIsInstance(calls[1].args[1].chain[0], Record)
        self.assertEqual(self.plugin.last[PRIVATE]["status"], "completed")
        self.assertFalse(self.plugin.jobs)

    async def test_cancel_during_spawn_terminates_the_owned_child(self):
        entered, release = asyncio.Event(), asyncio.Event()

        class Process:
            returncode = None
            terminated = False

            def terminate(self):
                self.terminated, self.returncode = True, -15

            async def wait(self):
                return self.returncode

        process = Process()

        async def spawn(*args, **kwargs):
            entered.set()
            await release.wait()
            return process

        with patch.object(music.asyncio, "create_subprocess_exec", side_effect=spawn):
            await self.plugin.request_song(self.event(), "琵琶曲")
            await entered.wait()
            closing = asyncio.create_task(self.plugin.terminate())
            await asyncio.sleep(0)
            release.set()
            await asyncio.wait_for(closing, timeout=2)
        self.assertTrue(process.terminated)
        self.assertFalse(self.plugin.jobs)
        self.context.send_message.assert_not_awaited()

    async def test_another_member_cannot_cancel_someone_elses_song(self):
        job = music.SingingJob(GROUP, "111", "琵琶曲", True, self.root, {})
        job.task = asyncio.create_task(asyncio.Event().wait())
        self.plugin.jobs[GROUP] = job
        self.context.llm_generate.return_value.completion_text = '{"mode":"cancel"}'
        result = json.loads(
            await self.plugin.request_song(
                self.event("取消点歌", group=True, sender="222"), action="cancel"
            )
        )
        self.assertEqual(result["status"], "not_owner")
        self.assertFalse(job.task.cancelled())

    async def test_cancel_before_task_start_does_not_leave_a_stuck_job(self):
        job = music.SingingJob(PRIVATE, "100000002", "琵琶曲", True, self.root, {})
        job.task = asyncio.create_task(asyncio.Event().wait())
        self.plugin.jobs[PRIVATE] = job
        self.context.llm_generate.return_value.completion_text = '{"mode":"cancel"}'
        await self.plugin.request_song(self.event("取消点歌"), action="cancel")
        self.assertFalse(self.plugin.jobs)
        self.assertTrue(job.task.cancelled())

    async def test_two_sessions_share_one_render_and_keep_delivery_isolated(self):
        entered, release = asyncio.Event(), asyncio.Event()
        owner = self

        class Process:
            returncode = None

            async def wait(self):
                entered.set()
                await release.wait()
                owner.make_ready()
                self.returncode = 0
                return 0

        events = [self.event(), self.event(group=True)]
        with patch.object(
            music.asyncio, "create_subprocess_exec", AsyncMock(return_value=Process())
        ) as spawn:
            await self.plugin.request_song(events[0], "琵琶曲")
            await entered.wait()
            await self.plugin.request_song(events[1], "琵琶曲")
            tasks = [self.plugin.jobs[key].task for key in [PRIVATE, GROUP]]
            for event in events:
                await self.plugin.deliver_cached_song(event)
                await self.plugin.mark_cached_sent(event)
            release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)
            spawn.assert_awaited_once()
        calls = self.context.send_message.await_args_list
        self.assertEqual(sum(c.args[0] == PRIVATE for c in calls), 3)
        self.assertEqual(sum(c.args[0] == GROUP for c in calls), 3)
        self.assertFalse(self.plugin.jobs)

    async def test_send_failure_stops_remaining_audio_without_retry(self):
        owner = self

        class Process:
            returncode = None

            async def wait(self):
                owner.make_ready()
                self.returncode = 0
                return 0

        async def send(session, chain):
            if isinstance(chain.chain[0], Record):
                raise RuntimeError("Synthetic QQ send failure")
            return True

        self.context.send_message.side_effect = send
        event = self.event()
        with patch.object(
            music.asyncio, "create_subprocess_exec", AsyncMock(return_value=Process())
        ):
            await self.plugin.request_song(event, "琵琶曲")
            job = self.plugin.jobs[PRIVATE]
            await self.plugin.deliver_cached_song(event)
            await self.plugin.mark_cached_sent(event)
            await asyncio.wait_for(job.task, timeout=2)
        records = [
            c
            for c in self.context.send_message.await_args_list
            if isinstance(c.args[1].chain[0], Record)
        ]
        self.assertEqual(len(records), 1)
        self.assertEqual(self.plugin.last[PRIVATE]["status"], "failed")

    async def test_failed_intent_check_does_not_start_a_song(self):
        self.make_ready()
        self.context.llm_generate.side_effect = asyncio.TimeoutError
        result = json.loads(await self.plugin.request_song(self.event(), "琵琶曲"))
        self.assertEqual(result["status"], "intent_unconfirmed")
        self.assertFalse(self.plugin.jobs)
        self.context.send_message.assert_not_awaited()

    async def test_disabling_music_suppresses_an_already_prepared_reply(self):
        self.make_ready()
        event = self.event()
        await self.plugin.request_song(event, "琵琶曲")
        self.config["enabled"] = False
        await self.plugin.deliver_cached_song(event)
        self.assertTrue(event.is_stopped())
        self.assertFalse(event.get_result().chain)
        self.context.send_message.assert_not_awaited()

    async def test_legacy_mimo_command_does_not_consume_natural_song_requests(self):
        name = f"{MimoTTSClonePlugin.__module__}_sing_command"
        handler = star_handlers_registry.get_handler_by_full_name(name)
        command = next(f for f in handler.event_filters if isinstance(f, CommandFilter))
        event = self.event("唱歌 琵琶曲")
        event.is_at_or_wake_command = True
        self.assertFalse(command.filter(event, {}))
        manual = self.event("mimo唱歌 --音色 Chloe 测试文本")
        manual.is_at_or_wake_command = True
        self.assertTrue(command.filter(manual, {}))


if __name__ == "__main__":
    unittest.main()
