"""Evolution proposal routes.

Endpoints:
- POST /api/v1/crm/{crm_id}/evolution/proposals                              — Create proposal
- POST /api/v1/crm/{crm_id}/evolution/proposals/{proposal_id}/approve        — Approve
- POST /api/v1/crm/{crm_id}/evolution/proposals/{proposal_id}/apply          — Apply
- POST /api/v1/crm/{crm_id}/evolution/proposals/{proposal_id}/reject         — Reject

Two-stage approval: approve marks it ready, apply executes the change.
AI suggestions NEVER apply automatically.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.auth import CurrentUser
from app.config import get_settings, Settings
from app.database import CRMRepository
from app.routers.responses import success_response, error_response
from app.schemas.crm import (
    ApproveProposalRequest,
    CRMConfiguration,
    EvolutionObjectiveRequest,
    ProposedChange,
    RejectProposalRequest,
    WorkflowAction,
    WorkflowTrigger,
)
from app.services.ai_provider import get_ai_provider

router = APIRouter(prefix="/crm/{crm_id}/evolution", tags=["Evolution"])


def _get_repo() -> CRMRepository:
    return CRMRepository()


def _proposal_to_dict(p: dict) -> dict:
    """Format a proposal DB row to API response."""
    return {
        "proposal_id": p["id"],
        "status": p["status"],
        "summary": p["summary"],
        "proposed_change": p["proposed_change"],
        "evidence": p.get("evidence", []),
        "approved_at": p.get("approved_at"),
        "approved_by": p.get("approved_by"),
        "applied_at": p.get("applied_at"),
        "applied_by": p.get("applied_by"),
        "rejected_at": p.get("rejected_at"),
        "rejected_by": p.get("rejected_by"),
        "rejection_reason": p.get("rejection_reason"),
        "application_error": p.get("application_error"),
    }


@router.post("/proposals")
async def create_proposal(
    crm_id: UUID,
    body: EvolutionObjectiveRequest,
    user_id: CurrentUser,
    settings: Annotated[Settings, Depends(get_settings)],
):
    """Generate an evolution proposal using AI (or mock provider)."""
    repo = _get_repo()
    crm = repo.get_crm(str(crm_id), user_id)
    if not crm:
        return error_response(404, "NOT_FOUND", "CRM not found.")
    if crm["status"] != "active":
        return error_response(403, "FORBIDDEN", "CRM is archived.")

    config = CRMConfiguration(**crm["configuration"])

    # Compute basic analytics for the AI
    analytics_data = {"total_leads": 0}

    try:
        provider = get_ai_provider(settings.llm_provider)
        suggestion = provider.generate_evolution_proposal(
            body.objective, crm["configuration"], analytics_data
        )
    except Exception:
        return error_response(502, "AI_PROVIDER_ERROR", "Failed to generate proposal.")

    # Validate the proposed change
    try:
        proposed = suggestion["proposed_change"]
        ProposedChange(**proposed)
    except Exception:
        return error_response(422, "VALIDATION_ERROR", "Generated proposal failed validation.")

    try:
        proposal = repo.create_proposal(
            crm_id=str(crm_id),
            summary=suggestion["summary"],
            proposed_change=proposed,
            evidence=suggestion.get("evidence", []),
            config_version=crm["configuration_version"],
        )
        return success_response(_proposal_to_dict(proposal), status_code=201)
    except Exception:
        return error_response(500, "INTERNAL_ERROR", "Failed to create proposal.")


@router.get("/proposals")
async def list_proposals(crm_id: UUID, user_id: CurrentUser):
    """List evolution proposals for a CRM."""
    repo = _get_repo()
    crm = repo.get_crm(str(crm_id), user_id)
    if not crm:
        return error_response(404, "NOT_FOUND", "CRM not found.")

    proposals = repo.list_proposals(str(crm_id))
    return success_response({
        "items": [_proposal_to_dict(p) for p in proposals],
    })


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(
    crm_id: UUID,
    proposal_id: UUID,
    user_id: CurrentUser,
    body: ApproveProposalRequest | None = None,
):
    """Approve a pending proposal. Does NOT apply it."""
    repo = _get_repo()
    crm = repo.get_crm(str(crm_id), user_id)
    if not crm:
        return error_response(404, "NOT_FOUND", "CRM not found.")
    if crm["status"] != "active":
        return error_response(403, "FORBIDDEN", "CRM is archived.")

    proposal = repo.get_proposal(str(proposal_id), str(crm_id))
    if not proposal:
        return error_response(404, "NOT_FOUND", "Proposal not found.")
    if proposal["status"] != "pending":
        return error_response(422, "VALIDATION_ERROR", f"Proposal is '{proposal['status']}', not pending.")

    # Staleness check
    if proposal["configuration_version_at_creation"] != crm["configuration_version"]:
        return error_response(409, "CONFLICT", "CRM configuration has changed since proposal creation.")

    # Optional version check
    if body and body.expected_version is not None:
        if crm["configuration_version"] != body.expected_version:
            return error_response(409, "CONFLICT", "Configuration version mismatch.")

    # Validate change type
    change = proposal["proposed_change"]
    if change.get("type") not in ("workflow.update", "workflow.create"):
        return error_response(422, "VALIDATION_ERROR", "Proposed change type not allowlisted.")

    result = repo.approve_proposal(str(proposal_id), user_id)
    if not result:
        return error_response(422, "VALIDATION_ERROR", "Could not approve proposal.")

    return success_response(_proposal_to_dict(result))


@router.post("/proposals/{proposal_id}/apply")
async def apply_proposal(
    crm_id: UUID,
    proposal_id: UUID,
    user_id: CurrentUser,
):
    """Apply an approved proposal atomically."""
    repo = _get_repo()
    crm = repo.get_crm(str(crm_id), user_id)
    if not crm:
        return error_response(404, "NOT_FOUND", "CRM not found.")
    if crm["status"] != "active":
        return error_response(403, "FORBIDDEN", "CRM is archived.")

    proposal = repo.get_proposal(str(proposal_id), str(crm_id))
    if not proposal:
        return error_response(404, "NOT_FOUND", "Proposal not found.")
    if proposal["status"] != "approved":
        return error_response(422, "VALIDATION_ERROR", f"Proposal is '{proposal['status']}', not approved.")

    # Staleness re-check
    if proposal["configuration_version_at_creation"] != crm["configuration_version"]:
        return error_response(409, "CONFLICT", "Configuration has changed since proposal creation.")

    change = proposal["proposed_change"]
    change_type = change.get("type")

    try:
        if change_type == "workflow.update":
            # Validate change fields
            wf_id = change.get("workflow_id")
            delay = change.get("delay_hours")
            if not wf_id or delay is None:
                repo.fail_proposal(str(proposal_id), "Missing workflow_id or delay_hours")
                return error_response(422, "VALIDATION_ERROR", "Invalid change: missing required fields.")

            # Fetch workflow
            workflow = repo.get_workflow(wf_id, str(crm_id))
            if not workflow:
                repo.fail_proposal(str(proposal_id), "Workflow not found")
                return error_response(422, "VALIDATION_ERROR", "Target workflow not found or not in CRM.")

            # Update the first action's delay_hours
            actions = workflow.get("actions", [])
            if not actions:
                repo.fail_proposal(str(proposal_id), "Workflow has no actions")
                return error_response(422, "VALIDATION_ERROR", "Workflow has no actions to update.")

            old_actions = list(actions)
            actions[0]["delay_hours"] = delay

            # Apply: update workflow, increment version, mark applied, audit
            repo.update_workflow_actions(wf_id, str(crm_id), actions)
            repo.increment_config_version(str(crm_id), user_id)
            result = repo.apply_proposal(str(proposal_id), user_id)
            repo.insert_audit_log(
                crm_id=str(crm_id),
                user_id=user_id,
                action="apply",
                entity_type="evolution_proposal",
                entity_id=str(proposal_id),
                old_values={"actions": old_actions},
                new_values={"actions": actions, "delay_hours": delay},
            )

        elif change_type == "workflow.create":
            name = change.get("name", "")
            trigger = change.get("trigger", {})
            actions = change.get("actions", [{"type": "create_follow_up_task", "delay_hours": 24}])

            if not name:
                repo.fail_proposal(str(proposal_id), "Missing workflow name")
                return error_response(422, "VALIDATION_ERROR", "Missing workflow name.")

            # Create workflow as draft
            wf = repo.create_workflow(
                crm_id=str(crm_id),
                name=name,
                trigger=trigger,
                actions=actions,
                status="draft",
            )
            repo.increment_config_version(str(crm_id), user_id)
            result = repo.apply_proposal(str(proposal_id), user_id)
            repo.insert_audit_log(
                crm_id=str(crm_id),
                user_id=user_id,
                action="apply",
                entity_type="evolution_proposal",
                entity_id=str(proposal_id),
                new_values={"workflow_id": wf["id"], "name": name},
            )

        else:
            repo.fail_proposal(str(proposal_id), f"Unsupported change type: {change_type}")
            return error_response(422, "VALIDATION_ERROR", "Unsupported change type.")

        if not result:
            return error_response(500, "INTERNAL_ERROR", "Failed to apply proposal.")

        return success_response(_proposal_to_dict(result))

    except Exception as exc:
        # Mark as failed
        repo.fail_proposal(str(proposal_id), str(exc)[:500])
        return error_response(500, "INTERNAL_ERROR", "Proposal application failed.")


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(
    crm_id: UUID,
    proposal_id: UUID,
    body: RejectProposalRequest,
    user_id: CurrentUser,
):
    """Reject a pending or approved proposal."""
    repo = _get_repo()
    crm = repo.get_crm(str(crm_id), user_id)
    if not crm:
        return error_response(404, "NOT_FOUND", "CRM not found.")

    proposal = repo.get_proposal(str(proposal_id), str(crm_id))
    if not proposal:
        return error_response(404, "NOT_FOUND", "Proposal not found.")
    if proposal["status"] not in ("pending", "approved"):
        return error_response(422, "VALIDATION_ERROR", f"Cannot reject proposal in '{proposal['status']}' status.")

    result = repo.reject_proposal(str(proposal_id), user_id, body.reason)
    if not result:
        return error_response(422, "VALIDATION_ERROR", "Could not reject proposal.")

    return success_response(_proposal_to_dict(result))
