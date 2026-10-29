"""Check aligned accompaniment, fixed L settings, and safe render boundaries."""

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import soundfile as sf

SCRIPT = Path(__file__).resolve().parents[1] / "render_singing.py"
SPEC = importlib.util.spec_from_file_location("render_singing_test_target", SCRIPT)
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        self.root = self.project / "song-lab"
        (self.root / "seed-vc").mkdir(parents=True)
        self.data = self.project / "astrbot/data/plugin_data/astrbot_plugin_xiaod_music"
        self.source = (
            self.project / "datasets/da-fei-yu-girl-voice-zh/singing/train/source.wav"
        )
        self.backing = self.data / "sources/recording/backing.wav"
        self.reference = self.root / "reference.wav"
        for path, frequency, amplitude in (
            (self.source, 220, 0.08),
            (self.backing, 330, 0.12),
            (self.reference, 300, 0.1),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            signal = amplitude * np.sin(
                np.arange(88200) / 44100 * np.pi * 2 * frequency
            )
            sf.write(path, signal, 44100, subtype="PCM_16")
        self.directory = self.data / "cache/test"
        self.directory.mkdir(parents=True)
        self.request = {
            "title": "Synthetic song",
            "source": str(self.source),
            "source_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
            "reference": str(self.reference),
            "reference_sha256": hashlib.sha256(self.reference.read_bytes()).hexdigest(),
            "voice_id": "test-L",
            "output_dir": str(self.directory),
            "full_source": True,
            "cache_key": "test",
        }
        (self.root / "selected_singing_preset.json").write_text(
            json.dumps(
                {
                    "voice_id": "test-L",
                    "reference_sha256": self.request["reference_sha256"],
                    "diffusion_steps": 50,
                    "guidance": 0.5,
                    "pitch_shift_semitones": 3,
                }
            )
        )
        self.calls = []

        def fake_engine(args):
            self.calls.append(args)
            output = Path(args.output)
            output.mkdir()
            audio, rate = sf.read(args.source)
            signal = 0.12 * np.sin(
                np.arange(len(audio)) / rate * np.pi * 2 * 220 * 2 ** (3 / 12)
            )
            sf.write(output / "generated.wav", signal, rate, subtype="PCM_16")

        self.engine = SimpleNamespace(main=fake_engine)

    def run_render(self):
        """Run the real mixing path while replacing only expensive voice inference.

        Returns:
            dict: Completed waveform metadata.
        """
        path = self.directory / "request.json"
        path.write_text(json.dumps(self.request))
        previous = Path.cwd()
        previous_engine = sys.modules.get("inference")
        sys.modules["inference"] = self.engine
        try:
            with (
                patch.object(renderer, "ROOT", self.root),
                patch.object(renderer, "PROJECT", self.project),
                patch.object(sys, "argv", [str(SCRIPT), str(path)]),
            ):
                renderer.main()
        finally:
            os.chdir(previous)
            if previous_engine is None:
                sys.modules.pop("inference", None)
            else:
                sys.modules["inference"] = previous_engine
        return json.loads((self.directory / "ready.json").read_text())

    def test_mix_keeps_both_notes_in_the_same_shifted_key_and_exact_timeline(self):
        self.request.update(
            accompaniment=str(self.backing),
            accompaniment_sha256=hashlib.sha256(self.backing.read_bytes()).hexdigest(),
        )
        ready = self.run_render()
        self.assertTrue(ready["has_accompaniment"])
        self.assertEqual(ready["duration"], 2)
        self.assertEqual(self.calls[0].diffusion_steps, 50)
        self.assertEqual(self.calls[0].inference_cfg_rate, 0.5)
        self.assertEqual(self.calls[0].semi_tone_shift, 3)
        signal, rate = sf.read(self.directory / "cover.wav")
        spectrum = np.abs(np.fft.rfft(signal))
        frequencies = np.fft.rfftfreq(len(signal), 1 / rate)
        for frequency in (220 * 2 ** (3 / 12), 330 * 2 ** (3 / 12)):
            near = np.abs(frequencies - frequency) < 1
            self.assertGreater(float(spectrum[near].max()), 300)
        self.assertLessEqual(float(np.abs(signal).max()), 0.951)
        with (
            wave.open(str(self.directory / "cover.wav")) as full,
            wave.open(str(self.directory / "part001.wav")) as part,
        ):
            self.assertEqual(
                full.readframes(full.getnframes()), part.readframes(part.getnframes())
            )

    def test_explicit_dry_render_preserves_the_previous_single_voice_path(self):
        ready = self.run_render()
        self.assertFalse(ready["has_accompaniment"])
        self.assertFalse((self.directory / "vocals_L.wav").exists())
        self.assertEqual(ready["preset"], "L-singing-scheme-2")

    def test_another_reference_is_rejected_before_inference(self):
        self.request["voice_id"] = "another-voice"
        with self.assertRaises(ValueError):
            self.run_render()
        self.assertFalse(self.calls)
        self.assertFalse((self.directory / "ready.json").exists())

    def test_changed_backing_is_rejected_before_inference(self):
        self.request.update(
            accompaniment=str(self.backing), accompaniment_sha256="incorrect"
        )
        with self.assertRaises(ValueError):
            self.run_render()
        self.assertFalse(self.calls)

    def test_sources_outside_the_music_library_are_rejected(self):
        self.request["source"] = str(self.project / "outside.wav")
        with self.assertRaises(ValueError):
            self.run_render()
        self.assertFalse(self.calls)


if __name__ == "__main__":
    unittest.main()
