"""Seed a fresh AstrBot through its native database/configuration services."""

import asyncio
import copy
import json
import secrets
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from common import ASTRO, LOCK, ROOT, RUNTIME, SINGING, write_json


def expand(value, variables):
    """Expand reviewed template fields without executing configuration text."""
    if isinstance(value, dict):
        return {key: expand(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [expand(item, variables) for item in value]
    if isinstance(value, str):
        if value in variables:
            return variables[value]
        for key, item in variables.items():
            if isinstance(item, str):
                value = value.replace(key, item)
        return value
    return value


def build_configs(defaults, settings, ws_token):
    """Build separate private/group profiles with a disabled unbound fallback."""
    variables = {
        "${BOT_QQ}": settings["bot_qq"],
        "${OWNER_QQ}": settings["owner_qq"],
        "${GROUP_QQ}": settings["group_qqs"][0] if settings["group_qqs"] else "",
        "${GROUP_SESSIONS}": [
            f"qq-personal:GroupMessage:{number}" for number in settings["group_qqs"]
        ],
        "${PRIVATE_PROACTIVE}": bool(settings["private_proactive"]),
        "${DEEPSEEK_API_KEY}": settings["deepseek_api_key"],
        "${MIMO_API_KEY}": settings["mimo_api_key"],
        "${RUNTIME}": str(RUNTIME),
    }
    common = copy.deepcopy(defaults)
    common["admins_id"] = [settings["owner_qq"]]
    common["platform"] = [
        {
            "id": "qq-personal",
            "type": "aiocqhttp",
            "enable": True,
            "ws_reverse_host": "127.0.0.1" if sys.platform == "darwin" else "0.0.0.0",
            "ws_reverse_port": settings["onebot_port"],
            "ws_reverse_token": ws_token,
        }
    ]
    source = expand(
        json.loads((ROOT / "presets/deepseek-source.json").read_text()), variables
    )
    model = json.loads((ROOT / "presets/deepseek-model.json").read_text())
    source["enable"] = model["enable"] = bool(settings["deepseek_api_key"])
    common["provider_sources"] = [source]
    common["provider"] = [model]
    common["dashboard"].update(
        host="127.0.0.1", port=settings["dashboard_port"], enable=True
    )
    common["provider_settings"].update(
        enable=bool(settings["deepseek_api_key"]),
        web_search=bool(settings["deepseek_api_key"]),
        websearch_provider="deepseek",
        websearch_deepseek_key=[settings["deepseek_api_key"]]
        if settings["deepseek_api_key"]
        else [],
        web_search_link=True,
        display_reasoning_text=False,
        identifier=False,
        group_name_display=False,
        prompt_prefix="{{prompt}}",
        computer_use_runtime="none",
        show_tool_use_status=False,
        show_tool_call_result=False,
    )
    common["provider_ltm_settings"]["group_icl_enable"] = False
    common["provider_ltm_settings"]["active_reply"]["enable"] = False
    profiles = {}
    for label in ("private", "group"):
        profile = copy.deepcopy(common)
        profile.update(
            expand(
                json.loads((ROOT / "presets" / f"{label}-overrides.json").read_text()),
                variables,
            )
        )
        profiles[label] = profile
    fallback = copy.deepcopy(common)
    fallback["provider_settings"]["enable"] = False
    fallback["plugin_set"] = []
    fallback["agent_runner"] = copy.deepcopy(profiles["group"]["agent_runner"])
    return fallback, profiles, variables


async def configure(settings_path):
    """Write account-scoped files without importing the maintainer's state."""
    if Path.cwd().resolve() != ASTRO.resolve():
        raise RuntimeError("Run this helper from this kit's runtime/astrbot directory.")
    sys.path.insert(0, str(ASTRO))
    from astrbot.core.astrbot_config_mgr import AstrBotConfigManager
    from astrbot.core.config.astrbot_config import AstrBotConfig
    from astrbot.core.config.default import DEFAULT_CONFIG
    from astrbot.core.db.sqlite import SQLiteDatabase
    from astrbot.core.umop_config_router import UmopConfigRouter
    from astrbot.core.utils.shared_preferences import SharedPreferences

    settings = json.loads(settings_path.read_text())
    RUNTIME.chmod(0o700)
    state_path = RUNTIME / "managed-state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    state.setdefault("ws_token", secrets.token_urlsafe(32))
    state.setdefault("napcat_token", secrets.token_urlsafe(24))
    fallback, profiles, variables = build_configs(
        DEFAULT_CONFIG, settings, state["ws_token"]
    )
    data = ASTRO / "data"
    data.mkdir(exist_ok=True)
    data.chmod(0o700)
    db = SQLiteDatabase(str(data / "data_v4.db"))
    await db.initialize()
    preferences = SharedPreferences(db)
    try:
        await preferences.initialize()
        router = UmopConfigRouter(preferences)
        await router.initialize()
        config = AstrBotConfig(
            config_path=str(data / "cmd_config.json"), default_config=fallback
        )
        if state.get("initialized"):
            current = copy.deepcopy(dict(config))
            for key in ("admins_id", "platform", "provider", "provider_sources"):
                current[key] = fallback[key]
            current["dashboard"]["port"] = settings["dashboard_port"]
            config.clear()
            config.update(current)
        else:
            config.clear()
            config.update(fallback)
        config.save_config()
        manager = AstrBotConfigManager(config, router, preferences)
        await manager.initialize()
        ids = state.setdefault("profile_ids", {})
        for label, profile in profiles.items():
            if ids.get(label) in manager.confs:
                existing = manager.confs[ids[label]]
                for key in ("admins_id", "platform", "provider", "provider_sources"):
                    existing[key] = profile[key]
                existing["provider_settings"].update(
                    {
                        key: profile["provider_settings"][key]
                        for key in (
                            "enable",
                            "web_search",
                            "websearch_provider",
                            "websearch_deepseek_key",
                        )
                    }
                )
                existing.save_config()
            else:
                ids[label] = await manager.create_conf(
                    profile, name="小D私聊" if label == "private" else "小D群聊"
                )
        routes = {
            key: value
            for key, value in router.umop_to_conf_id.items()
            if value not in ids.values()
        }
        routes.update(
            {
                f"qq-personal:GroupMessage:{number}": ids["group"]
                for number in settings["group_qqs"]
            }
        )
        routes["qq-personal:FriendMessage:*"] = ids["private"]
        routes["webchat:*:*"] = ids["private"]
        await router.update_routing_data(routes)
        persona = json.loads((ROOT / "presets/persona.json").read_text())
        if not await db.get_persona_by_id(persona["persona_id"]):
            await db.insert_persona(**persona)
        for origin in variables["${GROUP_SESSIONS}"]:
            if not await preferences.session_get(origin, "sel_conv_id", None):
                conversation = await db.create_conversation(
                    user_id=origin,
                    platform_id="qq-personal",
                    content=[],
                    title="小D群聊",
                    persona_id=persona["persona_id"],
                )
                await preferences.session_put(
                    origin, "sel_conv_id", conversation.conversation_id
                )
        for source_path in (ROOT / "presets/plugins").glob("*.json"):
            target = data / "config" / f"{source_path.stem}_config.json"
            desired = expand(json.loads(source_path.read_text()), variables)
            if source_path.stem == "astrbot_plugin_xiaod_music":
                desired["enabled"] = (SINGING / "ready.json").exists()
            if target.exists():
                existing = json.loads(target.read_text("utf-8-sig"))
                for key in (
                    "account_id",
                    "owner_id",
                    "platform_id",
                    "group_sessions",
                    "private_sessions",
                    "master_qq",
                    "api_key",
                ):
                    if key in desired:
                        existing[key] = desired[key]
                if source_path.stem == "astrbot_plugin_proactive_chat":
                    existing["friend_settings"]["session_list"] = desired[
                        "friend_settings"
                    ]["session_list"]
                    existing["friend_settings"]["enable"] = desired["friend_settings"][
                        "enable"
                    ]
                if source_path.stem == "astrbot_plugin_xiaod_music":
                    ready = (SINGING / "ready.json").exists()
                    if not ready or not state.get("singing_configured", False):
                        existing["enabled"] = ready
                desired = existing
            write_json(target, desired, private=True)
        voice_data = data / "plugin_data/astrbot_plugin_mimo_tts_clone"
        refs = voice_data / "voice_refs"
        refs.mkdir(parents=True, exist_ok=True)
        library = json.loads((ROOT / "presets/voices.json").read_text())
        for record in library["voices"]:
            source_path = ROOT / record["audio_path"]
            target = refs / source_path.name
            if not target.exists():
                shutil.copy2(source_path, target)
            record["audio_path"] = str(target)
            record.pop("sha256", None)
        if not (voice_data / "voices.json").exists():
            write_json(voice_data / "voices.json", library, private=True)
        mimo_config = json.loads(
            (data / "config/astrbot_plugin_mimo_tts_clone_config.json").read_text()
        )
        write_json(voice_data / "config.json", mimo_config, private=True)
        music = data / "plugin_data/astrbot_plugin_xiaod_music"
        music.mkdir(parents=True, exist_ok=True)
        catalog = json.loads((ROOT / "presets/song-catalog.json").read_text())
        catalog["reference"] = str(refs / "L.wav")
        for song in catalog["songs"]:
            song["source"] = str(
                RUNTIME
                / "datasets/da-fei-yu-girl-voice-zh/singing/train"
                / song["source"]
            )
        write_json(music / "catalog.json", catalog)
        meme = data / "plugin_data/meme_manager"
        pack = meme / "packs/xiaod-welcome"
        if not (pack / "manifest.json").exists():
            (pack / "memes/happy").mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / "assets/welcome.png", pack / "memes/happy/welcome.png")
            manifest = {
                "schema_version": 1,
                "id": "xiaod-welcome",
                "name": "小D欢迎表情",
                "version": "1.0.0",
                "categories": {
                    "happy": {
                        "description": "欢迎新朋友、积极回应、轻松庆祝；仅在贴切时使用"
                    }
                },
            }
            write_json(pack / "manifest.json", manifest)
            write_json(
                pack / "memes_data.json",
                {"happy": manifest["categories"]["happy"]["description"]},
            )
            registry_path = meme / "registry.json"
            registry = (
                json.loads(registry_path.read_text())
                if registry_path.exists()
                else {"schema_version": 1, "installed_packs": []}
            )
            if not any(
                item["id"] == "xiaod-welcome" for item in registry["installed_packs"]
            ):
                registry["installed_packs"].append(
                    {
                        "id": "xiaod-welcome",
                        "name": manifest["name"],
                        "version": "1.0.0",
                        "enabled": True,
                        "installed_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            write_json(registry_path, registry)
        config_dir = RUNTIME / "napcat/config"
        config_dir.mkdir(parents=True, exist_ok=True)
        for child in ("qq", "plugins"):
            (RUNTIME / "napcat" / child).mkdir(parents=True, exist_ok=True)
        write_json(
            config_dir / "webui.json",
            {
                "host": "0.0.0.0",
                "port": 6099,
                "token": state["napcat_token"],
                "loginRate": 10,
            },
            private=True,
        )
        onebot = {
            "network": {
                "httpServers": [],
                "httpSseServers": [],
                "httpClients": [],
                "websocketServers": [],
                "websocketClients": [
                    {
                        "name": "xiaod-local",
                        "enable": True,
                        "url": f"ws://host.docker.internal:{settings['onebot_port']}/ws",
                        "messagePostFormat": "array",
                        "reportSelfMessage": False,
                        "reconnectInterval": 5000,
                        "token": state["ws_token"],
                        "debug": False,
                        "heartInterval": 30000,
                        "verifyCertificate": True,
                    }
                ],
                "plugins": [],
            },
            "musicSignUrl": "",
            "enableLocalFile2Url": False,
            "parseMultMsg": False,
            "imageDownloadProxy": "",
        }
        write_json(
            config_dir / f"onebot11_{settings['bot_qq']}.json", onebot, private=True
        )
        write_json(config_dir / "onebot11.json", onebot, private=True)
        extra = (
            '    extra_hosts:\n      - "host.docker.internal:host-gateway"\n'
            if sys.platform != "darwin"
            else ""
        )
        compose = f"""services:
  napcat:
    image: {LOCK["napcat"]["image"]}
    restart: unless-stopped
    environment:
      TZ: Asia/Shanghai
    ports:
      - "127.0.0.1:{settings["napcat_port"]}:6099"
    volumes:
      - ./napcat/qq:/app/.config/QQ
      - ./napcat/config:/app/napcat/config
      - ./napcat/plugins:/app/napcat/plugins
{extra}    logging:
      driver: json-file
      options:
        max-size: 10m
        max-file: "3"
"""
        (RUNTIME / "compose.yaml").write_text(compose)
        state["initialized"] = True
        state["singing_configured"] = (SINGING / "ready.json").exists()
        write_json(state_path, state, private=True)
        print(
            "Fresh persona, scoped profiles, voices, lore and starter meme are configured."
        )
    finally:
        await preferences.close()
        await db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(configure(Path(sys.argv[1]).resolve()))
