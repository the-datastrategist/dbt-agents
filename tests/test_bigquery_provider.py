from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dbt_agents.config import ProjectConfig
from dbt_agents.providers.bigquery import BigQueryProvider


@dataclass
class FakeField:
    name: str
    field_type: str = "INTEGER"
    mode: str = "NULLABLE"
    description: str | None = None
    fields: tuple[Any, ...] = ()


class FakeIterator:
    schema = [FakeField("rows")]
    total_rows = 1

    def __iter__(self):
        return iter([{"rows": 42}])


class FakeJob:
    def __init__(self, dry_run: bool):
        self.total_bytes_processed = 100
        self.job_id = "job_123"
        self.location = "US"
        self.cache_hit = False
        self.dry_run = dry_run

    def result(self, **_: Any) -> FakeIterator:
        return FakeIterator()

    def cancel(self) -> bool:
        return True


class FakeClient:
    def __init__(self):
        self.jobs: list[FakeJob] = []

    def query(self, _sql: str, job_config: Any, location: str, **_: Any) -> FakeJob:
        assert location == "US"
        job = FakeJob(bool(job_config.dry_run))
        self.jobs.append(job)
        return job


def test_dry_run_precedes_bounded_query(project_config: ProjectConfig) -> None:
    client = FakeClient()
    provider = BigQueryProvider(project_config, client=client)
    result = provider.execute_read("select count(*) as rows from `example-project.source.events`")
    assert [job.dry_run for job in client.jobs] == [True, False]
    assert result["rows"] == [{"rows": 42}]
    assert result["total_bytes_processed"] == 100
