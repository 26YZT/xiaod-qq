"""Find a public song recording and cache aligned vocal/accompaniment stems."""

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT.parent / "astrbot/data/plugin_data/astrbot_plugin_xiaod_music"
MODEL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
MAX_DURATION = 360


def progress(directory: Path, stage: str):
    """Publish a bounded stage name for the existing QQ progress command.

    Args:
        directory: Validated job directory.
        stage: Current source-processing stage.
    """
    pending = directory / "progress.json.tmp"
    pending.write_text(json.dumps({"stage": stage, "updated_at": time.time()}))
    pending.replace(directory / "progress.json")


def prepare(request: dict) -> dict:
    """Search, download, and separate one bounded song without user credentials.

    Args:
        request: Title and destination from the explicitly requested QQ song.

    Returns:
        dict: Validated aligned source paths, hashes, and public provenance.

    Raises:
        ValueError: If the request or source violates the local audio bounds.
        RuntimeError: If no usable recording or aligned stems can be obtained.
    """
    import numpy as np
    import soundfile as sf
    import yt_dlp
    from opencc import OpenCC

    directory = Path(request["output_dir"]).resolve()
    if not directory.is_relative_to((DATA / "cache").resolve()):
        raise ValueError("The job directory is outside the music cache.")
    title = unicodedata.normalize("NFKC", request["title"]).strip().strip("《》")
    if (
        not 1 <= len(title) <= 80
        or "://" in title
        or any(ord(char) < 32 for char in title)
    ):
        raise ValueError("Only a bounded song title is accepted.")
    directory.mkdir(parents=True, exist_ok=True)
    query_key = hashlib.sha256(title.casefold().encode()).hexdigest()
    source_dir = DATA / "sources" / query_key
    source_dir.mkdir(parents=True, exist_ok=True)
    manifest = source_dir / "ready_source.json"
    if manifest.exists():
        try:
            cached = json.loads(manifest.read_text())
            valid = (
                cached.get("separation_model") == MODEL
                and cached.get("source_pipeline_version") == 2
            )
            for name in ("source", "accompaniment"):
                path = Path(cached[name]).resolve()
                valid = bool(
                    valid
                    and path.is_relative_to(source_dir.resolve())
                    and path.is_file()
                    and hashlib.sha256(path.read_bytes()).hexdigest()
                    == cached[f"{name}_sha256"]
                )
            if valid:
                return cached
        except (KeyError, OSError, TypeError, ValueError):
            pass
    if shutil.disk_usage(DATA).free < 2 * 1024**3:
        raise RuntimeError("Not enough free space to prepare another song.")
    progress(directory, "searching")
    node = shutil.which("node")
    options = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "extract_flat": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 1,
        "js_runtimes": {"node": {"path": node}} if node else {},
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        results = downloader.extract_info(f"ytsearch5:{title} 歌曲", download=False)
    simplified = OpenCC("t2s")
    expected_title = re.sub(
        r"[^a-z0-9\u4e00-\u9fff]", "", simplified.convert(title).casefold()
    )
    if not expected_title:
        raise ValueError("The requested song title has no searchable characters.")
    candidates = []
    for rank, item in enumerate(results.get("entries") or []):
        identifier = str(item.get("id", ""))
        label = str(item.get("title", ""))
        duration = item.get("duration")
        normalized_label = re.sub(
            r"[^a-z0-9\u4e00-\u9fff]", "", simplified.convert(label).casefold()
        )
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]{11}", identifier)
            or expected_title not in normalized_label
            or not isinstance(duration, (int, float))
            or not 15 <= duration <= MAX_DURATION
            or item.get("live_status") in {"is_live", "is_upcoming"}
            or re.search(
                r"伴奏|纯音乐|純音樂|合唱|钢琴|鋼琴|instrumental|karaoke|medley",
                label,
                re.I,
            )
        ):
            continue
        score = -rank * 2
        if re.search(r"官方|原唱|official", label, re.I):
            score += 5
        if re.search(r"AI|翻唱|cover|变声|變聲", label, re.I):
            score -= 8
        candidates.append((score, identifier, label))
    candidates.sort(key=lambda item: item[0], reverse=True)
    selected = None
    progress(directory, "downloading")
    for _, identifier, _ in candidates[:3]:
        download_options = {
            **options,
            "extract_flat": False,
            "skip_download": False,
            "format": "bestaudio/best",
            "outtmpl": str(source_dir / "recording.%(ext)s"),
            "max_filesize": 40 * 1024 * 1024,
            "fragment_retries": 1,
            "cachedir": str(ROOT / "cache/yt-dlp"),
            "match_filter": yt_dlp.utils.match_filter_func(
                f"duration <= {MAX_DURATION} & !is_live"
            ),
        }
        try:
            with yt_dlp.YoutubeDL(download_options) as downloader:
                result = downloader.extract_info(
                    f"https://www.youtube.com/watch?v={identifier}", download=True
                )
                raw = Path(downloader.prepare_filename(result)).resolve()
            if not raw.is_relative_to(source_dir.resolve()) or not raw.is_file():
                continue
            if raw.stat().st_size > 40 * 1024 * 1024:
                continue
            selected = {
                "provider": "youtube",
                "id": identifier,
                "title": str(result.get("title", ""))[:240],
                "uploader": str(result.get("uploader", ""))[:100],
                "url": f"https://www.youtube.com/watch?v={identifier}",
            }
            break
        except Exception as error:
            logging.warning("Public source attempt failed: %s", type(error).__name__)
    if selected is None:
        raise RuntimeError("No accessible recording of at most six minutes was found.")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is not available.")
    recording = source_dir / "recording.wav"
    subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            str(raw),
            "-vn",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(recording),
        ],
        check=True,
        timeout=90,
    )
    source_info = sf.info(recording)
    if not 15 <= source_info.duration <= MAX_DURATION:
        raise ValueError("The decoded recording must be at most six minutes.")
    progress(directory, "separating")
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    from audio_separator.separator import Separator
    import torch

    torch.set_num_threads(4)
    separator = Separator(
        log_level=logging.WARNING,
        model_file_dir=str(ROOT / "separation-models"),
        output_dir=str(source_dir),
        output_format="WAV",
        normalization_threshold=0.99,
        mdxc_params={
            "segment_size": 256,
            "override_model_segment_size": True,
            "batch_size": 1,
            "overlap": 2,
            "pitch_shift": 0,
        },
    )
    separator.load_model(MODEL)
    outputs = separator.separate(str(recording))
    stems = {}
    for name in outputs:
        path = (source_dir / name).resolve()
        if path.parent != source_dir.resolve():
            raise ValueError(
                "The separator returned a path outside its output directory."
            )
        if "(Vocals)" in path.name:
            stems["source"] = path
        elif "(Instrumental)" in path.name:
            stems["accompaniment"] = path
    if set(stems) != {"source", "accompaniment"}:
        raise RuntimeError("The separator did not produce both aligned stems.")
    for name, path in stems.items():
        info = sf.info(path)
        if info.samplerate != 44100 or abs(info.duration - source_info.duration) > 0.1:
            raise ValueError("The separated stems lost the original timing.")
    vocal, rate = sf.read(stems["source"], dtype="float32", always_2d=True)
    mono = vocal.mean(axis=1)
    if not np.isfinite(mono).all() or float(np.max(np.abs(mono))) < 0.01:
        raise ValueError("The selected recording did not yield usable vocals.")
    half_second = rate // 2
    blocks = len(mono) // half_second
    levels = np.sqrt(
        np.mean(mono[: blocks * half_second].reshape(blocks, -1) ** 2, axis=1)
    )
    window = np.ones(24, dtype="float32") / 24
    voiced_energy = np.convolve(levels**2, window, mode="valid")
    clip_start = min(float(np.argmax(voiced_energy)) / 2, source_info.duration - 12)
    prepared = {
        "title": title,
        "source": str(stems["source"]),
        "source_sha256": hashlib.sha256(stems["source"].read_bytes()).hexdigest(),
        "accompaniment": str(stems["accompaniment"]),
        "accompaniment_sha256": hashlib.sha256(
            stems["accompaniment"].read_bytes()
        ).hexdigest(),
        "duration": source_info.duration,
        "clip_start": round(clip_start, 3),
        "separation_model": MODEL,
        "source_pipeline_version": 2,
        "public_source": selected,
        "source_obtained_at": time.time(),
    }
    pending = source_dir / "ready_source.json.tmp"
    pending.write_text(json.dumps(prepared, ensure_ascii=False, indent=2))
    pending.replace(manifest)
    recording.unlink(missing_ok=True)
    return prepared


def main():
    """Prepare one source and publish its metadata only after successful separation.

    Raises:
        RuntimeError: If the worker cannot prepare a valid aligned recording.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    options = parser.parse_args()
    request = json.loads(options.request.read_text())
    directory = Path(request["output_dir"]).resolve()
    result = prepare(request)
    pending = directory / "prepared_source.json.tmp"
    pending.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    pending.replace(directory / "prepared_source.json")
    print(
        json.dumps(
            {
                "stage": "prepared",
                "title": result["title"],
                "duration": result["duration"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
