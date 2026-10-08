from __future__ import annotations

import json
import os
from typing import Any, cast

from .config import ProjectConfig
from .errors import PolicyDenied
from .utils import run_command

_BROAD_PROJECT_ROLES = {
    "roles/bigquery.admin",
    "roles/bigquery.dataEditor",
    "roles/bigquery.dataOwner",
}


class BigQueryIam:
    """Plan and inspect the two-principal BigQuery boundary without changing IAM."""

    def __init__(self, config: ProjectConfig):
        self.config = config

    def plan(
        self, query_identity: str | None = None, dbt_identity: str | None = None
    ) -> dict[str, Any]:
        query, runner = self._identities(query_identity, dbt_identity)
        bindings: list[dict[str, str]] = []
        for identity, purpose in ((query, "query"), (runner, "dbt")):
            bindings.append(
                {
                    "scope": f"project:{self.config.warehouse.project}",
                    "member": f"serviceAccount:{identity}",
                    "role": "roles/bigquery.jobUser",
                    "purpose": purpose,
                }
            )
        for dataset in self.config.warehouse.read_datasets:
            query_role = "READER"
            runner_role = (
                "WRITER" if dataset in self.config.warehouse.dbt_write_datasets else "READER"
            )
            bindings.extend(
                [
                    {
                        "scope": f"dataset:{self.config.warehouse.project}.{dataset}",
                        "member": f"serviceAccount:{query}",
                        "role": query_role,
                        "purpose": "query",
                    },
                    {
                        "scope": f"dataset:{self.config.warehouse.project}.{dataset}",
                        "member": f"serviceAccount:{runner}",
                        "role": runner_role,
                        "purpose": "dbt",
                    },
                ]
            )
        return {
            "project": self.config.warehouse.project,
            "query_identity": query,
            "dbt_identity": runner,
            "bindings": bindings,
            "forbidden_project_roles": sorted(_BROAD_PROJECT_ROLES),
            "mutates_cloud_state": False,
        }

    def verify(
        self, query_identity: str | None = None, dbt_identity: str | None = None
    ) -> dict[str, Any]:
        query, runner = self._identities(query_identity, dbt_identity)
        project = self.config.warehouse.project
        policy_result = run_command(
            ["gcloud", "projects", "get-iam-policy", project, "--format=json"],
            cwd=self.config.repo_root,
            timeout=60,
        )
        if policy_result.exit_code:
            raise RuntimeError(policy_result.stderr or "unable to read project IAM policy")
        policy = json.loads(policy_result.stdout)
        project_roles = _roles_by_member(policy)
        findings: list[dict[str, Any]] = []
        for identity, purpose in ((query, "query"), (runner, "dbt")):
            member = f"serviceAccount:{identity}"
            roles = project_roles.get(member, set())
            findings.append(
                {
                    "check": f"{purpose}_project_job_user",
                    "ok": "roles/bigquery.jobUser" in roles,
                    "roles": sorted(roles),
                }
            )
            broad = roles & _BROAD_PROJECT_ROLES
            findings.append(
                {
                    "check": f"{purpose}_no_broad_project_data_role",
                    "ok": not broad,
                    "unexpected_roles": sorted(broad),
                }
            )

        from google.cloud import bigquery

        admin_client = bigquery.Client(project=project, location=self.config.warehouse.location)
        for dataset in self.config.warehouse.read_datasets:
            metadata = admin_client.get_dataset(f"{project}.{dataset}")
            grants = {
                str(entry.entity_id): entry.role
                for entry in metadata.access_entries
                if entry.entity_type == "userByEmail"
            }
            expected_runner = (
                "WRITER" if dataset in self.config.warehouse.dbt_write_datasets else "READER"
            )
            findings.extend(
                [
                    {
                        "check": f"query_dataset_{dataset}",
                        "ok": grants.get(query) == "READER",
                        "expected": "READER",
                        "actual": grants.get(query),
                    },
                    {
                        "check": f"dbt_dataset_{dataset}",
                        "ok": grants.get(runner) == expected_runner,
                        "expected": expected_runner,
                        "actual": grants.get(runner),
                    },
                ]
            )
        return {
            "ok": all(finding["ok"] for finding in findings),
            "project": project,
            "findings": findings,
            "note": "Policy inspection is non-mutating; no negative write probe is attempted.",
        }

    def apply(
        self,
        *,
        query_identity: str | None = None,
        dbt_identity: str | None = None,
        approved: bool,
    ) -> dict[str, Any]:
        if not approved:
            raise PolicyDenied("IAM changes require the explicit --approve flag")
        query, runner = self._identities(query_identity, dbt_identity)
        project = self.config.warehouse.project
        operations: list[dict[str, Any]] = []
        for identity, display_name in (
            (query, "dbt-agents read-only query identity"),
            (runner, "dbt-agents dbt runner"),
        ):
            described = run_command(
                [
                    "gcloud",
                    "iam",
                    "service-accounts",
                    "describe",
                    identity,
                    f"--project={project}",
                ],
                cwd=self.config.repo_root,
                timeout=60,
            )
            if described.exit_code:
                account_id = identity.removesuffix(f"@{project}.iam.gserviceaccount.com")
                created = run_command(
                    [
                        "gcloud",
                        "iam",
                        "service-accounts",
                        "create",
                        account_id,
                        f"--project={project}",
                        f"--display-name={display_name}",
                        "--quiet",
                    ],
                    cwd=self.config.repo_root,
                    timeout=120,
                )
                if created.exit_code:
                    raise RuntimeError(created.stderr or f"failed to create {identity}")
                operations.append({"operation": "create_service_account", "identity": identity})
            binding = run_command(
                [
                    "gcloud",
                    "projects",
                    "add-iam-policy-binding",
                    project,
                    f"--member=serviceAccount:{identity}",
                    "--role=roles/bigquery.jobUser",
                    "--condition=None",
                    "--quiet",
                ],
                cwd=self.config.repo_root,
                timeout=120,
            )
            if binding.exit_code:
                raise RuntimeError(binding.stderr or f"failed to grant jobUser to {identity}")
            operations.append(
                {"operation": "grant_project_role", "identity": identity, "role": "jobUser"}
            )

        from google.cloud import bigquery

        client = bigquery.Client(project=project, location=self.config.warehouse.location)
        for dataset_name in self.config.warehouse.read_datasets:
            dataset = client.get_dataset(f"{project}.{dataset_name}")
            desired = {
                query: "READER",
                runner: (
                    "WRITER"
                    if dataset_name in self.config.warehouse.dbt_write_datasets
                    else "READER"
                ),
            }
            access = [
                entry
                for entry in dataset.access_entries
                if not (entry.entity_type == "userByEmail" and entry.entity_id in desired)
            ]
            access.extend(
                bigquery.AccessEntry(role, "userByEmail", identity)
                for identity, role in desired.items()
            )
            dataset.access_entries = access
            client.update_dataset(dataset, ["access_entries"])
            operations.append(
                {
                    "operation": "set_dataset_roles",
                    "dataset": dataset_name,
                    "roles": desired,
                }
            )
        return {
            "ok": True,
            "project": project,
            "query_identity": query,
            "dbt_identity": runner,
            "operations": operations,
            "service_account_keys_created": False,
        }

    def _identities(
        self, query_identity: str | None = None, dbt_identity: str | None = None
    ) -> tuple[str, str]:
        auth = self.config.warehouse.auth
        query = query_identity
        runner = dbt_identity
        if not query and auth.query_service_account_env:
            query = os.getenv(auth.query_service_account_env)
        if not runner and auth.dbt_service_account_env:
            runner = os.getenv(auth.dbt_service_account_env)
        if not query or not runner:
            raise PolicyDenied(
                "query and dbt identities are required as options or configured environment values"
            )
        project = self.config.warehouse.project
        suffix = f"@{project}.iam.gserviceaccount.com"
        if not query.endswith(suffix) or not runner.endswith(suffix):
            raise PolicyDenied("service accounts must belong to the configured GCP project")
        if query == runner:
            raise PolicyDenied("query and dbt identities must be distinct")
        return query, runner


def _roles_by_member(policy: dict[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for binding in policy.get("bindings", []):
        role = cast(str, binding.get("role"))
        for member in binding.get("members", []):
            result.setdefault(member, set()).add(role)
    return result
