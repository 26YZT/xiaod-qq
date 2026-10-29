"""Check the publication tree, excluding generated private runtime directories."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {
    ".git",
    "runtime",
    ".local",
    ".tools",
    ".cache",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
}
PATTERNS = {
    "service credential": re.compile(
        r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[opusr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{25,})\b"
    ),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "personal home path": re.compile(
        r"/Users/[^/\s]+/(?:Documents|Downloads|\.codex)/"
    ),
}


def files():
    """Yield only files intended for the public repository."""
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if path.is_file() and not any(part in EXCLUDED for part in relative.parts):
            yield path


def audit():
    """Return findings without printing matched credential values."""
    findings = []
    count = 0
    for path in files():
        count += 1
        relative = str(path.relative_to(ROOT))
        if path.is_symlink():
            findings.append({"path": relative, "reason": "symlink"})
        if path.suffix.lower() in {
            ".db",
            ".sqlite",
            ".sqlite3",
            ".log",
            ".pem",
            ".key",
        }:
            findings.append({"path": relative, "reason": "private/runtime file type"})
        if path.stat().st_size > 50 * 1024**2:
            findings.append({"path": relative, "reason": "oversized model/data file"})
        try:
            content = path.read_text()
        except UnicodeError:
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(content):
                findings.append({"path": relative, "reason": label})
    return {"files_checked": count, "findings": findings}


if __name__ == "__main__":
    report = audit()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(bool(report["findings"]))
