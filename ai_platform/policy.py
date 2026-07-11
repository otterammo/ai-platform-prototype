from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from .events import CORRELATION_ID_STATUS_KEY, EventContext, correlation_id_from_manifest
from .resources import (
    AgentRunResource,
    ApprovalResource,
    PolicyResource,
    PolicyRule,
    ResourceKind,
    parse_resource,
)
from .storage import CONTROLLER_FIELD_MANAGER, ResourceStore


class PolicyEffect(StrEnum):
    ALLOW = "Allow"
    REQUIRE_APPROVAL = "RequireApproval"
    DENY = "Deny"


@dataclass(frozen=True)
class RuntimeAction:
    tool: str
    operation: str
    details: dict[str, Any] = field(default_factory=dict)
    workspace: str | None = None
    mission: str | None = None
    agent: str | None = None
    agentRun: str | None = None
    correlation_id: str | None = None

    @property
    def action_hash(self) -> str:
        payload = {
            "tool": self.tool,
            "operation": self.operation,
            "details": self.details,
            "workspace": self.workspace,
            "mission": self.mission,
            "agent": self.agent,
            "agentRun": self.agentRun,
        }
        encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tool": self.tool,
            "operation": self.operation,
            "details": self.details,
            "actionHash": self.action_hash,
        }
        if self.workspace:
            payload["workspace"] = self.workspace
        if self.mission:
            payload["mission"] = self.mission
        if self.agent:
            payload["agent"] = self.agent
        if self.agentRun:
            payload["agentRun"] = self.agentRun
        if self.correlation_id:
            payload["correlationId"] = self.correlation_id
        return payload


@dataclass(frozen=True)
class PolicyDecision:
    effect: PolicyEffect
    reason: str
    policy_name: str | None = None
    rule_index: int | None = None


class ApprovalRequired(Exception):
    def __init__(self, approval_id: str, action: RuntimeAction) -> None:
        self.approval_id = approval_id
        self.action = action
        super().__init__(f"approval {approval_id} is required for {action.tool}/{action.operation}")


class PolicyDenied(Exception):
    def __init__(self, action: RuntimeAction, reason: str) -> None:
        self.action = action
        self.reason = reason
        super().__init__(f"policy denied {action.tool}/{action.operation}: {reason}")


class PolicyEngine:
    def __init__(self, store: ResourceStore) -> None:
        self.store = store

    def evaluate(self, action: RuntimeAction) -> PolicyDecision:
        policies = self._policies()
        if not policies:
            return PolicyDecision(PolicyEffect.ALLOW, "NoPolicies")

        for policy in policies:
            for index, rule in enumerate(policy.spec.rules):
                if not self._matches(rule, action):
                    continue
                if rule.allow is True:
                    return PolicyDecision(PolicyEffect.ALLOW, "MatchedRule", policy.metadata.name, index)
                if rule.requiresApproval is True:
                    return PolicyDecision(
                        PolicyEffect.REQUIRE_APPROVAL,
                        "MatchedRule",
                        policy.metadata.name,
                        index,
                    )
                return PolicyDecision(PolicyEffect.DENY, "MatchedRule", policy.metadata.name, index)
        return PolicyDecision(PolicyEffect.DENY, "NoMatchingPolicyRule")

    def authorize(
        self,
        action: RuntimeAction,
        *,
        pause_agent_run: bool = True,
        approval_agent_run: bool = True,
    ) -> PolicyDecision:
        decision = self.evaluate(action)
        self._emit_policy_event("PolicyEvaluated", action, decision, f"Evaluated {action.tool}/{action.operation}")

        if decision.effect == PolicyEffect.ALLOW:
            self._emit_policy_event("PolicyAllowed", action, decision, f"Allowed {action.tool}/{action.operation}")
            return decision

        existing_approval = self._approval_for_action(action)
        if decision.effect == PolicyEffect.REQUIRE_APPROVAL:
            if existing_approval and existing_approval.status.phase == "Approved":
                approved_decision = PolicyDecision(
                    PolicyEffect.ALLOW,
                    "ApprovalAlreadyGranted",
                    decision.policy_name,
                    decision.rule_index,
                )
                self._emit_policy_event(
                    "PolicyAllowed",
                    action,
                    approved_decision,
                    f"Approval {existing_approval.metadata.name} already granted",
                    approval_id=existing_approval.metadata.name,
                )
                return approved_decision
            if existing_approval and existing_approval.status.phase == "Rejected":
                self._emit_policy_event(
                    "PolicyDenied",
                    action,
                    decision,
                    f"Approval {existing_approval.metadata.name} was rejected",
                    approval_id=existing_approval.metadata.name,
                )
                raise PolicyDenied(action, "ApprovalRejected")

            approval, created = self._get_or_create_pending_approval(
                action,
                decision,
                existing_approval,
                approval_agent_run=approval_agent_run,
            )
            if created:
                self._emit_policy_event(
                    "ApprovalRequested",
                    action,
                    decision,
                    f"Approval {approval.metadata.name} requested",
                    approval_id=approval.metadata.name,
                )
            if pause_agent_run:
                self._pause_agent_run(action, decision, approval.metadata.name)
            raise ApprovalRequired(approval.metadata.name, action)

        self._emit_policy_event("PolicyDenied", action, decision, f"Denied {action.tool}/{action.operation}")
        raise PolicyDenied(action, decision.reason)

    def _policies(self) -> list[PolicyResource]:
        policies: list[PolicyResource] = []
        for manifest in self.store.list(ResourceKind.POLICY):
            resource = parse_resource(manifest)
            if isinstance(resource, PolicyResource):
                policies.append(resource)
        return sorted(policies, key=lambda item: item.metadata.name)

    @staticmethod
    def _matches(rule: PolicyRule, action: RuntimeAction) -> bool:
        if rule.match.tool is not None and rule.match.tool != action.tool:
            return False
        if rule.match.operation is not None and rule.match.operation != action.operation:
            return False
        return True

    def _approval_for_action(self, action: RuntimeAction) -> ApprovalResource | None:
        candidates: list[ApprovalResource] = []
        for manifest in self.store.list(ResourceKind.APPROVAL):
            resource = parse_resource(manifest)
            if isinstance(resource, ApprovalResource) and resource.spec.actionHash == action.action_hash:
                candidates.append(resource)
        if not candidates:
            return None
        for phase in ("Pending", "Approved", "Rejected"):
            for approval in candidates:
                if approval.status.phase == phase:
                    return approval
        return candidates[0]

    def _get_or_create_pending_approval(
        self,
        action: RuntimeAction,
        decision: PolicyDecision,
        existing_approval: ApprovalResource | None,
        *,
        approval_agent_run: bool,
    ) -> tuple[ApprovalResource, bool]:
        if existing_approval and existing_approval.status.phase == "Pending":
            return existing_approval, False

        name = self._approval_name()
        manifest = {
            "apiVersion": "ai.platform/v1",
            "kind": "Approval",
            "metadata": {"name": name},
            "spec": {
                "workspace": action.workspace or "",
                "mission": action.mission or "",
                "agent": action.agent or "",
                "agentRun": action.agentRun if approval_agent_run else None,
                "action": action.to_payload(),
                "actionHash": action.action_hash,
                "policy": decision.policy_name,
                "ruleIndex": decision.rule_index,
            },
            "status": {"phase": "Pending"},
        }
        applied = self.store.apply(
            manifest,
            event_context=self._context(action, "CreateApproval", decision.reason),
            field_manager=CONTROLLER_FIELD_MANAGER,
        )
        self.store.update_status(
            ResourceKind.APPROVAL,
            name,
            None,
            "Pending",
            f"Approval requested for {action.tool}/{action.operation}",
            {
                "approvalId": name,
                "policy": decision.policy_name,
                "ruleIndex": decision.rule_index,
                "runtimeAction": action.to_payload(),
            },
        )
        resource = parse_resource(applied)
        if not isinstance(resource, ApprovalResource):
            raise TypeError(f"expected ApprovalResource, got {type(resource).__name__}")
        refreshed = self.store.get(ResourceKind.APPROVAL, name)
        if refreshed is not None:
            parsed = parse_resource(refreshed)
            if isinstance(parsed, ApprovalResource):
                resource = parsed
        return resource, True

    def _approval_name(self) -> str:
        while True:
            name = f"approval-{uuid4().hex[:12]}"
            if self.store.get(ResourceKind.APPROVAL, name) is None:
                return name

    def _pause_agent_run(self, action: RuntimeAction, decision: PolicyDecision, approval_id: str) -> None:
        if not action.agentRun or not action.workspace:
            return
        run_manifest = self.store.get(ResourceKind.AGENT_RUN, action.agentRun, action.workspace)
        if run_manifest is None:
            return
        run = parse_resource(run_manifest)
        if not isinstance(run, AgentRunResource):
            return

        message = f"AgentRun paused pending approval {approval_id}"
        pause_data = {
            "pendingApproval": approval_id,
            "approval": approval_id,
            "approvalId": approval_id,
            "policy": decision.policy_name,
            "ruleIndex": decision.rule_index,
        }
        self.store.update_status(
            ResourceKind.AGENT_RUN,
            run.metadata.name,
            run.metadata.namespace,
            "WaitingForApproval",
            message,
            pause_data,
            event_type="AgentRunWaiting",
            event_context=self._context(action, "PauseAgentRun", "ApprovalRequired"),
        )

    def _emit_policy_event(
        self,
        event_type: str,
        action: RuntimeAction,
        decision: PolicyDecision,
        message: str,
        *,
        approval_id: str | None = None,
    ) -> None:
        payload = self._event_payload(action, decision, approval_id)
        self.store.emit_event(
            event_type,
            ResourceKind.AGENT_RUN if action.agentRun else (ResourceKind.AGENT if action.agent else None),
            action.agentRun or action.agent,
            action.workspace,
            message,
            payload,
            event_context=self._context(action, event_type, decision.reason),
        )

    @staticmethod
    def _event_payload(
        action: RuntimeAction,
        decision: PolicyDecision,
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "runtimeAction": action.to_payload(),
            "actionHash": action.action_hash,
            "decision": decision.effect.value,
            "reason": decision.reason,
            "policy": decision.policy_name,
            "ruleIndex": decision.rule_index,
        }
        if approval_id:
            payload["approvalId"] = approval_id
        return payload

    @staticmethod
    def _context(action: RuntimeAction, controller_action: str, reason: str) -> EventContext:
        return EventContext(
            controller="PolicyEngine",
            action=controller_action,
            reason=reason,
            correlation_id=action.correlation_id,
            workspace=action.workspace,
            mission=action.mission,
        )


class ApprovalService:
    def __init__(self, store: ResourceStore) -> None:
        self.store = store

    def approve(self, name: str, actor: str = "manual", reason: str | None = None) -> dict[str, Any]:
        approval = self._load_pending(name)
        decision_reason = reason or "Approved"
        decided_at = self._now()
        payload = self._approval_event_payload(approval, actor, decision_reason, decided_at)
        self.store.update_status(
            ResourceKind.APPROVAL,
            approval.metadata.name,
            None,
            "Approved",
            f"Approval granted by {actor}",
            {
                "approvedBy": actor,
                "approvedAt": decided_at,
                "reason": decision_reason,
            },
        )
        self.store.emit_event(
            "ApprovalGranted",
            ResourceKind.APPROVAL,
            approval.metadata.name,
            approval.spec.workspace,
            f"Approval {approval.metadata.name} granted by {actor}",
            payload,
            event_context=self._context(approval, "GrantApproval", "ApprovalGranted"),
        )
        self._resume_agent_run(approval, actor)
        refreshed = self.store.get(ResourceKind.APPROVAL, approval.metadata.name)
        if refreshed is None:
            raise KeyError(f"Approval {name} not found after approval")
        return refreshed

    def reject(self, name: str, actor: str = "manual", reason: str | None = None) -> dict[str, Any]:
        approval = self._load_pending(name)
        decision_reason = reason or "Rejected"
        decided_at = self._now()
        payload = self._approval_event_payload(approval, actor, decision_reason, decided_at)
        self.store.update_status(
            ResourceKind.APPROVAL,
            approval.metadata.name,
            None,
            "Rejected",
            f"Approval rejected by {actor}",
            {
                "rejectedBy": actor,
                "rejectedAt": decided_at,
                "reason": decision_reason,
            },
        )
        self.store.emit_event(
            "ApprovalRejected",
            ResourceKind.APPROVAL,
            approval.metadata.name,
            approval.spec.workspace,
            f"Approval {approval.metadata.name} rejected by {actor}",
            payload,
            event_context=self._context(approval, "RejectApproval", "ApprovalRejected"),
        )
        self._fail_agent_run(approval, actor, decision_reason)
        refreshed = self.store.get(ResourceKind.APPROVAL, approval.metadata.name)
        if refreshed is None:
            raise KeyError(f"Approval {name} not found after rejection")
        return refreshed

    def _load_pending(self, name: str) -> ApprovalResource:
        manifest = self.store.get(ResourceKind.APPROVAL, name)
        if manifest is None:
            raise KeyError(f"Approval {name} not found")
        resource = parse_resource(manifest)
        if not isinstance(resource, ApprovalResource):
            raise TypeError(f"expected ApprovalResource, got {type(resource).__name__}")
        if resource.status.phase != "Pending":
            raise ValueError(f"Approval {name} is {resource.status.phase}, not Pending")
        return resource

    def _resume_agent_run(self, approval: ApprovalResource, actor: str) -> None:
        if not approval.spec.agentRun:
            return
        run_manifest = self.store.get(ResourceKind.AGENT_RUN, approval.spec.agentRun, approval.spec.workspace)
        if run_manifest is None:
            return
        run = parse_resource(run_manifest)
        if not isinstance(run, AgentRunResource):
            return
        self.store.update_status(
            ResourceKind.AGENT_RUN,
            run.metadata.name,
            run.metadata.namespace,
            "Pending",
            f"Approval {approval.metadata.name} granted by {actor}; AgentRun resumed",
            {"approval": approval.metadata.name, "approvalId": approval.metadata.name, "approvedBy": actor},
            event_type="AgentRunResumed",
            event_context=self._context(approval, "ResumeAgentRun", "ApprovalGranted"),
            clear_data_keys=["pendingApproval"],
        )

    def _fail_agent_run(self, approval: ApprovalResource, actor: str, reason: str) -> None:
        if not approval.spec.agentRun:
            return
        run_manifest = self.store.get(ResourceKind.AGENT_RUN, approval.spec.agentRun, approval.spec.workspace)
        if run_manifest is None:
            return
        run = parse_resource(run_manifest)
        if not isinstance(run, AgentRunResource):
            return
        message = f"Approval {approval.metadata.name} rejected by {actor}: {reason}"
        self.store.update_status(
            ResourceKind.AGENT_RUN,
            run.metadata.name,
            run.metadata.namespace,
            "Failed",
            message,
            {
                "approval": approval.metadata.name,
                "approvalId": approval.metadata.name,
                "rejectedBy": actor,
                "error": reason,
            },
            event_type="AgentRunFailed",
            event_context=self._context(approval, "FailAgentRun", "ApprovalRejected"),
            clear_data_keys=["pendingApproval"],
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _approval_event_payload(
        approval: ApprovalResource,
        actor: str,
        reason: str,
        decided_at: str,
    ) -> dict[str, Any]:
        return {
            "approvalId": approval.metadata.name,
            "actor": actor,
            "reason": reason,
            "decidedAt": decided_at,
            "runtimeAction": approval.spec.action,
            "actionHash": approval.spec.actionHash,
            "policy": approval.spec.policy,
            "ruleIndex": approval.spec.ruleIndex,
        }

    @staticmethod
    def _context(approval: ApprovalResource, controller_action: str, reason: str) -> EventContext:
        return EventContext(
            controller="ApprovalService",
            action=controller_action,
            reason=reason,
            correlation_id=approval_correlation_id(approval),
            workspace=approval.spec.workspace,
            mission=approval.spec.mission,
        )


def approval_correlation_id(approval: ApprovalResource) -> str | None:
    value = approval.status.data.get(CORRELATION_ID_STATUS_KEY)
    if isinstance(value, str):
        return value
    return correlation_id_from_manifest(approval.model_dump(mode="json", exclude_none=True))
