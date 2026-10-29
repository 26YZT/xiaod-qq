"""Render an aligned song with the accepted L singing preset, without training."""

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
import wave
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent


def main():
    """Create a complete PCM16 cover and bounded QQ voice segments atomically.

    Raises:
        ValueError: If paths, hashes, duration, or generated samples are invalid.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    options = parser.parse_args()
    request = json.loads(options.request.read_text())
    source = Path(request["source"]).resolve()
    reference = Path(request["reference"]).resolve()
    destination = Path(request["output_dir"]).resolve()
    accompaniment = (
        Path(request["accompaniment"]).resolve()
        if request.get("accompaniment")
        else None
    )
    library = (PROJECT / "datasets/da-fei-yu-girl-voice-zh/singing/train").resolve()
    data_root = (
        PROJECT / "astrbot/data/plugin_data/astrbot_plugin_xiaod_music"
    ).resolve()
    if not (
        source.is_relative_to(library) or source.is_relative_to(data_root / "sources")
    ) or not destination.is_relative_to(data_root / "cache"):
        raise ValueError("The render request points outside the music directories.")
    preset = json.loads((ROOT / "selected_singing_preset.json").read_text())
    if (
        request["voice_id"] != preset["voice_id"]
        or request["reference_sha256"] != preset["reference_sha256"]
        or preset["diffusion_steps"] != 50
        or preset["guidance"] != 0.5
        or preset["pitch_shift_semitones"] != 3
    ):
        raise ValueError("The request does not use the accepted L singing preset.")
    for name, path in (("source", source), ("reference", reference)):
        if hashlib.sha256(path.read_bytes()).hexdigest() != request[f"{name}_sha256"]:
            raise ValueError(f"The {name} file changed.")
    if accompaniment and (
        not accompaniment.is_relative_to(data_root / "sources")
        or hashlib.sha256(accompaniment.read_bytes()).hexdigest()
        != request.get("accompaniment_sha256")
    ):
        raise ValueError(
            "The accompaniment file changed or points outside the sources."
        )
    if request.get("online_source") and not accompaniment:
        raise ValueError("An online accompanied song must include its aligned backing.")
    os.environ.update(
        PYTORCH_ENABLE_MPS_FALLBACK="1",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        HF_HOME=str(ROOT / "seed-vc/checkpoints/hf_cache"),
        HF_HUB_CACHE=str(ROOT / "seed-vc/checkpoints/hf_cache"),
        HF_HUB_DISABLE_PROGRESS_BARS="1",
        MPLCONFIGDIR=str(ROOT / "cache/matplotlib"),
    )
    os.chdir(ROOT / "seed-vc")
    sys.path.insert(0, str(ROOT / "seed-vc"))
    import inference as engine
    import numpy as np
    import soundfile as sf
    import torch

    torch.set_num_threads(4)
    torch.manual_seed(42)
    if torch.backends.mps.is_available():
        torch.mps.manual_seed(42)
    np.random.seed(42)
    info = sf.info(source)
    if not 0 < info.duration <= 360:
        raise ValueError("The available source must be at most six minutes.")
    full = request["full_source"] is True
    start = 0 if full else float(request.get("clip_start", 0))
    duration = info.duration if full else min(12, info.duration - start)
    if start < 0 or duration <= 0:
        raise ValueError("The requested source excerpt is invalid.")
    destination.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    (destination / "progress.json").write_text(json.dumps({"stage": "singing"}))
    mix_metadata = {"has_accompaniment": False}
    with tempfile.TemporaryDirectory(prefix="render-", dir=destination) as temporary:
        temporary = Path(temporary)
        samples, rate = sf.read(
            source,
            start=round(start * info.samplerate),
            frames=round(duration * info.samplerate),
            dtype="float32",
        )
        if samples.ndim == 2:
            samples = samples.mean(axis=1)
        prepared = temporary / "source.wav"
        sf.write(prepared, samples, rate, subtype="PCM_16")
        args = Namespace(
            source=str(prepared),
            target=str(reference),
            output=str(temporary / "generated"),
            diffusion_steps=50,
            length_adjust=1.0,
            inference_cfg_rate=0.5,
            f0_condition=True,
            auto_f0_adjust=False,
            semi_tone_shift=3,
            checkpoint=None,
            config=None,
            fp16=False,
        )
        engine.main(args)
        raw_files = list((temporary / "generated").glob("*.wav"))
        if len(raw_files) != 1:
            raise ValueError("The renderer did not create one complete waveform.")
        output, rate = sf.read(raw_files[0], dtype="float32")
        if not np.isfinite(output).all() or output.size == 0:
            raise ValueError("The rendered waveform is empty or invalid.")
        if abs(len(output) / rate - duration) > 0.4:
            raise ValueError("The rendered waveform lost source duration.")
        peak = float(np.abs(output).max())
        if peak > 0.99:
            output *= 0.95 / peak
        if accompaniment:
            import librosa

            (destination / "progress.json").write_text(json.dumps({"stage": "mixing"}))
            sf.write(destination / "vocals_L.wav", output, rate, subtype="PCM_16")
            backing_info = sf.info(accompaniment)
            if (
                backing_info.samplerate != info.samplerate
                or abs(backing_info.duration - info.duration) > 0.1
                or rate != info.samplerate
            ):
                raise ValueError("The accompaniment does not match the vocal timeline.")
            backing, _ = sf.read(
                accompaniment,
                start=round(start * rate),
                frames=round(duration * rate),
                dtype="float32",
                always_2d=True,
            )
            if not np.isfinite(backing).all():
                raise ValueError("The accompaniment contains invalid samples.")
            backing = librosa.effects.pitch_shift(
                backing.mean(axis=1), sr=rate, n_steps=3, res_type="soxr_hq"
            )
            expected_frames = round(duration * rate)
            output = np.pad(
                output[:expected_frames], (0, max(0, expected_frames - len(output)))
            )
            backing = np.pad(
                backing[:expected_frames], (0, max(0, expected_frames - len(backing)))
            )
            original_rms = float(np.sqrt(np.mean(samples**2)))
            converted_rms = float(np.sqrt(np.mean(output**2)))
            if converted_rms < 0.001:
                raise ValueError("The converted vocal is too quiet to mix.")
            vocal_gain = float(np.clip(original_rms / converted_rms, 0.25, 4.0)) * 1.122
            output = output * vocal_gain + backing
            peak = float(np.abs(output).max())
            mix_gain = min(1.0, 0.95 / max(peak, 1e-6))
            output *= mix_gain
            mix_metadata = {
                "has_accompaniment": True,
                "accompaniment_sha256": request["accompaniment_sha256"],
                "accompaniment_pitch_shift_semitones": 3,
                "vocal_gain": round(vocal_gain, 6),
                "mix_gain": round(mix_gain, 6),
                "public_source": request.get("public_source"),
                "mix_preset": "aligned-original-plus3-v1",
            }
        final = destination / "cover.wav"
        pending = destination / "cover.part.wav"
        sf.write(pending, output, rate, subtype="PCM_16")
        pending.replace(final)
    parts = []
    with wave.open(str(final)) as complete:
        part_frames = complete.getframerate() * 45
        index = 0
        while block := complete.readframes(part_frames):
            index += 1
            path = destination / f"part{index:03d}.wav"
            with wave.open(str(path), "wb") as part:
                part.setparams(complete.getparams())
                part.writeframes(block)
            parts.append(path.name)
    metadata = {
        "cache_key": request["cache_key"],
        "title": request["title"],
        "full_source": full,
        "voice_id": request["voice_id"],
        "reference_sha256": request["reference_sha256"],
        "source_sha256": request["source_sha256"],
        "duration": round(len(output) / rate, 3),
        "parts": parts,
        "cover": final.name,
        "preset": "L-singing-scheme-2",
        "pitch_shift_semitones": 3,
        "diffusion_steps": 50,
        "guidance": 0.5,
        "render_seconds": round(time.monotonic() - started, 3),
        "training": False,
        **mix_metadata,
    }
    pending = destination / "ready.json.tmp"
    pending.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    pending.replace(destination / "ready.json")
    print(json.dumps(metadata, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
