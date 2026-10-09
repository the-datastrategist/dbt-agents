from __future__ import annotations

from pathlib import Path

import pytest

from dbt_agents.config import ProjectConfig
from dbt_agents.errors import PolicyDenied
from dbt_agents.policy import FilePolicy, SqlPolicy


def test_allows_bounded_select(project_config: ProjectConfig) -> None:
    result = SqlPolicy(project_config).validate_read_query(
        "select count(*) as rows from `example-project.source.events`"
    )
    assert result["relations"] == [
        {"project": "example-project", "dataset": "source", "table": "events"}
    ]


@pytest.mark.parametrize(
    "sql",
    [
        "delete from `example-project.source.events` where true",
        "create table `example-project.dbt_dev.x` as select 1",
        "select 1; select 2",
        "call `example-project.source.proc`()",
        "select * from external_query('connection', 'select 1')",
        "select * from `other-project.source.events`",
        "select * from `example-project.not_allowed.events`",
    ],
)
def test_rejects_unsafe_sql(project_config: ProjectConfig, sql: str) -> None:
    with pytest.raises(PolicyDenied):
        SqlPolicy(project_config).validate_read_query(sql)


def test_cte_is_not_treated_as_physical_table(project_config: ProjectConfig) -> None:
    result = SqlPolicy(project_config).validate_read_query(
        "with x as (select count(*) n from `example-project.source.events`) select max(n) from x"
    )
    assert len(result["relations"]) == 1


def test_raw_rows_disabled_by_default(project_config: ProjectConfig) -> None:
    config = project_config.model_copy(deep=True)
    config.warehouse.allow_raw_rows = False
    with pytest.raises(PolicyDenied, match="raw row"):
        SqlPolicy(config).validate_read_query(
            "select id from `example-project.source.events` limit 1"
        )


def test_protected_column(project_config: ProjectConfig) -> None:
    config = project_config.model_copy(deep=True)
    config.warehouse.protected_columns = ["email"]
    with pytest.raises(PolicyDenied, match="protected"):
        SqlPolicy(config).validate_read_query(
            "select email from `example-project.source.events` limit 1"
        )


@pytest.mark.parametrize(
    "sql",
    [
        "select array_agg(t) from `example-project.source.events` t",
        "select array_agg(struct(t.*)) from `example-project.source.events` t",
        "select count(*), any_value(t) from `example-project.source.events` t",
        "select string_agg(cast(id as string)) from `example-project.source.events`",
    ],
)
def test_raw_row_aggregates_are_rejected(project_config: ProjectConfig, sql: str) -> None:
    config = project_config.model_copy(deep=True)
    config.warehouse.allow_raw_rows = False
    with pytest.raises(PolicyDenied, match="raw row"):
        SqlPolicy(config).validate_read_query(sql)


def test_protected_columns_reject_wildcards_and_whole_rows(
    project_config: ProjectConfig,
) -> None:
    config = project_config.model_copy(deep=True)
    config.warehouse.allow_raw_rows = True
    config.warehouse.protected_columns = ["email"]
    policy = SqlPolicy(config)
    with pytest.raises(PolicyDenied, match="protected"):
        policy.validate_read_query("select t.* from `example-project.source.events` t")
    with pytest.raises(PolicyDenied, match="protected"):
        policy.validate_read_query("select any_value(t) from `example-project.source.events` t")


def test_path_escape_and_protected_file(project_config: ProjectConfig, tmp_path: Path) -> None:
    policy = FilePolicy(project_config)
    with pytest.raises(PolicyDenied, match="escapes"):
        policy.resolve(tmp_path / "outside.txt", must_exist=False)
    secret = project_config.repo_root / ".env"
    secret.write_text("TOKEN=nope", encoding="utf-8")
    with pytest.raises(PolicyDenied, match="protected"):
        policy.assert_readable(secret)

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file.txt").write_text("nope", encoding="utf-8")
    link = project_config.repo_root / "linked"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(PolicyDenied, match="escapes"):
        policy.assert_readable(link / "file.txt")


def test_writable_allowlist(project_config: ProjectConfig) -> None:
    policy = FilePolicy(project_config)
    assert policy.assert_writable("dbt/models/new.sql").name == "new.sql"
    with pytest.raises(PolicyDenied, match="writable"):
        policy.assert_writable("README.md")
