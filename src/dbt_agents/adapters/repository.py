from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

from ..config import ProjectConfig
from ..errors import ExecutionFailed, PolicyDenied, StaleWorkspace
from ..policy import FilePolicy
from ..utils import run_command, sha256_bytes, sha256_file

_SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "target",
    "dbt_packages",
    "logs",
    "dist",
    "build",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".dbt-agents",
}


class RepositoryAdapter:
    def __init__(self, config: ProjectConfig):
        self.config = config
        self.files = FilePolicy(config)
        self.root = config.repo_root
        self.git_root = config.resolved_git_root

    def read(self, path: str, start_line: int = 1, end_line: int | None = None) -> dict[str, Any]:
        file_path = self.files.assert_readable(path)
        raw = file_path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PolicyDenied("file is not valid UTF-8", {"path": path}) from exc
        lines = text.splitlines()
        if start_line < 1:
            raise PolicyDenied("start_line must be at least 1")
        effective_end = min(end_line or len(lines), len(lines))
        if effective_end < start_line - 1:
            raise PolicyDenied("end_line must not precede start_line")
        selected = "\n".join(lines[start_line - 1 : effective_end])
        return {
            "path": file_path.relative_to(self.root).as_posix(),
            "sha256": sha256_bytes(raw),
            "start_line": start_line,
            "end_line": effective_end,
            "total_lines": len(lines),
            "content": selected,
        }

    def search(self, query: str, path: str = ".", *, regex: bool = False) -> dict[str, Any]:
        if not query or len(query) > 500:
            raise PolicyDenied("search query must contain 1-500 characters")
        search_root = self.files.resolve(path)
        if not search_root.is_dir():
            raise PolicyDenied("search path must be a directory", {"path": path})
        try:
            pattern = re.compile(query if regex else re.escape(query))
        except re.error as exc:
            raise PolicyDenied("invalid search regular expression", {"reason": str(exc)}) from exc

        matches: list[dict[str, Any]] = []
        scanned = 0
        for directory, dirnames, filenames in os.walk(search_root, followlinks=False):
            dirnames[:] = [
                name
                for name in dirnames
                if name not in _SKIP_DIRS and self.files.filter_search_path(Path(directory) / name)
            ]
            for name in filenames:
                candidate = Path(directory) / name
                if not self.files.filter_search_path(candidate):
                    continue
                try:
                    if candidate.stat().st_size > self.config.limits.file_max_bytes:
                        continue
                    raw = candidate.read_bytes()
                    if b"\x00" in raw[:8192]:
                        continue
                    text = raw.decode("utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                scanned += 1
                for line_number, line in enumerate(text.splitlines(), 1):
                    if pattern.search(line):
                        matches.append(
                            {
                                "path": candidate.relative_to(self.root).as_posix(),
                                "line": line_number,
                                "text": line[:500],
                            }
                        )
                        if len(matches) >= self.config.limits.search_max_matches:
                            return {"matches": matches, "scanned_files": scanned, "truncated": True}
        return {"matches": matches, "scanned_files": scanned, "truncated": False}

    def status(self, include_diff: bool = True) -> dict[str, Any]:
        branch = run_command(["git", "branch", "--show-current"], cwd=self.git_root, timeout=10)
        status = run_command(
            ["git", "status", "--short", "--untracked-files=normal"],
            cwd=self.git_root,
            timeout=15,
        )
        if branch.exit_code or status.exit_code:
            raise ExecutionFailed("unable to inspect Git repository", {"stderr": status.stderr})
        result: dict[str, Any] = {
            "git_root": str(self.git_root),
            "branch": branch.stdout.strip(),
            "status": status.stdout.splitlines(),
        }
        if include_diff:
            diff = run_command(
                ["git", "diff", "--no-ext-diff", "--unified=3"],
                cwd=self.git_root,
                timeout=20,
            )
            result["diff"] = diff.stdout
            result["diff_truncated"] = len(diff.stdout) >= 200_000
        return result

    def write(self, path: str, content: str, expected_sha256: str | None) -> dict[str, Any]:
        file_path = self.files.assert_writable(path)
        existed = file_path.exists()
        if existed:
            current_hash = sha256_file(file_path)
            if expected_sha256 is None:
                raise StaleWorkspace(
                    "expected_sha256 is required when replacing a file", {"path": path}
                )
            if current_hash != expected_sha256:
                raise StaleWorkspace(
                    "file changed after it was read",
                    {"path": path, "expected": expected_sha256, "actual": current_hash},
                )
        elif expected_sha256 not in (None, ""):
            raise StaleWorkspace(
                "new file must not provide a non-empty expected hash", {"path": path}
            )
        encoded = content.encode("utf-8")
        if len(encoded) > self.config.limits.file_max_bytes:
            raise PolicyDenied("new file content exceeds configured size limit", {"path": path})
        file_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{file_path.name}.", dir=file_path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, file_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {
            "path": file_path.relative_to(self.root).as_posix(),
            "created": not existed,
            "sha256": sha256_bytes(encoded),
            "bytes": len(encoded),
        }

    def publish(
        self,
        action: str,
        *,
        branch: str | None = None,
        message: str | None = None,
        paths: list[str] | None = None,
        title: str | None = None,
        body: str | None = None,
        base: str = "main",
    ) -> dict[str, Any]:
        if action == "branch":
            if not branch or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}", branch):
                raise PolicyDenied("invalid branch name")
            argv = ["git", "switch", "-c", branch]
        elif action == "commit":
            if not message or not paths:
                raise PolicyDenied("commit requires a message and explicit paths")
            resolved_paths = self._git_paths(paths)
            argv = ["git", "commit", "--only", "-m", message, "--", *resolved_paths]
        elif action == "push":
            current = branch or self.status(include_diff=False)["branch"]
            if not current:
                raise PolicyDenied("cannot push a detached HEAD")
            argv = ["git", "push", "--set-upstream", "origin", current]
        elif action == "pr":
            if not title:
                raise PolicyDenied("pull request requires a title")
            argv = ["gh", "pr", "create", "--title", title, "--body", body or "", "--base", base]
        else:
            raise PolicyDenied("unsupported Git publishing action", {"action": action})
        result = run_command(argv, cwd=self.git_root, timeout=120)
        if result.exit_code:
            raise ExecutionFailed(
                "Git publishing operation failed", {"result": result.model_dump()}
            )
        return {"action": action, "result": result.model_dump()}

    def _git_paths(self, paths: list[str]) -> list[str]:
        resolved: list[str] = []
        for path in paths:
            absolute = self.files.assert_writable(path)
            try:
                resolved.append(absolute.relative_to(self.git_root).as_posix())
            except ValueError as exc:
                raise PolicyDenied(
                    "commit path is outside configured git_root", {"path": path}
                ) from exc
        return resolved
