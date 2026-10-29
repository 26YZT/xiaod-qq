"""Install the two isolated inference environments and pinned singing models."""

import json
import shutil
import subprocess

from common import LOCK, ROOT, RUNTIME, SINGING, checkout, download, python_in, run


def install_singing():
    """Install inference only; no GPT-SoVITS installation or model training."""
    for name in ("ffmpeg", "ffprobe", "node"):
        if not shutil.which(name):
            raise RuntimeError(f"Singing needs {name}; see docs/INSTALL.md.")
    required_gib = 2 if (SINGING / "ready.json").exists() else 6
    if shutil.disk_usage(ROOT).free < required_gib * 1024**3:
        raise RuntimeError(
            f"At least {required_gib} GiB of free space is required for this singing installation."
        )
    checkout(SINGING / "seed-vc", LOCK["seed_vc"])
    patch = ROOT / "singing/seed-vc.patch"
    if patch.stat().st_size:
        command = ["git", "-C", str(SINGING / "seed-vc"), "apply"]
        if (
            subprocess.run(
                [*command, "--check", str(patch)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        ):
            run([*command, patch])
        elif (
            subprocess.run(
                [*command, "--reverse", "--check", str(patch)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            != 0
        ):
            raise RuntimeError(
                "The Seed-VC tree has incompatible changes; it was not reset."
            )
    for folder, lockfile in (
        (".venv", "singing.txt"),
        (".separator-venv", "separation.txt"),
    ):
        environment = SINGING / folder
        if not python_in(environment).exists():
            run(["uv", "venv", "--python", "3.11", environment])
        run(
            [
                "uv",
                "pip",
                "sync",
                "--python",
                python_in(environment),
                ROOT / "locks" / lockfile,
            ]
        )
    wheel = RUNTIME / "downloads/audio_separator-0.30.2-py3-none-any.whl"
    download(LOCK["separator"]["wheel_url"], wheel, LOCK["separator"]["wheel_sha256"])
    run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            python_in(SINGING / ".separator-venv"),
            "--no-deps",
            wheel,
        ]
    )
    run([python_in(SINGING / ".venv"), ROOT / "tools/setup_models.py"])
    run(
        [
            python_in(SINGING / ".separator-venv"),
            ROOT / "tools/setup_models.py",
            "--separator",
        ]
    )
    download_songs()
    (SINGING / "ready.json").write_text(
        json.dumps(
            {
                "preset": "L-singing-scheme-2",
                "training": False,
                "model_lock": "singing/models.lock.json",
            }
        )
    )
    print("L singing scheme 2 is ready. Run './xiaod init' to enable it in QQ.")


def download_songs():
    """Fetch the five attributed, checksum-verified noncommercial dry sources."""
    catalog = json.loads((ROOT / "presets/song-catalog.json").read_text())
    print(
        "Dry reference sources: YinZilu/da-fei-yu-girl-voice-zh, CC BY-NC 4.0. See third_party/voice-dataset-LICENSE.md."
    )
    root = RUNTIME / "datasets/da-fei-yu-girl-voice-zh/singing/train"
    for song in catalog["songs"]:
        path = root / song["source"]
        url = (
            "https://www.modelscope.cn/datasets/YinZilu/da-fei-yu-girl-voice-zh/resolve/master/singing/train/"
            + song["source"]
        )
        download(url, path, song["source_sha256"])


if __name__ == "__main__":
    install_singing()
