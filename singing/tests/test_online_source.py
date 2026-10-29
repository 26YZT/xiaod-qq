"""Verify title matching, offline reuse, and source/cache path restrictions."""

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
import yt_dlp

SCRIPT = Path(__file__).resolve().parents[1] / "prepare_online_song.py"
SPEC = importlib.util.spec_from_file_location("online_source_test_target", SCRIPT)
source_worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source_worker)


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / "music"
        self.directory = self.data / "cache/job"
        self.directory.mkdir(parents=True)
        self.request = {"title": "测试歌曲", "output_dir": str(self.directory)}
        self.network = []
        self.entries = []
        owner = self

        class Downloader:
            def __init__(self, options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def extract_info(self, query, download):
                owner.network.append(download)
                if download:
                    raise AssertionError(
                        "An unrelated or blocked source must not download."
                    )
                return {"entries": owner.entries}

        self.paths = patch.object(source_worker, "DATA", self.data)
        self.paths.start()
        self.addCleanup(self.paths.stop)
        self.downloader = patch.object(yt_dlp, "YoutubeDL", Downloader)
        self.downloader.start()
        self.addCleanup(self.downloader.stop)

    def make_cache(self):
        """Create real audio fixtures for reusable and corrupted-source cases.

        Returns:
            tuple[Path, dict]: Source directory and its trusted local manifest.
        """
        key = hashlib.sha256("测试歌曲".encode()).hexdigest()
        directory = self.data / "sources" / key
        directory.mkdir(parents=True)
        manifest = {
            "separation_model": source_worker.MODEL,
            "source_pipeline_version": 2,
            "duration": 15,
            "clip_start": 0,
        }
        for name, frequency in (("source", 300), ("accompaniment", 500)):
            path = directory / f"{name}.wav"
            audio = 0.1 * np.sin(np.arange(44100 * 15) / 44100 * np.pi * 2 * frequency)
            sf.write(path, audio, 44100, subtype="PCM_16")
            manifest[name] = str(path)
            manifest[f"{name}_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        (directory / "ready_source.json").write_text(json.dumps(manifest))
        return directory, manifest

    def test_a_different_song_or_karaoke_result_cannot_be_presented_as_the_request(
        self,
    ):
        self.entries = [
            {"id": "abcdefghijk", "title": label, "duration": 120}
            for label in ("另一首歌曲", "测试歌曲 伴奏", "测试歌曲 合唱团")
        ]
        with self.assertRaises(RuntimeError):
            source_worker.prepare(self.request)
        self.assertEqual(self.network, [False])

    def test_valid_stems_are_reused_without_search_or_download(self):
        _, expected = self.make_cache()
        self.assertEqual(source_worker.prepare(self.request), expected)
        self.assertFalse(self.network)

    def test_a_cached_path_cannot_escape_its_source_directory(self):
        directory, manifest = self.make_cache()
        escaped = self.root / "outside.wav"
        Path(manifest["source"]).rename(escaped)
        manifest["source"] = str(escaped)
        (directory / "ready_source.json").write_text(json.dumps(manifest))
        with self.assertRaises(RuntimeError):
            source_worker.prepare(self.request)
        self.assertEqual(self.network, [False])

    def test_user_urls_and_destinations_outside_the_cache_are_rejected(self):
        for request in (
            {**self.request, "title": "https://127.0.0.1/private"},
            {**self.request, "output_dir": str(self.root / "outside")},
        ):
            with self.assertRaises(ValueError):
                source_worker.prepare(request)
        self.assertFalse(self.network)


if __name__ == "__main__":
    unittest.main()
