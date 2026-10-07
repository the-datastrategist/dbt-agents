from __future__ import annotations

import base64
import datetime as dt
import decimal
import json
import os
import re
from typing import Any

from ..config import ProjectConfig
from ..errors import ExecutionFailed, PolicyDenied
from ..policy import SqlPolicy


class BigQueryProvider:
    def __init__(self, config: ProjectConfig, client: Any | None = None):
        self.config = config
        self.warehouse = config.warehouse
        self.limits = config.limits
        self.policy = SqlPolicy(config)
        self._client = client

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": "bigquery",
            "project": self.warehouse.project,
            "location": self.warehouse.location,
            "read_datasets": self.warehouse.read_datasets,
            "dbt_write_datasets": self.warehouse.dbt_write_datasets,
            "supports": ["describe_relation", "dry_run", "execute_read", "cancel"],
            "limits": {
                "max_bytes_billed": self.limits.query_max_bytes_billed,
                "max_rows": self.limits.query_max_rows,
                "timeout_seconds": self.limits.query_timeout_seconds,
                "max_result_bytes": self.limits.query_max_result_bytes,
            },
        }

    def describe_relation(self, relation: str) -> dict[str, Any]:
        project, dataset, table = self._parse_relation(relation)
        client = self._get_client()
        try:
            metadata = client.get_table(f"{project}.{dataset}.{table}")
        except Exception as exc:
            raise ExecutionFailed(
                "unable to describe BigQuery relation", {"reason": str(exc)}
            ) from exc
        return {
            "project": project,
            "dataset": dataset,
            "table": table,
            "type": getattr(metadata, "table_type", None),
            "num_rows": getattr(metadata, "num_rows", None),
            "num_bytes": getattr(metadata, "num_bytes", None),
            "partitioning": _partitioning(metadata),
            "clustering_fields": getattr(metadata, "clustering_fields", None),
            "schema": [_field_to_dict(field) for field in metadata.schema],
        }

    def dry_run(self, sql: str) -> dict[str, Any]:
        validation = self.policy.validate_read_query(sql)
        bigquery = _bigquery_module()
        config = bigquery.QueryJobConfig(
            dry_run=True,
            use_query_cache=False,
            use_legacy_sql=False,
            maximum_bytes_billed=self.limits.query_max_bytes_billed,
            labels={"tool": "dbt-agents", "operation": "dry-run"},
        )
        try:
            retry = _bounded_retry(self.limits.query_timeout_seconds)
            job = self._get_client().query(
                sql,
                job_config=config,
                location=self.warehouse.location,
                retry=retry,
                job_retry=retry,
                timeout=self.limits.query_timeout_seconds,
            )
        except Exception as exc:
            raise ExecutionFailed("BigQuery dry run failed", {"reason": str(exc)}) from exc
        estimated = int(job.total_bytes_processed or 0)
        if estimated > self.limits.query_max_bytes_billed:
            raise PolicyDenied(
                "query exceeds configured byte budget",
                {"estimated_bytes": estimated, "maximum_bytes": self.limits.query_max_bytes_billed},
            )
        return {
            "validation": validation,
            "estimated_bytes": estimated,
            "maximum_bytes_billed": self.limits.query_max_bytes_billed,
        }

    def execute_read(self, sql: str) -> dict[str, Any]:
        estimate = self.dry_run(sql)
        bigquery = _bigquery_module()
        config = bigquery.QueryJobConfig(
            use_legacy_sql=False,
            maximum_bytes_billed=self.limits.query_max_bytes_billed,
            labels={"tool": "dbt-agents", "operation": "read-query"},
        )
        client = self._get_client()
        job = None
        try:
            retry = _bounded_retry(self.limits.query_timeout_seconds)
            job = client.query(
                sql,
                job_config=config,
                location=self.warehouse.location,
                retry=retry,
                job_retry=retry,
                timeout=self.limits.query_timeout_seconds,
            )
            iterator = job.result(
                timeout=self.limits.query_timeout_seconds,
                max_results=self.limits.query_max_rows,
            )
            columns = [field.name for field in iterator.schema]
            rows: list[dict[str, Any]] = []
            used_bytes = 0
            truncated = False
            for row in iterator:
                serialized_row = {
                    column: _json_value(row[column], self.limits.query_max_cell_chars)
                    for column in columns
                }
                encoded_size = len(json.dumps(serialized_row, default=str).encode("utf-8"))
                if used_bytes + encoded_size > self.limits.query_max_result_bytes:
                    truncated = True
                    break
                rows.append(serialized_row)
                used_bytes += encoded_size
                if len(rows) >= self.limits.query_max_rows:
                    truncated = bool(getattr(iterator, "total_rows", len(rows)) > len(rows))
                    break
        except Exception as exc:
            if job is not None:
                try:
                    job.cancel()
                except Exception:
                    pass
            raise ExecutionFailed(
                "BigQuery read query failed",
                {"reason": str(exc), "job_id": getattr(job, "job_id", None)},
            ) from exc
        return {
            "job_id": job.job_id,
            "location": job.location,
            "estimated_bytes": estimate["estimated_bytes"],
            "total_bytes_processed": int(job.total_bytes_processed or 0),
            "cache_hit": bool(job.cache_hit),
            "columns": columns,
            "rows": rows,
            "returned_rows": len(rows),
            "total_rows": getattr(iterator, "total_rows", None),
            "result_bytes": used_bytes,
            "truncated": truncated,
        }

    def cancel(self, job_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,1024}", job_id):
            raise PolicyDenied("invalid BigQuery job ID")
        try:
            cancelled = self._get_client().cancel_job(
                job_id,
                location=self.warehouse.location,
                project=self.warehouse.project,
            )
        except Exception as exc:
            raise ExecutionFailed("unable to cancel BigQuery job", {"reason": str(exc)}) from exc
        return {"job_id": job_id, "cancel_requested": bool(cancelled)}

    def _parse_relation(self, relation: str) -> tuple[str, str, str]:
        parts = relation.strip("`").split(".")
        if len(parts) == 2:
            project, dataset, table = self.warehouse.project, *parts
        elif len(parts) == 3:
            project, dataset, table = parts
        else:
            raise PolicyDenied("relation must be dataset.table or project.dataset.table")
        if project != self.warehouse.project:
            raise PolicyDenied("relation project is not allowlisted", {"project": project})
        if dataset not in self.warehouse.read_datasets:
            raise PolicyDenied("relation dataset is not allowlisted", {"dataset": dataset})
        if not all(re.fullmatch(r"[A-Za-z0-9_-]+", part) for part in (project, dataset, table)):
            raise PolicyDenied("relation contains invalid identifier characters")
        return project, dataset, table

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        bigquery = _bigquery_module()
        credentials = _credentials(self.config)
        self._client = bigquery.Client(
            project=self.warehouse.project,
            credentials=credentials,
            location=self.warehouse.location,
        )
        return self._client


def _credentials(config: ProjectConfig) -> Any:
    try:
        import google.auth
        from google.auth import impersonated_credentials
        from google.oauth2 import service_account
    except ImportError as exc:
        raise ExecutionFailed("BigQuery authentication libraries are not installed") from exc
    auth = config.warehouse.auth
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
    if auth.mode == "service_account_file":
        filename = os.getenv(auth.query_credentials_file_env)
        if not filename:
            raise PolicyDenied(f"{auth.query_credentials_file_env} is required")
        return service_account.Credentials.from_service_account_file(filename, scopes=scopes)
    source, _ = google.auth.default(scopes=scopes)
    if auth.mode == "impersonation":
        if not auth.query_service_account_env:
            raise PolicyDenied("query_service_account_env is required for impersonation")
        target = os.getenv(auth.query_service_account_env)
        if not target:
            raise PolicyDenied(f"{auth.query_service_account_env} is not set")
        return impersonated_credentials.Credentials(
            source_credentials=source,
            target_principal=target,
            target_scopes=scopes,
            lifetime=900,
        )
    return source


def _bigquery_module() -> Any:
    try:
        from google.cloud import bigquery
    except ImportError as exc:
        raise ExecutionFailed("install dbt-agents[bigquery] to use BigQuery") from exc
    return bigquery


def _bounded_retry(timeout_seconds: int) -> Any:
    try:
        from google.api_core.retry import Retry
    except ImportError as exc:
        raise ExecutionFailed("google-api-core is required for BigQuery") from exc
    return Retry(initial=0.5, maximum=2.0, multiplier=2.0, deadline=timeout_seconds)


def _field_to_dict(field: Any) -> dict[str, Any]:
    return {
        "name": field.name,
        "type": field.field_type,
        "mode": field.mode,
        "description": field.description,
        "fields": [_field_to_dict(child) for child in field.fields],
    }


def _partitioning(table: Any) -> dict[str, Any] | None:
    time_partitioning = getattr(table, "time_partitioning", None)
    if time_partitioning:
        return {
            "type": str(time_partitioning.type_),
            "field": time_partitioning.field,
            "expiration_ms": time_partitioning.expiration_ms,
        }
    if getattr(table, "range_partitioning", None):
        return {"type": "RANGE", "field": table.range_partitioning.field}
    return None


def _json_value(value: Any, max_chars: int) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, dt.date | dt.time | dt.datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")[:max_chars]
    if isinstance(value, dict):
        return {str(key): _json_value(item, max_chars) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item, max_chars) for item in value]
    return str(value)[:max_chars]
