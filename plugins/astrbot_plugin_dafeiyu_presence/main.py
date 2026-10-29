"""Scheduled, account-scoped QQ moments and signatures."""

import asyncio
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


class DafeiyuPresence(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.timezone = ZoneInfo("Asia/Shanghai")
        self.lock = asyncio.Lock()
        self.stopping = False
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_dafeiyu_presence")
        self.state_path = self.data_dir / "state.json"
        self.state = {"attempts": [], "drafts": {}}
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(self.state, dict) or not isinstance(
                self.state.get("attempts"), list
            ):
                raise ValueError(
                    "Invalid presence state; restore it before enabling writes."
                )
        self.scheduler = AsyncIOScheduler(timezone=self.timezone)

    async def initialize(self):
        """Register future cron slots without publishing during startup."""
        if not self.config.get("enabled", False):
            return
        for kind, time_key in [
            ("qzone", "qzone_time"),
            ("signature", "signature_time"),
        ]:
            if not self.config.get(f"{kind}_enabled", False):
                continue
            if kind == "signature" and not self.config.get(
                "signature_sync_off_confirmed", False
            ):
                continue
            hour, minute = [int(value) for value in self.config[time_key].split(":")]
            kwargs = (
                {"day_of_week": self.config.get("qzone_weekdays", "tue,thu,sat")}
                if kind == "qzone"
                else {}
            )
            self.scheduler.add_job(
                self.run,
                "cron",
                hour=hour,
                minute=minute,
                kwargs={"kind": kind},
                id=kind,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=120,
                **kwargs,
            )
        self.scheduler.start()
        for job in self.scheduler.get_jobs():
            logger.info(
                "[DafeiyuPresence] Scheduled %s at %s",
                job.id,
                job.next_run_time.isoformat(),
            )

    async def terminate(self):
        """Stop scheduled work when the plugin is unloaded."""
        self.stopping = True
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def save_state(self):
        """Atomically persist reservations and outcomes before another run."""
        temporary = self.state_path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            temporary.chmod(0o600)
            handle.write(json.dumps(self.state, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.state_path)

    async def draft(self, kind: str) -> str:
        """Generate public text from the persona and allowlisted character lore.

        Args:
            kind: Either qzone or signature.

        Returns:
            Validated plain text; never includes a chat transcript.
        """
        if kind not in ("qzone", "signature"):
            raise ValueError("Unknown presence task")
        persona = self.context.persona_manager.get_persona_v3_by_id(
            self.config["persona_id"]
        )
        if not persona or not persona.get("prompt"):
            raise ValueError("Configured persona is unavailable")
        lore_path = (
            Path(get_astrbot_data_path())
            / "config/astrbot_plugin_worldbook_config.json"
        )
        lore = []
        if lore_path.exists():
            entries = json.loads(lore_path.read_text(encoding="utf-8-sig")).get(
                "entry_storage", []
            )
            lore = [
                entry["content"]
                for entry in entries
                if entry.get("enabled")
                and entry.get("name") in self.config.get("public_lore_names", [])
                and not entry.get("scope")
            ]
        day = datetime.now(self.timezone).date().isoformat()
        if lore:
            index = int(hashlib.sha256(f"{day}:{kind}".encode()).hexdigest(), 16) % len(
                lore
            )
            lore = [lore[index]]
        recent = [
            entry["text"]
            for entry in self.state["attempts"][-6:]
            if entry.get("kind") == kind
        ]
        limit = 100 if kind == "qzone" else 20
        purpose = (
            "一条QQ空间说说，30～100字，1～3句"
            if kind == "qzone"
            else "QQ资料页个性签名，8～20字，1句"
        )
        prompt = (
            f"为当前角色写{purpose}。这是角色自己的公开表达，不是在回复某位聊天对象。"
            "按角色口吻写小念头、反差或趣味，不机械重复白饭、尾巴或固定口头禅。"
            "不编造刚出门、吃过什么、见过谁等真实活动；没提供天气、地点等信息就不声称知道。"
            "只写想法、偏好、愿望、比喻或一般性的角色吐槽；角色小设定可用设想表达，不叙述已完成的现实生活事件。"
            "不提私聊、群聊、他人姓名、QQ号、秘密、主人身份或任何内部配置。"
            "不@任何人，不索要礼物，不含链接、工具标记或括号旁白。不要照抄近期文案。"
            '只返回JSON对象 {"text":"准备发布的正文"}，不要代码框或分析。\n'
            + json.dumps(
                {"公开角色补充": lore, "近期该类文案": recent}, ensure_ascii=False
            )
        )
        umo = f"{self.config['platform_id']}:FriendMessage:{self.config['owner_id']}"
        provider = await self.context.get_current_chat_provider_id(umo)
        response = await asyncio.wait_for(
            self.context.llm_generate(
                chat_provider_id=provider,
                prompt=prompt,
                system_prompt=persona["prompt"],
                contexts=[],
                temperature=0.85,
                max_tokens=300,
            ),
            timeout=45,
        )
        raw = response.completion_text.strip()
        if raw.startswith("```") and raw.endswith("```"):
            raw = "\n".join(raw.splitlines()[1:-1])
        text = json.loads(raw).get("text", "").strip()
        if (
            not text
            or len(text) > limit
            or any(
                mark in text
                for mark in [
                    "&&",
                    "```",
                    "http://",
                    "https://",
                    "@",
                    "<think",
                    "[SKIP]",
                ]
            )
            or re.search(r"\d{5,}", text)
        ):
            raise ValueError("Generated public text failed validation")
        if kind == "signature" and "\n" in text:
            raise ValueError("Signature must be a single line")
        return text

    async def run(self, kind: str):
        """Execute one future slot with persistent quota and identity checks.

        Args:
            kind: Either qzone or signature.
        """
        if (
            self.stopping
            or not self.config.get("enabled")
            or not self.config.get(f"{kind}_enabled")
        ):
            return
        if kind == "signature" and not self.config.get("signature_sync_off_confirmed"):
            return
        async with self.lock:
            now = datetime.now(self.timezone)
            date = now.date().isoformat()
            week = f"{now.isocalendar().year}-W{now.isocalendar().week:02}"
            attempts = self.state["attempts"]
            if any(
                entry.get("kind") == kind and entry.get("date") == date
                for entry in attempts
            ):
                return
            if (
                kind == "qzone"
                and sum(
                    entry.get("kind") == kind and entry.get("week") == week
                    for entry in attempts
                )
                >= 3
            ):
                return
            reservation = None
            try:
                platform = next(
                    (
                        p
                        for p in self.context.platform_manager.get_insts()
                        if p.meta().id == self.config["platform_id"]
                    ),
                    None,
                )
                if platform is None:
                    raise RuntimeError("Configured QQ adapter is unavailable")
                bot = platform.get_client()
                identity = await bot.call_action("get_login_info")
                if str(identity.get("user_id")) != str(self.config["account_id"]):
                    raise RuntimeError(
                        "Logged-in account does not match the approved account"
                    )
                text = await self.draft(kind)
                self.state.setdefault("drafts", {})[kind] = {
                    "text": text,
                    "at": now.isoformat(),
                }
                self.save_state()
                if self.stopping or self.config.get("dry_run", True):
                    return
                if any(
                    entry.get("kind") == kind and entry.get("text") == text
                    for entry in attempts[-15:]
                ):
                    return
                if kind == "signature" and "original_signature" not in self.state:
                    profile = await bot.call_action(
                        "get_stranger_info",
                        user_id=int(self.config["account_id"]),
                        no_cache=True,
                    )
                    if not isinstance(profile.get("long_nick"), str):
                        raise RuntimeError("Cannot back up the original signature")
                    self.state["original_signature"] = profile["long_nick"]
                    self.save_state()
                reservation = {
                    "kind": kind,
                    "date": date,
                    "week": week,
                    "text": text,
                    "at": now.isoformat(),
                    "status": "pending",
                }
                attempts.append(reservation)
                self.state["attempts"] = attempts[-100:]
                self.save_state()
                # Reserve before writing. Unknown outcomes are never retried automatically.
                if kind == "qzone":
                    result = await bot.call_action(
                        "send_qzone_msg", content=text, images=[], ugc_right=4
                    )
                    if not isinstance(result, dict) or not result.get("tid"):
                        raise RuntimeError("No Qzone post ID was returned")
                    reservation["tid"] = str(result["tid"])
                else:
                    await bot.call_action("set_self_longnick", longNick=text)
                    for delay in (0, 1, 2):
                        if delay:
                            await asyncio.sleep(delay)
                        profile = await bot.call_action(
                            "get_stranger_info",
                            user_id=int(self.config["account_id"]),
                            no_cache=True,
                        )
                        if profile.get("long_nick") == text:
                            break
                    else:
                        raise RuntimeError("Signature write has not been verified")
                reservation["status"] = "success"
                logger.info(
                    "[DafeiyuPresence] Completed %s for the approved account", kind
                )
            except Exception as exc:
                if reservation is not None:
                    reservation["status"] = "unknown"
                self.state["last_error"] = {
                    "kind": kind,
                    "at": now.isoformat(),
                    "type": type(exc).__name__,
                }
                logger.warning(
                    "[DafeiyuPresence] %s stopped: %s", kind, type(exc).__name__
                )
            finally:
                self.save_state()

    @filter.command("小D日常状态")
    async def status(self, event: AstrMessageEvent):
        """Show schedule and write outcomes to the configured owner.

        Args:
            event: Incoming command event.
        """
        if (
            not event.is_private_chat()
            or event.get_sender_id() != self.config["owner_id"]
        ):
            event.stop_event()
            return
        labels = {"qzone": "空间动态", "signature": "个性签名"}
        outcomes = {
            "success": "已确认成功",
            "pending": "执行中或待核对",
            "unknown": "结果未确认，不自动重发",
        }
        jobs = [
            f"{labels[job.id]}：{job.next_run_time:%m-%d %H:%M}"
            for job in self.scheduler.get_jobs()
        ]
        latest = []
        for kind in ("qzone", "signature"):
            last = next(
                (
                    entry
                    for entry in reversed(self.state["attempts"])
                    if entry.get("kind") == kind
                ),
                None,
            )
            if last:
                latest.append(
                    f"{labels[kind]}上次：{last['date']} / {outcomes.get(last['status'], last['status'])}"
                )
        yield event.plain_result(
            "日常任务：\n"
            + ("\n".join(jobs) or "尚未启用")
            + ("\n" + "\n".join(latest) if latest else "\n还没有实际更新记录。")
        )

    @filter.command("小D日常预览")
    async def preview(self, event: AstrMessageEvent):
        """Generate drafts privately without publishing.

        Args:
            event: Incoming command event.
        """
        if (
            not event.is_private_chat()
            or event.get_sender_id() != self.config["owner_id"]
        ):
            event.stop_event()
            return
        try:
            moment = await self.draft("qzone")
            signature = await self.draft("signature")
            yield event.plain_result(
                f"说说草稿：{moment}\n\n签名草稿：{signature}\n\n仅预览，没有发布或修改资料。"
            )
        except Exception as exc:
            logger.warning("[DafeiyuPresence] Preview failed: %s", type(exc).__name__)
            yield event.plain_result("这次草稿没生成好，稍后再试。")
