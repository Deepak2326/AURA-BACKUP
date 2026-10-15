"""Workflow and Analytics routes.

Endpoints:
- POST /api/v1/crm/{crm_id}/workflows     — Create workflow
- GET  /api/v1/crm/{crm_id}/workflows     — List workflows
- GET  /api/v1/crm/{crm_id}/analytics     — Get CRM analytics
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter

from app.auth import CurrentUser
from app.database import CRMRepository
from app.routers.responses import success_response, error_response
from app.schemas.crm import (
    CRMConfiguration,
    CreateWorkflowRequest,
)
from app.services.validation import find_entity

router = APIRouter(prefix="/crm/{crm_id}", tags=["Workflows & Analytics"])


def _get_repo() -> CRMRepository:
    return CRMRepository()


@router.post("/workflows")
async def create_workflow(
    crm_id: UUID,
    body: CreateWorkflowRequest,
    user_id: CurrentUser,
):
    """Create a workflow. AI-generated workflows start as draft."""
    repo = _get_repo()
    crm = repo.get_crm(str(crm_id), user_id)
    if not crm:
        return error_response(404, "NOT_FOUND", "CRM not found.")
    if crm["status"] != "active":
        return error_response(403, "FORBIDDEN", "CRM is archived.")

    # Validate trigger entity exists in CRM
    config = CRMConfiguration(**crm["configuration"])
    entity = find_entity(config, body.trigger.entity)
    if not entity:
        return error_response(422, "VALIDATION_ERROR", f"Trigger entity '{body.trigger.entity}' not in CRM.")

    try:
        wf = repo.create_workflow(
            crm_id=str(crm_id),
            name=body.name,
            trigger=body.trigger.model_dump(),
            actions=[a.model_dump() for a in body.actions],
            status="draft" if not body.enabled else "active",
        )
        return success_response(
            {
                "workflow_id": wf["id"],
                "status": wf["status"],
                "name": wf["name"],
                "trigger": wf["trigger"],
                "actions": wf["actions"],
            },
            status_code=201,
        )
    except Exception:
        return error_response(500, "INTERNAL_ERROR", "Failed to create workflow.")


@router.get("/workflows")
async def list_workflows(crm_id: UUID, user_id: CurrentUser):
    """List workflows for a CRM."""
    repo = _get_repo()
    crm = repo.get_crm(str(crm_id), user_id)
    if not crm:
        return error_response(404, "NOT_FOUND", "CRM not found.")

    try:
        workflows = repo.list_workflows(str(crm_id))
        return success_response({
            "items": [
                {
                    "workflow_id": w["id"],
                    "status": w["status"],
                    "name": w["name"],
                    "trigger": w["trigger"],
                    "actions": w["actions"],
                }
                for w in workflows
            ],
        })
    except Exception:
        return error_response(500, "INTERNAL_ERROR", "Failed to list workflows.")


@router.get("/analytics")
async def get_analytics(crm_id: UUID, user_id: CurrentUser):
    """Get CRM analytics. Resolves lead entity and stage field from configuration."""
    repo = _get_repo()
    crm = repo.get_crm(str(crm_id), user_id)
    if not crm:
        return error_response(404, "NOT_FOUND", "CRM not found.")

    config = CRMConfiguration(**crm["configuration"])

    # Find lead entity (first entity, or one named 'lead')
    lead_entity = find_entity(config, "lead")
    if not lead_entity:
        lead_entity = config.entities[0] if config.entities else None

    if not lead_entity:
        return success_response({
            "total_leads": 0,
            "qualified_leads": 0,
            "won_leads": 0,
            "conversion_rate": 0.0,
            "by_source": [],
            "by_stage": [],
        })

    entity_name = lead_entity.name

    # Find stage/status field
    stage_field = None
    source_field = None
    for f in lead_entity.fields:
        if f.name in ("status", "stage") and f.type == "select":
            stage_field = f
        if f.name == "source" and f.type == "select":
            source_field = f

    try:
        total_leads = repo.count_records(str(crm_id), entity_name)

        qualified = 0
        won = 0
        by_stage: list[dict[str, Any]] = []
        by_source: list[dict[str, Any]] = []

        if stage_field and stage_field.options:
            by_stage = repo.count_records_by_field(str(crm_id), entity_name, stage_field.name)
            # Determine qualified/won from pipeline stages or options
            qualified_stages = {"Qualified", "qualified"}
            won_stages = {"Won", "won", "Closed Won", "closed_won"}

            for entry in by_stage:
                val = entry.get("value", "")
                if val in qualified_stages:
                    qualified = entry.get("count", 0)
                if val in won_stages:
                    won = entry.get("count", 0)

        if source_field:
            by_source = repo.count_records_by_field(str(crm_id), entity_name, source_field.name)

        conversion_rate = won / total_leads if total_leads > 0 else 0.0

        return success_response({
            "total_leads": total_leads,
            "qualified_leads": qualified,
            "won_leads": won,
            "conversion_rate": round(conversion_rate, 4),
            "by_source": by_source,
            "by_stage": by_stage,
        })
    except Exception:
        return error_response(500, "INTERNAL_ERROR", "Failed to compute analytics.")
