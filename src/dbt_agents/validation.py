from __future__ import annotations

from typing import Any

from .service import DbtAgentsService


class ValidationWorkflow:
    """Compose existing safe operations into a repeatable change-validation ladder."""

    def __init__(self, service: DbtAgentsService):
        self.service = service

    def run(
        self,
        project: str,
        *,
        selector: str | None,
        target: str | None,
        with_tests: bool,
        approved: bool,
    ) -> dict[str, Any]:
        steps: list[dict[str, Any]] = []
        status = self.service.repo_status(project, include_diff=False)
        steps.append({"step": "repo_status", "result": status})
        if not status.get("ok"):
            return _summary(steps)

        compiled = self.service.dbt_compile(project, selector, target)
        steps.append({"step": "dbt_compile", "result": compiled})
        if not compiled.get("ok") or not with_tests:
            return _summary(steps)

        if not selector:
            return {
                "ok": False,
                "steps": steps,
                "error": "selector_required",
                "message": "targeted dbt tests require an explicit selector",
            }
        details = {"selector": selector, "target": target}
        plan = self.service.change_plan(project, "dbt_test", details)
        steps.append({"step": "dbt_test_plan", "result": plan})
        if not plan.get("ok"):
            return _summary(steps)
        tested = self.service.dbt_test(
            project,
            selector,
            target=target,
            approved=approved,
            plan_id=plan["data"]["plan_id"],
        )
        steps.append({"step": "dbt_test", "result": tested})
        return _summary(steps)


def _summary(steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ok": all(step["result"].get("ok", False) for step in steps),
        "steps": steps,
    }
