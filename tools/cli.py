"""Install, configure, and run the public 小D QQ companion kit."""

import argparse
import getpass
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import zipfile

from common import (
    ASTRO,
    LOCK,
    ROOT,
    RUNTIME,
    SETTINGS,
    SINGING,
    active_pid,
    checkout,
    compose_command,
    download,
    python_in,
    run,
    write_json,
)


def install(with_singing=False, skip_dashboard=False):
    """Install fixed sources and isolated dependencies without touching another bot."""
    if active_pid():
        raise RuntimeError(
            "Stop this checkout with './xiaod stop' before reinstalling."
        )
    for command in ("git", "curl", "uv"):
        if not shutil.which(command):
            raise RuntimeError(f"Missing {command}. See docs/INSTALL.md.")
    print("[1/4] Installing the fixed AstrBot source and dependency lock...")
    checkout(ASTRO, LOCK["astrbot"])
    environment = ASTRO / ".venv"
    if not python_in(environment).exists():
        run(["uv", "venv", "--python", "3.12", environment])
    run(
        [
            "uv",
            "pip",
            "sync",
            "--python",
            python_in(environment),
            ROOT / "locks/runtime.txt",
        ]
    )
    print("[2/4] Installing the versioned plugin bundle...")
    destination = ASTRO / "data/plugins"
    destination.mkdir(parents=True, exist_ok=True)
    for source in (ROOT / "plugins").iterdir():
        if not source.is_dir():
            continue
        target = destination / source.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(
            source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
    print("[3/4] Installing the pinned management dashboard...")
    if not skip_dashboard:
        archive = RUNTIME / "downloads/dashboard.zip"
        download(LOCK["dashboard"]["url"], archive, LOCK["dashboard"]["sha256"])
        dist = ASTRO / "data/dist"
        dist.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as bundle:
            for item in bundle.infolist():
                if not (dist / item.filename).resolve().is_relative_to(dist.resolve()):
                    raise RuntimeError("The dashboard archive contains an unsafe path.")
            bundle.extractall(dist)
        if not (dist / "index.html").exists() and (dist / "dist/index.html").exists():
            for item in (dist / "dist").iterdir():
                shutil.move(str(item), dist / item.name)
            (dist / "dist").rmdir()
        if not (dist / "index.html").exists():
            raise RuntimeError("The dashboard archive did not contain index.html.")
    print(
        "[4/4] Installing the fixed singing workflow..."
        if with_singing
        else "[4/4] Chat and MiMo voice are ready; singing can be installed later."
    )
    SINGING.mkdir(parents=True, exist_ok=True)
    for name in ("render_singing.py", "prepare_online_song.py"):
        shutil.copy2(ROOT / "singing" / name, SINGING / name)
    shutil.copy2(
        ROOT / "presets/singing.json", SINGING / "selected_singing_preset.json"
    )
    if with_singing:
        from setup_singing import install_singing

        install_singing()
    print("Installation complete. Run './xiaod init', then './xiaod start'.")


def initialize(options):
    """Collect private account values and generate fresh, scoped runtime data."""
    if active_pid():
        raise RuntimeError(
            "Stop this checkout before changing its connection settings."
        )
    if not python_in(ASTRO / ".venv").exists():
        raise RuntimeError("Run './xiaod install' first.")
    sample = json.loads((ROOT / "config.example.json").read_text())
    if SETTINGS.exists():
        sample.update(json.loads(SETTINGS.read_text()))
    if options.config:
        sample.update(json.loads(options.config.read_text()))
    else:
        for key, label in (
            ("bot_qq", "托管机器人的QQ号"),
            ("owner_qq", "你的常用QQ号"),
        ):
            sample[key] = input(
                f"{label} [{sample.get(key, '')}]: "
            ).strip() or sample.get(key, "")
        groups = input("允许接话的群号，逗号分隔；留空保留原设置: ").strip()
        if groups:
            sample["group_qqs"] = [
                value.strip()
                for value in groups.replace("，", ",").split(",")
                if value.strip()
            ]
        sample["deepseek_api_key"] = getpass.getpass(
            "DeepSeek API Key（不回显；留空保留）: "
        ).strip() or sample.get("deepseek_api_key", "")
        sample["mimo_api_key"] = getpass.getpass(
            "MiMo API Key（不回显；留空可先用文字）: "
        ).strip() or sample.get("mimo_api_key", "")
        answer = (
            input("允许小D低频主动私聊你？2～4小时，23:00～09:00静音 [Y/n]: ")
            .strip()
            .lower()
        )
        sample["private_proactive"] = answer not in {"n", "no"}
    for key in ("bot_qq", "owner_qq"):
        if not re.fullmatch(r"[1-9][0-9]{4,11}", str(sample[key])):
            raise RuntimeError(f"Invalid QQ number: {key}")
        sample[key] = str(sample[key])
    sample["group_qqs"] = [str(value) for value in sample["group_qqs"]]
    if not all(
        re.fullmatch(r"[1-9][0-9]{4,11}", value) for value in sample["group_qqs"]
    ):
        raise RuntimeError("A group number is invalid.")
    for key in ("dashboard_port", "onebot_port", "napcat_port"):
        sample[key] = int(sample[key])
        if not 1024 <= sample[key] <= 65535:
            raise RuntimeError(f"Invalid port: {key}")
    if (
        len({sample[key] for key in ("dashboard_port", "onebot_port", "napcat_port")})
        != 3
    ):
        raise RuntimeError("The three local ports must be different.")
    write_json(SETTINGS, sample, private=True)
    run(
        [python_in(ASTRO / ".venv"), ROOT / "tools/configure_runtime.py", SETTINGS],
        cwd=ASTRO,
    )
    print(
        "Configuration complete. Keys and account data are private and ignored by Git."
    )


def start(no_napcat=False, foreground=False):
    """Start only this checkout; leave existing bots and unrelated processes alone."""
    if not SETTINGS.exists():
        raise RuntimeError("Run './xiaod init' first.")
    if active_pid():
        print("This checkout is already running.")
        return
    settings = json.loads(SETTINGS.read_text())
    for key in (
        ("dashboard_port", "onebot_port")
        if no_napcat
        else ("dashboard_port", "onebot_port", "napcat_port")
    ):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", settings[key])) == 0:
                raise RuntimeError(
                    f"Port {settings[key]} is in use. Change this checkout's ports; no existing process was stopped."
                )
    if not no_napcat:
        run([*compose_command(), "up", "-d"], cwd=RUNTIME)
    env = os.environ.copy()
    env["SSL_CERT_FILE"] = subprocess.check_output(
        [str(python_in(ASTRO / ".venv")), "-m", "certifi"], text=True
    ).strip()
    command = [
        str(python_in(ASTRO / ".venv")),
        "-u",
        str(ASTRO / "main.py"),
        "--webui-dir",
        str(ASTRO / "data/dist"),
    ]
    if foreground:
        os.chdir(ASTRO)
        os.execve(command[0], command, env)
    logs = RUNTIME / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / "astrbot.log").open("ab") as output:
        process = subprocess.Popen(
            command,
            cwd=ASTRO,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    write_json(RUNTIME / "process.json", {"pid": process.pid}, private=True)
    print(f"AstrBot: http://127.0.0.1:{settings['dashboard_port']}")
    print(f"QQ login: http://127.0.0.1:{settings['napcat_port']}/webui")
    print(
        "Use './xiaod qq-token' for your local NapCat login token. Scan with the bot account, not the owner account."
    )


def main():
    """Dispatch the local installer and operator commands."""
    parser = argparse.ArgumentParser(description="小D · 鲸鱼娘 QQ 伙伴")
    subs = parser.add_subparsers(dest="command", required=True)
    sub = subs.add_parser("install")
    sub.add_argument("--singing", action="store_true")
    sub.add_argument("--skip-dashboard", action="store_true", help=argparse.SUPPRESS)
    sub = subs.add_parser("init")
    sub.add_argument("--config", type=__import__("pathlib").Path)
    sub = subs.add_parser("start")
    sub.add_argument(
        "--no-napcat",
        action="store_true",
        help="Developer smoke check; QQ will not connect",
    )
    sub.add_argument("--foreground", action="store_true")
    subs.add_parser("stop")
    subs.add_parser("status")
    subs.add_parser("doctor")
    subs.add_parser("qq-token")
    options = parser.parse_args()
    if sys.platform == "win32":
        raise RuntimeError(
            "This release provides macOS/Linux launchers. Windows setup is not validated."
        )
    if options.command == "install":
        install(options.singing, options.skip_dashboard)
    elif options.command == "init":
        initialize(options)
    elif options.command == "start":
        start(options.no_napcat, options.foreground)
    elif options.command == "stop":
        pid = active_pid()
        if pid:
            os.kill(pid, signal.SIGTERM)
        if (RUNTIME / "compose.yaml").exists():
            run([*compose_command(), "down"], cwd=RUNTIME)
        print("This checkout stopped; login and memory files remain in runtime/.")
    elif options.command == "qq-token":
        print(json.loads((RUNTIME / "napcat/config/webui.json").read_text())["token"])
    elif options.command == "status":
        print(
            json.dumps(
                {
                    "pid": active_pid(),
                    "configured": SETTINGS.exists(),
                    "singing_installed": python_in(SINGING / ".venv").exists(),
                    "log": str(RUNTIME / "logs/astrbot.log"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        result = {
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "free_disk_GiB": round(shutil.disk_usage(ROOT).free / 1024**3, 1),
            "commands": {
                name: bool(shutil.which(name))
                for name in ("uv", "git", "curl", "ffmpeg", "ffprobe", "node")
            },
            "runtime_installed": python_in(ASTRO / ".venv").exists(),
        }
        try:
            run([*compose_command(), "version"], stdout=subprocess.DEVNULL)
            result["docker_compose"] = True
        except (RuntimeError, subprocess.CalledProcessError):
            result["docker_compose"] = False
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError, OSError, ValueError) as error:
        print(f"小D setup: {error}", file=sys.stderr)
        raise SystemExit(1)
