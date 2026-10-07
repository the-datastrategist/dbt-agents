from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from .config import ProjectConfig
from .errors import ApprovalRequired, PolicyDenied
from .models import ApprovalLevel, PolicyDecision

_ALWAYS_PROTECTED_PARTS = {".git", ".env", ".venv", ".dbt-agents", "__pycache__"}
_SECRET_FILE_RE = re.compile(
    r"(?i)(?:service[-_]?account|credential|private[-_]?key|secrets?)(?:\.[^.]+)?$"
)


class FilePolicy:
    def __init__(self, config: ProjectConfig):
        self.config = config
        self.root = config.repo_root.resolve()

    def resolve(self, requested: str | Path, *, must_exist: bool = True) -> Path:
        requested_path = Path(requested)
        candidate = requested_path if requested_path.is_absolute() else self.root / requested_path
        try:
            resolved = candidate.resolve(strict=must_exist)
        except FileNotFoundError as exc:
            raise PolicyDenied("path does not exist", {"path": str(requested)}) from exc
        if self.root not in (resolved, *resolved.parents):
            raise PolicyDenied("path escapes configured repository root", {"path": str(requested)})
        relative = resolved.relative_to(self.root)
        self._deny_protected(relative)
        return resolved

    def assert_readable(self, requested: str | Path) -> Path:
        path = self.resolve(requested)
        if not path.is_file():
            raise PolicyDenied("path is not a regular file", {"path": str(requested)})
        if path.stat().st_size > self.config.limits.file_max_bytes:
            raise PolicyDenied(
                "file exceeds configured size limit",
                {"path": str(requested), "bytes": path.stat().st_size},
            )
        if b"\x00" in path.read_bytes()[:8192]:
            raise PolicyDenied("binary files cannot be read", {"path": str(requested)})
        return path

    def assert_writable(self, requested: str | Path) -> Path:
        path = self.resolve(requested, must_exist=False)
        relative = path.relative_to(self.root).as_posix()
        if not any(_glob_matches(relative, pattern) for pattern in self.config.writable_paths):
            raise PolicyDenied("path is outside configured writable paths", {"path": relative})
        parent = path.parent.resolve()
        if self.root not in (parent, *parent.parents):
            raise PolicyDenied(
                "write parent escapes repository through a symlink", {"path": relative}
            )
        return path

    def filter_search_path(self, path: Path) -> bool:
        try:
            relative = path.resolve().relative_to(self.root)
            self._deny_protected(relative)
            return True
        except (ValueError, PolicyDenied, FileNotFoundError):
            return False

    def _deny_protected(self, relative: Path) -> None:
        posix = relative.as_posix()
        if any(
            part in _ALWAYS_PROTECTED_PARTS or part.startswith(".env.") for part in relative.parts
        ):
            raise PolicyDenied("protected path cannot be accessed", {"path": posix})
        if _SECRET_FILE_RE.search(relative.name):
            raise PolicyDenied("credential-like path cannot be accessed", {"path": posix})
        if any(_glob_matches(posix, pattern) for pattern in self.config.protected_paths):
            raise PolicyDenied("configured protected path cannot be accessed", {"path": posix})


def _glob_matches(path: str, pattern: str) -> bool:
    normalized = pattern.replace("\\", "/")
    return fnmatch.fnmatchcase(path, normalized) or PurePosixPath(path).match(normalized)


class SqlPolicy:
    _forbidden_text = re.compile(
        r"(?is)\b(?:INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|TRUNCATE|CALL|"
        r"EXPORT\s+DATA|LOAD\s+DATA|EXECUTE\s+IMMEDIATE|BEGIN|COMMIT|ROLLBACK|GRANT|REVOKE)\b"
    )
    _qualified_routine = re.compile(r"(?i)\b`?[\w-]+`?\s*\.\s*`?[\w-]+`?\s*\.\s*`?[\w-]+`?\s*\(")
    _external_function = re.compile(r"(?i)\b(?:EXTERNAL_QUERY|AI\.[A-Z_]+)\s*\(")

    def __init__(self, config: ProjectConfig):
        self.config = config
        self.warehouse = config.warehouse

    def validate_read_query(self, sql: str) -> dict[str, object]:
        if not sql.strip():
            raise PolicyDenied("query is empty")
        if self._forbidden_text.search(_strip_comments_and_strings(sql)):
            raise PolicyDenied("query contains a forbidden mutating or scripting keyword")
        if self._qualified_routine.search(_strip_comments_and_strings(sql)):
            raise PolicyDenied(
                "qualified routines are disabled because they may invoke remote code"
            )
        if self._external_function.search(_strip_comments_and_strings(sql)):
            raise PolicyDenied("external query and AI functions are disabled")
        try:
            import sqlglot
            from sqlglot import exp
        except ImportError as exc:
            raise PolicyDenied("SQL validation requires the bigquery package extra") from exc
        try:
            statements = sqlglot.parse(sql, read="bigquery")
        except Exception as exc:
            raise PolicyDenied(
                "query could not be parsed as BigQuery SQL", {"reason": str(exc)}
            ) from exc
        if len(statements) != 1:
            raise PolicyDenied("exactly one SQL statement is allowed")
        statement = statements[0]
        if not isinstance(statement, exp.Select | exp.Union | exp.Intersect | exp.Except):
            raise PolicyDenied(
                "only read query statements are allowed", {"type": type(statement).__name__}
            )

        forbidden_types = tuple(
            kind
            for name in (
                "Insert",
                "Update",
                "Delete",
                "Merge",
                "Create",
                "Drop",
                "Alter",
                "Command",
                "Transaction",
                "Commit",
                "Rollback",
                "Grant",
                "Revoke",
                "Copy",
                "LoadData",
            )
            if (kind := getattr(exp, name, None)) is not None
        )
        if forbidden_types and any(statement.find_all(*forbidden_types)):
            raise PolicyDenied("query AST contains a forbidden operation")

        ctes = {cte.alias_or_name.casefold() for cte in statement.find_all(exp.CTE)}
        protected = {column.casefold() for column in self.warehouse.protected_columns}
        selected_columns = {
            column.name.casefold() for column in statement.find_all(exp.Column) if column.name
        }
        exposed = protected & selected_columns
        if exposed:
            raise PolicyDenied("query references protected columns", {"columns": sorted(exposed)})
        relations: list[dict[str, str]] = []
        for table in statement.find_all(exp.Table):
            name = table.name
            if name.casefold() in ctes and not table.db and not table.catalog:
                continue
            project = table.catalog or self.warehouse.project
            dataset = table.db or self.warehouse.default_dataset
            if project != self.warehouse.project:
                raise PolicyDenied(
                    "query references a non-allowlisted project", {"project": project}
                )
            if not dataset:
                raise PolicyDenied(
                    "unqualified table requires warehouse.default_dataset", {"table": name}
                )
            if dataset not in self.warehouse.read_datasets:
                raise PolicyDenied(
                    "query references a non-allowlisted dataset", {"dataset": dataset}
                )
            relations.append({"project": project, "dataset": dataset, "table": name})
        if relations and not self.warehouse.allow_raw_rows:
            has_aggregate = any(statement.find_all(exp.AggFunc))
            if not has_aggregate:
                raise PolicyDenied(
                    "raw row queries are disabled; use aggregate diagnostics "
                    "or enable allow_raw_rows"
                )
        return {"statement_type": type(statement).__name__, "relations": relations}


class PolicyEngine:
    def __init__(self, config: ProjectConfig):
        self.config = config
        self.files = FilePolicy(config)
        self.sql = SqlPolicy(config)

    @staticmethod
    def allow(level: ApprovalLevel, *rules: str) -> PolicyDecision:
        return PolicyDecision(decision="allow", level=level, rule_ids=list(rules))

    @staticmethod
    def require_approval(
        approved: bool,
        level: ApprovalLevel,
        reasons: Iterable[str],
        *rules: str,
    ) -> PolicyDecision:
        reasons_list = list(reasons)
        if not approved:
            raise ApprovalRequired(
                "operation requires explicit approval",
                {"level": int(level), "reasons": reasons_list, "rules": list(rules)},
            )
        return PolicyDecision(
            decision="allow",
            level=level,
            rule_ids=list(rules),
            reasons=reasons_list,
        )


def _strip_comments_and_strings(sql: str) -> str:
    # The AST is authoritative. This normalized copy only makes the fast keyword
    # check ignore harmless text in comments and literals.
    without_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    without_line = re.sub(r"--[^\n]*", " ", without_block)
    return re.sub(r"'(?:''|[^'])*'", "''", without_line)
