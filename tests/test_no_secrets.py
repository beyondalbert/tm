"""Fail if a tracked file looks like it contains a committed secret.

This is a fast local guard (runs with the normal test suite). The gitleaks CI
job does the deeper scan; this catches the common case before a commit.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("provider key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("openai project key", re.compile(r"\bsk-proj-[A-Za-z0-9_\-]{20,}")),
    ("pypi token", re.compile(r"\bpypi-AgEI[A-Za-z0-9_\-]{10,}")),
    ("aws access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
)

# Values that are intentionally synthetic (used by tests/docs).
ALLOW: tuple[re.Pattern[str], ...] = (
    re.compile(r"^sk-0{32}$"),
    re.compile(r"^sk-x{4,}$"),
)


def _tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"],
        capture_output=True,
        text=True,
        check=False,
    )
    names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return [ROOT / name for name in names]


def test_no_secrets_in_tracked_files() -> None:
    offenders: list[str] = []
    for path in _tracked_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for label, pattern in PATTERNS:
            for match in pattern.finditer(text):
                value = match.group(0)
                if any(allowed.search(value) for allowed in ALLOW):
                    continue
                offenders.append(f"{path.relative_to(ROOT)}: {label}: {value[:8]}...")
    assert not offenders, "possible committed secrets:\n" + "\n".join(offenders)
