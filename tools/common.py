"""Shared, project-scoped paths and installation utilities."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
ASTRO = RUNTIME / "astrbot"
SINGING = RUNTIME / "song-lab"
SETTINGS = ROOT / ".local/settings.json"
LOCK = json.loads((ROOT / "sources.lock.json").read_text())


def python_in(environment: Path) -> Path:
    """Return the interpreter inside a POSIX virtual environment."""
    return environment / "bin/python"


def run(arguments, cwd=ROOT, **kwargs):
    """Run an argument vector without a shell or secret-bearing command echo."""
    subprocess.run([str(arg) for arg in arguments], cwd=cwd, check=True, **kwargs)


def write_json(path: Path, value, private=False):
    """Write JSON atomically, restricting generated account configuration."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    if private:
        temporary.chmod(0o600)
    temporary.replace(path)


def checksum(path: Path) -> str:
    """Hash a potentially large model without reading it all into memory."""
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024**2), b""):
            result.update(block)
    return result.hexdigest()


def download(url: str, destination: Path, expected=None):
    """Download through system TLS and verify immutable release assets."""
    if destination.exists() and expected and checksum(destination) == expected:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    run(
        [
            "curl",
            "--fail",
            "--location",
            "--retry",
            "2",
            "--connect-timeout",
            "20",
            "--max-time",
            "1800",
            "--output",
            temporary,
            url,
        ]
    )
    if expected and checksum(temporary) != expected:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Checksum mismatch: {destination.name}")
    temporary.replace(destination)


def checkout(target: Path, specification: dict):
    """Create an immutable upstream checkout without resetting existing changes."""
    if not (target / ".git").exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        if specification.get("tag"):
            run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--branch",
                    specification["tag"],
                    specification["url"],
                    target,
                ]
            )
        else:
            run(["git", "clone", "--depth", "1", specification["url"], target])
    revision = subprocess.check_output(
        ["git", "-C", str(target), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != specification["commit"]:
        raise RuntimeError(
            f"Unexpected upstream revision in {target}; refusing to reset it."
        )


def docker_command():
    """Locate Docker Desktop's CLI without altering the user's shell settings."""
    candidate = shutil.which("docker")
    bundled = Path("/Applications/Docker.app/Contents/Resources/bin/docker")
    if candidate:
        return [candidate, "compose"]
    if bundled.exists():
        return [str(bundled), "compose"]
    raise RuntimeError(
        "Docker Desktop/Engine is required to connect QQ. See docs/INSTALL.md."
    )


def compose_command():
    """Keep this checkout's QQ container isolated from other installations."""
    project = "xiaod-qq-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:8]
    return [*docker_command(), "-p", project, "-f", RUNTIME / "compose.yaml"]


def active_pid():
    """Return only a running AstrBot process owned by this checkout."""
    path = RUNTIME / "process.json"
    if not path.exists():
        return None
    pid = json.loads(path.read_text()).get("pid")
    try:
        command = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "args="], text=True, stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError:
        return None
    if str(ASTRO / "main.py") not in command:
        raise RuntimeError(
            "The saved process id belongs to another program; it will not be stopped."
        )
    return int(pid)
