from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from .errors import ExecutionFailed
from .models import CommandResult

_SECRET_OUTPUT = [
    re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s]+"),
    re.compile(r"(?i)((?:password|token|secret)\s*[=:]\s*)[^\s]+"),
]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def sanitize_output(value: str, max_chars: int = 200_000) -> str:
    value = value[:max_chars]
    for pattern in _SECRET_OUTPUT:
        value = pattern.sub(r"\1[REDACTED]", value)
    return value


def run_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout: int,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    if not argv or any("\x00" in arg for arg in argv):
        raise ExecutionFailed("invalid command arguments")
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    start = time.monotonic()
    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            env=process_env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        timed_out = False
        stdout, stderr, exit_code = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        stderr += f"\nCommand timed out after {timeout} seconds."
        exit_code = 124
    duration_ms = int((time.monotonic() - start) * 1000)
    return CommandResult(
        argv=list(argv),
        cwd=str(cwd),
        exit_code=exit_code,
        stdout=sanitize_output(stdout),
        stderr=sanitize_output(stderr),
        duration_ms=duration_ms,
        timed_out=timed_out,
    )
