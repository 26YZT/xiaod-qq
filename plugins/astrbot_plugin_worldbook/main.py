# plugin.py

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.star import Star
from astrbot.core.star.context import Context
from astrbot.core.star.filter.permission import PermissionType

from .core.config import PluginConfig
from .core.editor import LoreEditor
from .core.entry import LoreEntry
from .core.lorebook import Lorebook
from .core.scheduler import LoreCronScheduler
from .core.session import SessionCache
from .core.share import LorebookShare
from .core.template import Template
from .core.wildcard import WildcardResolver


class WorldBookPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)

        self.cfg = PluginConfig(config)
        self.lorebook = Lorebook(self.cfg)
        self.share = LorebookShare(self.lorebook, self.cfg)
        self.sessions = SessionCache(self.cfg)
        self.style = None
        self.cron = LoreCronScheduler(self.lorebook, self.sessions)
        self.wildcards = WildcardResolver()

    # ================= 生命周期 =================

    async def initialize(self):
        """加载插件时调用"""
        await self.lorebook.initialize()
        self.cron.start()

        try:
            import pillowmd

            self.style = pillowmd.LoadMarkdownStyles(self.cfg.style_dir)
        except Exception as e:
            logger.error(f"无法加载pillowmd样式：{e}")

        self.editor = LoreEditor(self.cfg, self.lorebook, self.sessions, self.style)

    async def terminate(self):
        """插件卸载时调用"""
        self.cron.shutdown()

    # ================= 全局态命令 =================

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("查看条目")
    async def view_entry(self, event: AstrMessageEvent, arg: str | None = None):
        """查看条目（全部 / 启用 / 禁用 / 单个）"""
        async for msg in self.editor.view_entry(event, arg):
            await event.send(msg)

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("添加条目")
    async def add_entry(self, event: AstrMessageEvent, name: str):
        """添加条目 <名称> <内容>"""
        async for msg in self.editor.add_entry(event, name):
            await event.send(msg)

    @filter.llm_tool(name="worldbook_add_entry")
    async def llm_add_entry(
        self,
        event: AstrMessageEvent,
        name: str,
        content: str,
        template: str = "common",
        keywords: str = "",
        cron: str = "",
        probability: float = 1.0,
        target_id: str = "",
    ) -> str:
        """Add a worldbook entry with specified template and trigger rules.

        Templates:
        - common: Default keyword-triggered entry. Good for rules, general memory, notes.
        - resident: Always injected unconditionally into prompt. Good for core persona/rules.
        - chance: Low probability random injection. Good for casual remarks.
        - schedule: Cron-based timed injection. Good for reminders, daily/hourly events.
        - user: Injected only for specific user (set target_id to QQ/User ID).
        - group: Injected only in specific group (set target_id to Group ID).

        Args:
            name(string): Short unique entry name, <= 10 characters.
            content(string): Entry content to inject when activated.
            template(string): Template type: common, resident, chance, schedule, user, group. Defaults to common.
            keywords(string): Optional trigger keywords or regex patterns separated by commas. Defaults to name.
            cron(string): 5-segment cron expression, required when template is schedule (e.g. '0 8 * * *').
            probability(number): Trigger probability from 0.0 to 1.0, used when template is chance.
            target_id(string): Target user QQ or group ID when template is user or group.
        """
        name = str(name).strip()
        content = str(content).strip()
        template_str = str(template or "common").strip().lower()
        keywords = str(keywords or "").strip()
        cron = str(cron or "").strip()
        target_id = str(target_id or "").strip()

        if not name:
            return "Worldbook entry add failed: name is required."
        if len(name) > 10:
            return "Worldbook entry add failed: name must be no more than 10 characters."
        if not content:
            return "Worldbook entry add failed: content is required."
        if self.lorebook.get_entry(name):
            return f"Worldbook entry add failed: entry already exists: {name}"

        try:
            tmpl = Template.from_data({"template": template_str})
        except ValueError as e:
            return f"Worldbook entry add failed: {e}"

        entry_data = tmpl.defaults()
        entry_data.update({
            "template": tmpl.value,
            "name": name,
            "content": content,
        })

        if tmpl in (Template.COMMON, Template.DEFAULT):
            trigger_keywords: list[str] = []
            raw_keywords = (
                keywords.replace("\uff0c", ",")
                .replace("\n", ",")
                .replace(" ", ",")
                .split(",")
            )
            for kw in raw_keywords:
                kw = kw.strip()
                if kw and kw not in trigger_keywords:
                    trigger_keywords.append(kw)
                if len(trigger_keywords) >= 8:
                    break
            entry_data["keywords"] = trigger_keywords or [name]
        elif keywords:
            raw_keywords = [k.strip() for k in keywords.replace("\uff0c", ",").replace("\n", ",").split(",") if k.strip()]
            if raw_keywords:
                entry_data["keywords"] = raw_keywords[:8]

        if tmpl == Template.SCHEDULE and cron:
            entry_data["cron"] = cron

        if tmpl == Template.CHANCE:
            try:
                entry_data["probability"] = max(0.0, min(1.0, float(probability)))
            except (ValueError, TypeError):
                pass

        if tmpl in (Template.USER, Template.GROUP) and target_id:
            entry_data["scope"] = [target_id]

        try:
            names = self.lorebook.add_entries([entry_data])
            if not names:
                return f"Worldbook entry add failed: entry already exists: {name}"
            return f"Worldbook entry added: {', '.join(names)} (template={tmpl.value})"
        except Exception as e:
            logger.error(f"worldbook_add_entry failed: {e}")
            return f"Worldbook entry add failed: {e}"

    @filter.llm_tool(name="worldbook_update_entry")
    async def llm_update_entry(
        self,
        event: AstrMessageEvent,
        name: str,
        content: str = "",
        enabled: bool | None = None,
        keywords: str = "",
        priority: int | None = None,
        scope: str = "",
        cron: str = "",
        duration: int | None = None,
        times: int | None = None,
        probability: float | None = None,
    ) -> str:
        """Update fields of an existing worldbook entry. Only provided fields are changed.

        Args:
            name(string): Name of the entry to update. Required.
            content(string): New content to inject. Leave empty to keep current.
            enabled(boolean): Enable or disable the entry.
            keywords(string): New trigger keywords/regex, comma-separated. Leave empty to keep current.
            priority(number): New priority value.
            scope(string): New scope, comma-separated list of user/group IDs or 'admin'. Leave empty to keep current.
            cron(string): New cron expression (5 segments). Leave empty to keep current.
            duration(number): New duration in seconds. 0 means permanent.
            times(number): New max inject times. 0 means unlimited.
            probability(number): New trigger probability 0.0~1.0.
        """
        name = str(name).strip()
        if not name:
            return "Worldbook entry update failed: name is required."
        if not self.lorebook.get_entry(name):
            return f"Worldbook entry update failed: entry not found: {name}"

        fields: dict = {}

        if content:
            fields["content"] = str(content).strip()
        if enabled is not None:
            fields["enabled"] = bool(enabled)
        if keywords:
            raw = [k.strip() for k in keywords.replace("\uff0c", ",").replace("\n", ",").split(",") if k.strip()]
            if raw:
                fields["keywords"] = raw[:8]
        if priority is not None:
            fields["priority"] = int(priority)
        if scope:
            raw_scope = [s.strip() for s in scope.replace("\uff0c", ",").split(",") if s.strip()]
            if raw_scope:
                fields["scope"] = raw_scope
        if cron:
            fields["cron"] = str(cron).strip()
        if duration is not None:
            fields["duration"] = int(duration)
        if times is not None:
            fields["times"] = int(times)
        if probability is not None:
            fields["probability"] = max(0.0, min(1.0, float(probability)))

        if not fields:
            return "Worldbook entry update failed: no fields to update."

        try:
            changed = self.lorebook.update_entry(name, fields)
            if changed:
                return f"Worldbook entry updated: {name} (fields: {', '.join(fields.keys())})"
            return f"Worldbook entry update failed: no changes applied to {name}."
        except Exception as e:
            logger.error(f"worldbook_update_entry failed: {e}")
            return f"Worldbook entry update failed: {e}"

    @filter.llm_tool(name="worldbook_query_entry")
    async def llm_query_entry(
        self,
        event: AstrMessageEvent,
        name: str = "",
        keyword: str = "",
    ) -> str:
        """Query worldbook entries. If name is provided, returns entry details. Otherwise lists matching or all entries.

        Args:
            name(string): Exact entry name to inspect. Optional.
            keyword(string): Search keyword in entry name, content, or trigger keywords. Optional.
        """
        name = str(name).strip()
        keyword = str(keyword).strip().lower()

        if name:
            entry = self.lorebook.get_entry(name)
            if not entry:
                return f"Worldbook entry not found: {name}"
            d = entry.to_dict()
            info = [
                f"Name: {d.get('name')}",
                f"Template: {d.get('template')}",
                f"Enabled: {d.get('enabled')}",
                f"Priority: {d.get('priority')}",
                f"Keywords: {', '.join(d.get('keywords', [])) or 'None'}",
                f"Scope: {', '.join(d.get('scope', [])) or 'All'}",
                f"Cron: {d.get('cron') or 'None'}",
                f"Duration: {d.get('duration')}s",
                f"Times: {d.get('times')}",
                f"Probability: {d.get('probability')}",
                f"Content: {d.get('content')}",
            ]
            return "\n".join(info)

        entries = self.lorebook.list_entries()
        if not entries:
            return "Worldbook has no entries."

        if keyword:
            filtered = []
            for e in entries:
                d = e.to_dict()
                in_name = keyword in d.get("name", "").lower()
                in_content = keyword in d.get("content", "").lower()
                in_kw = any(keyword in str(k).lower() for k in d.get("keywords", []))
                if in_name or in_content or in_kw:
                    filtered.append(e)
            entries = filtered
            if not entries:
                return f"No worldbook entries found matching keyword: {keyword}"

        summary = []
        for e in entries:
            d = e.to_dict()
            status = "enabled" if d.get("enabled") else "disabled"
            summary.append(f"- {d.get('name')} (template={d.get('template')}, {status}, priority={d.get('priority')})")
        return f"Found {len(entries)} entries:\n" + "\n".join(summary)

    @filter.llm_tool(name="worldbook_delete_entry")
    async def llm_delete_entry(
        self,
        event: AstrMessageEvent,
        names: str,
    ) -> str:
        """Delete one or more worldbook entries by name.

        Args:
            names(string): Entry name(s) to delete, comma-separated.
        """
        names = str(names).strip()
        if not names:
            return "Worldbook entry delete failed: names is required."

        name_list = [n.strip() for n in names.replace("\uff0c", ",").split(",") if n.strip()]
        if not name_list:
            return "Worldbook entry delete failed: no valid names provided."

        try:
            success, failed = self.lorebook.remove_entries(name_list)
            parts = []
            if success:
                parts.append(f"Deleted: {', '.join(success)}")
            if failed:
                parts.append(f"Not found: {', '.join(failed)}")
            return "; ".join(parts) if parts else "No entries deleted."
        except Exception as e:
            logger.error(f"worldbook_delete_entry failed: {e}")
            return f"Worldbook entry delete failed: {e}"

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("删除条目")
    async def delete_entry(self, event: AstrMessageEvent):
        """删除条目 <名称1> <名称2>"""
        async for msg in self.editor.delete_entry(event):
            await event.send(msg)

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("设置触发词")
    async def set_keywords(self, event: AstrMessageEvent):
        """设置触发词 <关键词|正则表达式>"""
        async for msg in self.editor.set_keywords(event):
            await event.send(msg)

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("设置优先级")
    async def set_priority(self, event: AstrMessageEvent):
        """设置优先级 <数字>"""
        async for msg in self.editor.set_priority(event):
            await event.send(msg)

    # ================= 会话态命令 =================

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("启用条目")
    async def enable_entry(self, event: AstrMessageEvent):
        """启用条目 <名称1> <名称2>"""
        async for msg in self.editor.enable_entry(event):
            await event.send(msg)

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("禁用条目")
    async def disable_entry(self, event: AstrMessageEvent):
        """禁用条目 <名称1> <名称2>"""
        async for msg in self.editor.disable_entry(event):
            await event.send(msg)

    @filter.command("条目状态")
    async def entries_state(self, event: AstrMessageEvent):
        """查看当前会话的条目状态"""
        async for msg in self.editor.entries_state(event):
            await event.send(msg)

    @filter.command("清除条目", alias={"清空条目"})
    async def clear_entries(self, event: AstrMessageEvent):
        """清除当前会话的某个条目，默认清除全部"""
        async for msg in self.editor.clear_entries(event):
            await event.send(msg)

    # ================= 文件流通 =================

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("导出世界书")
    async def upload_lorebook(self, event: AstrMessageEvent, name: str | None = None):
        async for msg in self.share.upload_lorebook(event, name):
            yield msg

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("导入世界书")
    async def import_lorebook(self, event: AstrMessageEvent):
        async for msg in self.share.download_lorebook(event):
            yield msg

    # ================= 核心机制 =================

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """
        LLM 请求主入口

        执行顺序：
        1. 判决并挂载符合条件的条目
        2. 使用当前会话中的条目构造 system_prompt
        """
        msg = event.message_str
        if not msg:
            return

        umo = event.unified_msg_origin

        # Step 1：判决 + 挂载
        self._decide_entries(event, msg, umo)

        # Step 2：使用会话中的条目
        self._consume_entries(event, req, umo)

    def _decide_entries(self, event, msg: str, umo: str) -> None:
        """
        判决与挂载阶段

        职责：
        - 遍历所有可用的 LoreEntry
        - 通过 LoreEntry.can_activate 做统一判决
        - 将通过判决的条目写入 Session
        """

        gid = event.get_group_id()
        uid = event.get_sender_id()
        is_admin = event.is_admin()

        candidates: list[LoreEntry] = []

        for e in self.lorebook.entries:
            # 所有是否“允许进入会话”的判断
            # 必须统一由 LoreEntry.can_activate 给出
            if e.check_activate(
                text=msg,
                user_id=uid,
                group_id=gid,
                session_id=umo,
                is_admin=is_admin,
            ):
                candidates.append(e)

        if not candidates:
            return

        # 将通过判决的条目写入 Session
        self.sessions.attach(umo, candidates)

    def _consume_entries(
        self, event: AstrMessageEvent, req: ProviderRequest, umo: str
    ) -> None:
        """
        使用阶段

        职责：
        - 读取当前会话中已有的条目
        - 进行使用阶段 scope / enabled / active 判定
        - 按 priority 排序并裁剪
        - 将内容注入 system_prompt
        - 记录一次使用消耗
        """

        uid = event.get_sender_id()
        gid = event.get_group_id()
        is_admin = event.is_admin()

        # Step 0：取出会话中仍然处于 active 状态的条目（已按 priority 排序）
        session_entries = self.sessions.get_sorted_active(umo)
        if not session_entries:
            return

        # Step 1：使用阶段 scope gate
        scoped_entries: list[LoreEntry] = []
        for e in session_entries:
            if e.allow_consume(
                user_id=uid,
                group_id=gid,
                session_id=umo,
                is_admin=is_admin,
            ):
                scoped_entries.append(e)
            else:
                logger.debug(f"[条目:{e.name}] 使用阶段 scope 不满足，已跳过")

        if not scoped_entries:
            return

        # Step 2：注入数量限制（仅影响本次请求）
        max_count = self.cfg.max_inject_count
        inject_entries = scoped_entries

        if max_count > 0 and len(inject_entries) > max_count:
            dropped = inject_entries[max_count:]
            logger.debug(
                f"超出最大允许注入数 {max_count}，"
                f"已忽略 [{', '.join(e.name for e in dropped)}]"
            )
            inject_entries = inject_entries[:max_count]

        if not inject_entries:
            return

        logger.debug(f"当前会话实际注入条目：{[e.name for e in inject_entries]}")

        # Step 3：构造 system_prompt 注入内容
        sections: list[str] = []

        for entry in inject_entries:
            title = f"## [{entry.name}]"
            rendered = self.wildcards.render(entry, event)
            sections.append(f"{title}\n{rendered}")

            # 一次注入视为一次使用
            entry.on_consume()

        req.system_prompt += "\n\n" + "\n\n".join(sections) + "\n\n"
