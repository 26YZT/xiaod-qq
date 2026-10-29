"""Download immutable models into the singing worker's private cache layout."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SINGING = ROOT / "runtime/song-lab"


def sha256(path):
    """Hash a model in bounded memory."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024**2), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    """Fetch only the fixed inference weights, never training dependencies."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--separator", action="store_true")
    options = parser.parse_args()
    if options.separator:
        from audio_separator.separator import Separator

        specification = json.loads((ROOT / "sources.lock.json").read_text())[
            "separator"
        ]
        directory = SINGING / "separation-models"
        directory.mkdir(parents=True, exist_ok=True)
        separator = Separator(
            model_file_dir=str(directory),
            output_dir=str(SINGING / "cache/setup"),
            output_format="WAV",
        )
        separator.download_model_and_data(specification["model"])
        if sha256(directory / specification["model"]) != specification["model_sha256"]:
            raise RuntimeError(
                "The separation model checksum does not match the release."
            )
        return
    from huggingface_hub import hf_hub_download

    for item in json.loads((ROOT / "singing/models.lock.json").read_text()):
        cache = SINGING / "seed-vc/checkpoints"
        if item["cache"] == "hub":
            cache /= "hf_cache"
        path = Path(
            hf_hub_download(
                repo_id=item["repo"],
                filename=item["filename"],
                revision=item["revision"],
                cache_dir=str(cache),
            )
        )
        if sha256(path) != item["sha256"]:
            raise RuntimeError(
                f"Model checksum mismatch: {item['repo']}/{item['filename']}"
            )
        reference = cache / ("models--" + item["repo"].replace("/", "--")) / "refs/main"
        reference.parent.mkdir(parents=True, exist_ok=True)
        reference.write_text(item["revision"])
    print("All fixed singing model checksums passed.")


if __name__ == "__main__":
    main()
