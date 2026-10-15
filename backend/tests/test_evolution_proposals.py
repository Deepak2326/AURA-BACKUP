"""Regression tests for evolution proposal lifecycle.

Tests:
1. Successful workflow.create: approve, apply, verify one workflow created.
2. Successful workflow.update: approve, apply, verify field change.
3. Rejected proposal cannot be applied.
4. Invalid/unsupported proposal payloads are rejected.
5. Duplicate application is prevented.
6. Application failure leaves proposal and workflow consistent.
7. Tenant isolation: user cannot access another tenant's proposals/workflows.
8. Audit logging: expected events are recorded.

These tests use mocked Supabase interactions via FastAPI TestClient
with the existing test JWT infrastructure.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from app.main import create_app
from app.config import get_settings
from app.database import CRMRepository

settings = get_settings()
if not settings.supabase_jwt_secret:
    settings.supabase_jwt_secret = "test-jwt-secret-key-32-chars-minimum-length!"

app = create_app()
client = TestClient(app)

USER_A_ID = str(uuid.uuid4())
USER_B_ID = str(uuid.uuid4())

CRM_ID = str(uuid.uuid4())
WORKFLOW_ID = str(uuid.uuid4())
PROPOSAL_ID_WF_UPDATE = str(uuid.uuid4())
PROPOSAL_ID_WF_CREATE = str(uuid.uuid4())
PROPOSAL_ID_REJECTED = str(uuid.uuid4())
PROPOSAL_ID_APPLIED = str(uuid.uuid4())


def create_valid_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "aud": "authenticated",
        "exp": 9999999999,
    }
    return jwt.encode(payload, settings.supabase_jwt_secret, algorithm="HS256")


def auth_headers(user_id: str) -> dict:
    return {"Authorization": f"Bearer {create_valid_token(user_id)}"}


# ── Fixtures ────────────────────────────────────────────────────────


def _mock_crm(owner_id: str = USER_A_ID, version: int = 1, status: str = "active"):
    return {
        "id": CRM_ID,
        "owner_id": owner_id,
        "name": "Test CRM",
        "configuration": {
            "schema_version": "1.0",
            "name": "Test CRM",
            "industry": "technology",
            "entities": [
                {
                    "name": "lead",
                    "label": "Lead",
                    "fields": [
                        {"name": "full_name", "label": "Full Name", "type": "text", "required": True},
                    ],
                }
            ],
            "pipeline_stages": ["New", "Won"],
        },
        "configuration_version": version,
        "status": status,
        "created_at": "2026-10-01T00:00:00Z",
        "updated_at": "2026-10-01T00:00:00Z",
    }


def _mock_workflow(delay_hours: int = 48):
    return {
        "id": WORKFLOW_ID,
        "crm_id": CRM_ID,
        "name": "Follow-up Workflow",
        "trigger": {"type": "record.created", "entity": "lead"},
        "actions": [{"type": "create_follow_up_task", "delay_hours": delay_hours}],
        "status": "draft",
        "created_at": "2026-10-01T00:00:00Z",
        "updated_at": "2026-10-01T00:00:00Z",
    }


def _mock_proposal_wf_update(status: str = "pending", config_version: int = 1):
    return {
        "id": PROPOSAL_ID_WF_UPDATE,
        "crm_id": CRM_ID,
        "status": status,
        "summary": "Reduce delay to 12 hours",
        "proposed_change": {
            "type": "workflow.update",
            "workflow_id": WORKFLOW_ID,
            "delay_hours": 12,
        },
        "evidence": [{"metric": "conversion", "value": 0.15}],
        "configuration_version_at_creation": config_version,
        "created_at": "2026-10-01T00:00:00Z",
        "updated_at": "2026-10-01T00:00:00Z",
        "approved_at": "2026-10-01T01:00:00Z" if status in ("approved", "applied") else None,
        "approved_by": USER_A_ID if status in ("approved", "applied") else None,
        "applied_at": "2026-10-01T02:00:00Z" if status == "applied" else None,
        "applied_by": USER_A_ID if status == "applied" else None,
        "rejected_at": None,
        "rejected_by": None,
        "rejection_reason": None,
        "application_error": None,
    }


def _mock_proposal_wf_create(status: str = "pending", config_version: int = 1):
    return {
        "id": PROPOSAL_ID_WF_CREATE,
        "crm_id": CRM_ID,
        "status": status,
        "summary": "Create new onboarding workflow",
        "proposed_change": {
            "type": "workflow.create",
            "name": "Onboarding Follow-up",
            "trigger": {"type": "record.created", "entity": "lead"},
            "actions": [{"type": "create_follow_up_task", "delay_hours": 24}],
        },
        "evidence": [],
        "configuration_version_at_creation": config_version,
        "created_at": "2026-10-01T00:00:00Z",
        "updated_at": "2026-10-01T00:00:00Z",
        "approved_at": "2026-10-01T01:00:00Z" if status in ("approved", "applied") else None,
        "approved_by": USER_A_ID if status in ("approved", "applied") else None,
        "applied_at": None,
        "applied_by": None,
        "rejected_at": None,
        "rejected_by": None,
        "rejection_reason": None,
        "application_error": None,
    }


def _mock_proposal_rejected(config_version: int = 1):
    return {
        "id": PROPOSAL_ID_REJECTED,
        "crm_id": CRM_ID,
        "status": "rejected",
        "summary": "Rejected proposal",
        "proposed_change": {
            "type": "workflow.update",
            "workflow_id": WORKFLOW_ID,
            "delay_hours": 6,
        },
        "evidence": [],
        "configuration_version_at_creation": config_version,
        "created_at": "2026-10-01T00:00:00Z",
        "updated_at": "2026-10-01T00:00:00Z",
        "approved_at": None,
        "approved_by": None,
        "applied_at": None,
        "applied_by": None,
        "rejected_at": "2026-10-01T01:00:00Z",
        "rejected_by": USER_A_ID,
        "rejection_reason": "Not appropriate",
        "application_error": None,
    }


# ── 1. Successful workflow.update ───────────────────────────────────


@patch("app.routers.evolution._get_repo")
def test_approve_workflow_update_proposal(mock_get_repo):
    """Approve a pending workflow.update proposal."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm()
    repo.get_proposal.return_value = _mock_proposal_wf_update(status="pending")
    approved_proposal = _mock_proposal_wf_update(status="approved")
    repo.approve_proposal.return_value = approved_proposal

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/approve",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "approved"
    assert data["proposal_id"] == PROPOSAL_ID_WF_UPDATE
    mock_get_repo.assert_called_once_with(create_valid_token(USER_A_ID))
    repo.approve_proposal.assert_called_once_with(PROPOSAL_ID_WF_UPDATE, USER_A_ID)
    repo.insert_audit_log.assert_not_called()


@patch("app.routers.evolution._get_repo")
def test_approval_route_rejects_other_tenant_before_rpc(mock_get_repo):
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = None
    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/approve",
        headers=auth_headers(USER_B_ID),
    )
    assert response.status_code == 404
    repo.approve_proposal.assert_not_called()
    mock_get_repo.assert_called_once_with(create_valid_token(USER_B_ID))


@patch("app.routers.evolution._get_repo")
def test_apply_workflow_update_proposal(mock_get_repo):
    """The route delegates workflow.update application to the database function."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm()
    repo.get_proposal.return_value = _mock_proposal_wf_update(status="approved")
    applied = _mock_proposal_wf_update(status="applied")
    repo.apply_proposal.return_value = applied

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/apply",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "applied"

    repo.apply_proposal.assert_called_once_with(PROPOSAL_ID_WF_UPDATE)
    repo.update_workflow_actions.assert_not_called()
    repo.create_workflow.assert_not_called()
    repo.increment_config_version.assert_not_called()
    repo.insert_audit_log.assert_not_called()


# ── 2. Successful workflow.create ───────────────────────────────────


@patch("app.routers.evolution._get_repo")
def test_apply_workflow_create_proposal(mock_get_repo):
    """The route delegates workflow.create application to the database function."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm()
    repo.get_proposal.return_value = _mock_proposal_wf_create(status="approved")

    applied = _mock_proposal_wf_create(status="approved")
    applied["status"] = "applied"
    applied["applied_at"] = "2026-10-01T02:00:00Z"
    applied["applied_by"] = USER_A_ID
    repo.apply_proposal.return_value = applied

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_CREATE}/apply",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "applied"

    repo.apply_proposal.assert_called_once_with(PROPOSAL_ID_WF_CREATE)
    repo.create_workflow.assert_not_called()
    repo.update_workflow_actions.assert_not_called()
    repo.increment_config_version.assert_not_called()
    repo.insert_audit_log.assert_not_called()


# ── 3. Rejected proposal cannot be applied ──────────────────────────


@patch("app.routers.evolution._get_repo")
def test_rejected_proposal_cannot_be_applied(mock_get_repo):
    """A rejected proposal must not be applied."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm()
    repo.get_proposal.return_value = _mock_proposal_rejected()

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_REJECTED}/apply",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 422
    err = response.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert "rejected" in err["message"].lower() or "not approved" in err["message"].lower()


@patch("app.routers.evolution._get_repo")
def test_rejected_proposal_cannot_be_approved(mock_get_repo):
    """A rejected proposal must not be re-approved."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm()
    repo.get_proposal.return_value = _mock_proposal_rejected()

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_REJECTED}/approve",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 422


# ── 4. Invalid / unsupported payloads ───────────────────────────────


@patch("app.routers.evolution._get_repo")
def test_invalid_change_type_rejected_at_approval(mock_get_repo):
    """Proposal with unsupported change type is rejected at approval."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm()
    invalid_proposal = _mock_proposal_wf_update(status="pending")
    invalid_proposal["proposed_change"]["type"] = "entity.delete"  # Not allowlisted
    repo.get_proposal.return_value = invalid_proposal

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/approve",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 422


@patch("app.routers.evolution._get_repo")
def test_workflow_update_missing_workflow_id(mock_get_repo):
    """workflow.update proposal missing workflow_id fails at apply."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm()
    broken = _mock_proposal_wf_update(status="approved")
    del broken["proposed_change"]["workflow_id"]
    repo.get_proposal.return_value = broken
    repo.apply_proposal.return_value = {
        "status": "invalid_change: missing workflow_id or delay_hours",
        "error": "invalid_change: missing workflow_id or delay_hours",
    }

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/apply",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 422
    repo.apply_proposal.assert_called_once_with(PROPOSAL_ID_WF_UPDATE)
    repo.fail_proposal.assert_not_called()


@patch("app.routers.evolution._get_repo")
def test_workflow_update_missing_delay_hours(mock_get_repo):
    """workflow.update proposal missing delay_hours fails at apply."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm()
    broken = _mock_proposal_wf_update(status="approved")
    del broken["proposed_change"]["delay_hours"]
    repo.get_proposal.return_value = broken
    repo.apply_proposal.return_value = {
        "status": "invalid_change: missing workflow_id or delay_hours",
        "error": "invalid_change: missing workflow_id or delay_hours",
    }

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/apply",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 422
    repo.apply_proposal.assert_called_once_with(PROPOSAL_ID_WF_UPDATE)
    repo.fail_proposal.assert_not_called()


@patch("app.routers.evolution._get_repo")
def test_workflow_create_missing_name(mock_get_repo):
    """workflow.create missing name fails at apply."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm()
    broken = _mock_proposal_wf_create(status="approved")
    broken["proposed_change"]["name"] = ""
    repo.get_proposal.return_value = broken
    repo.apply_proposal.return_value = {
        "status": "invalid_change: workflow name must be non-empty",
        "error": "invalid_change: workflow name must be non-empty",
    }

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_CREATE}/apply",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 422
    repo.apply_proposal.assert_called_once_with(PROPOSAL_ID_WF_CREATE)
    repo.fail_proposal.assert_not_called()


@patch("app.routers.evolution._get_repo")
def test_unsupported_change_type_fails_at_apply(mock_get_repo):
    """Unknown change type fails at apply with 422."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm()
    broken = _mock_proposal_wf_update(status="approved")
    broken["proposed_change"]["type"] = "field.add"
    repo.get_proposal.return_value = broken
    repo.apply_proposal.return_value = {
        "status": "invalid_change: unknown change type",
        "error": "invalid_change: unknown change type",
    }

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/apply",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 422
    repo.apply_proposal.assert_called_once_with(PROPOSAL_ID_WF_UPDATE)
    repo.fail_proposal.assert_not_called()


# ── 5. Duplicate application prevented ──────────────────────────────


@patch("app.routers.evolution._get_repo")
def test_already_applied_proposal_cannot_apply_again(mock_get_repo):
    """An already-applied proposal cannot be applied again."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm()
    already_applied = _mock_proposal_wf_update(status="applied")
    repo.get_proposal.return_value = already_applied

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/apply",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 422
    err = response.json()["error"]
    assert "applied" in err["message"].lower() or "not approved" in err["message"].lower()

    # Workflow must not be mutated
    repo.update_workflow_actions.assert_not_called()
    repo.create_workflow.assert_not_called()


@patch("app.routers.evolution._get_repo")
def test_already_applied_proposal_cannot_approve_again(mock_get_repo):
    """An already-applied proposal cannot be re-approved."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm()
    already_applied = _mock_proposal_wf_update(status="applied")
    repo.get_proposal.return_value = already_applied

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/approve",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 422


# ── 6. Application failure consistency ──────────────────────────────


@patch("app.routers.evolution._get_repo")
def test_application_failure_is_reported_without_route_side_effects(mock_get_repo):
    """The SQL function reports failure; the route does not mutate proposal state."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm()
    repo.get_proposal.return_value = _mock_proposal_wf_update(status="approved")
    repo.apply_proposal.return_value = {"status": "apply_failed", "error": "apply_failed"}

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/apply",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 500

    repo.apply_proposal.assert_called_once_with(PROPOSAL_ID_WF_UPDATE)
    repo.update_workflow_actions.assert_not_called()
    repo.create_workflow.assert_not_called()
    repo.increment_config_version.assert_not_called()
    repo.insert_audit_log.assert_not_called()
    repo.fail_proposal.assert_not_called()


@pytest.mark.parametrize(
    ("proposal_kind", "rpc_error"),
    [
        ("workflow.update", "stale"),
        ("workflow.create", "stale"),
        ("workflow.update", "apply_failed"),
        ("workflow.create", "apply_failed"),
    ],
)
@patch("app.routers.evolution._get_repo")
def test_rejected_rpc_leaves_all_application_state_unchanged(
    mock_get_repo, proposal_kind, rpc_error
):
    """A stale/failed RPC cannot leave route-side workflow, version, or audit writes."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm(version=1)
    proposal = (
        _mock_proposal_wf_update(status="approved", config_version=1)
        if proposal_kind == "workflow.update"
        else _mock_proposal_wf_create(status="approved", config_version=1)
    )
    repo.get_proposal.return_value = proposal

    state = {
        "workflow": _mock_workflow(delay_hours=48),
        "created_workflows": [],
        "configuration_version": 1,
        "audit_events": [],
        "proposal_status": "approved",
    }
    repo.get_workflow.return_value = state["workflow"]

    def update_workflow(_workflow_id, _crm_id, actions):
        state["workflow"]["actions"] = actions

    def create_workflow(**workflow):
        state["created_workflows"].append(workflow)
        return {"id": str(uuid.uuid4()), **workflow}

    def increment_version(*_args):
        state["configuration_version"] += 1

    def insert_audit(**event):
        state["audit_events"].append(event)

    repo.update_workflow_actions.side_effect = update_workflow
    repo.create_workflow.side_effect = create_workflow
    repo.increment_config_version.side_effect = increment_version
    repo.insert_audit_log.side_effect = insert_audit
    repo.apply_proposal.return_value = {"status": rpc_error, "error": rpc_error}

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{proposal['id']}/apply",
        headers=auth_headers(USER_A_ID),
    )

    assert response.status_code == (409 if rpc_error == "stale" else 500)
    assert state == {
        "workflow": _mock_workflow(delay_hours=48),
        "created_workflows": [],
        "configuration_version": 1,
        "audit_events": [],
        "proposal_status": "approved",
    }
    repo.apply_proposal.assert_called_once_with(proposal["id"])
    repo.fail_proposal.assert_not_called()


def test_apply_repository_propagates_rpc_failure_without_status_fallback():
    """An RPC failure must not trigger a direct proposal status update."""
    client_stub = MagicMock()
    client_stub.rpc.side_effect = RuntimeError("database unavailable")
    repo = CRMRepository(client=client_stub)

    with pytest.raises(RuntimeError, match="Failed to apply evolution proposal"):
        repo.apply_proposal(PROPOSAL_ID_WF_UPDATE)

    client_stub.table.assert_not_called()


@patch("app.routers.evolution._get_repo")
def test_workflow_not_found_is_reported_by_database_function(mock_get_repo):
    """Workflow validation errors come from the database function."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm()
    repo.get_proposal.return_value = _mock_proposal_wf_update(status="approved")
    repo.apply_proposal.return_value = {
        "status": "invalid_change: workflow not found or does not belong to CRM",
        "error": "invalid_change: workflow not found or does not belong to CRM",
    }

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/apply",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 422
    repo.apply_proposal.assert_called_once_with(PROPOSAL_ID_WF_UPDATE)
    repo.fail_proposal.assert_not_called()


@patch("app.routers.evolution._get_repo")
def test_workflow_with_no_actions_fails(mock_get_repo):
    """workflow.update on a workflow with empty actions array fails."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm()
    repo.get_proposal.return_value = _mock_proposal_wf_update(status="approved")
    repo.apply_proposal.return_value = {
        "status": "invalid_change: workflow has no actions to update",
        "error": "invalid_change: workflow has no actions to update",
    }

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/apply",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 422
    repo.apply_proposal.assert_called_once_with(PROPOSAL_ID_WF_UPDATE)
    repo.fail_proposal.assert_not_called()


# ── 7. Tenant isolation ─────────────────────────────────────────────


@patch("app.routers.evolution._get_repo")
def test_tenant_b_cannot_approve_tenant_a_proposal(mock_get_repo):
    """User B cannot approve User A's proposal — CRM ownership check."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    # User B's get_crm for user A's CRM returns None (not owner)
    repo.get_crm.return_value = None

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/approve",
        headers=auth_headers(USER_B_ID),
    )
    assert response.status_code == 404


@patch("app.routers.evolution._get_repo")
def test_tenant_b_cannot_apply_tenant_a_proposal(mock_get_repo):
    """User B cannot apply User A's proposal — CRM ownership check."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = None  # User B doesn't own this CRM

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/apply",
        headers=auth_headers(USER_B_ID),
    )
    assert response.status_code == 404


@patch("app.routers.evolution._get_repo")
def test_tenant_b_cannot_reject_tenant_a_proposal(mock_get_repo):
    """User B cannot reject User A's proposal."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = None

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/reject",
        headers=auth_headers(USER_B_ID),
        json={"reason": "Not my CRM, but I'll try"},
    )
    assert response.status_code == 404


@patch("app.routers.evolution._get_repo")
def test_tenant_b_cannot_list_tenant_a_proposals(mock_get_repo):
    """User B cannot list User A's proposals."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = None

    response = client.get(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals",
        headers=auth_headers(USER_B_ID),
    )
    assert response.status_code == 404


# ── 8. Audit logging ────────────────────────────────────────────────


@patch("app.routers.evolution._get_repo")
def test_audit_log_recorded_for_workflow_update_apply(mock_get_repo):
    """The API delegates update and audit work to the database function."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm()
    repo.get_proposal.return_value = _mock_proposal_wf_update(status="approved")
    applied = _mock_proposal_wf_update(status="applied")
    repo.apply_proposal.return_value = applied

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/apply",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 200

    repo.apply_proposal.assert_called_once_with(PROPOSAL_ID_WF_UPDATE)
    repo.insert_audit_log.assert_not_called()
    repo.increment_config_version.assert_not_called()
    repo.update_workflow_actions.assert_not_called()


@patch("app.routers.evolution._get_repo")
def test_audit_log_recorded_for_workflow_create_apply(mock_get_repo):
    """The API delegates create and audit work to the database function."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm()
    repo.get_proposal.return_value = _mock_proposal_wf_create(status="approved")

    applied = _mock_proposal_wf_create(status="approved")
    applied["status"] = "applied"
    repo.apply_proposal.return_value = applied

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_CREATE}/apply",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 200

    repo.apply_proposal.assert_called_once_with(PROPOSAL_ID_WF_CREATE)
    repo.insert_audit_log.assert_not_called()
    repo.increment_config_version.assert_not_called()
    repo.create_workflow.assert_not_called()


# ── Staleness checks ────────────────────────────────────────────────


@patch("app.routers.evolution._get_repo")
def test_stale_proposal_rejected_at_approval(mock_get_repo):
    """Proposal is rejected at approval when CRM version has changed."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    # CRM at version 2, proposal created at version 1
    repo.get_crm.return_value = _mock_crm(version=2)
    repo.get_proposal.return_value = _mock_proposal_wf_update(status="pending", config_version=1)

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/approve",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 409


@patch("app.routers.evolution._get_repo")
def test_stale_proposal_rejected_at_apply(mock_get_repo):
    """The database function detects stale versions and the route returns conflict."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm(version=2)
    repo.get_proposal.return_value = _mock_proposal_wf_update(status="approved", config_version=1)
    repo.apply_proposal.return_value = {"status": "stale", "error": "stale"}

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/apply",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 409
    repo.apply_proposal.assert_called_once_with(PROPOSAL_ID_WF_UPDATE)


# ── Archived CRM checks ─────────────────────────────────────────────


@patch("app.routers.evolution._get_repo")
def test_archived_crm_blocks_approval(mock_get_repo):
    """Cannot approve proposals for an archived CRM."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm(status="archived")

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/approve",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 403


@patch("app.routers.evolution._get_repo")
def test_archived_crm_blocks_apply(mock_get_repo):
    """Cannot apply proposals for an archived CRM."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm(status="archived")

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/apply",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 403


# ── Unauthenticated access blocked ──────────────────────────────────


def test_unauthenticated_cannot_approve():
    """Unauthenticated requests to approve are rejected."""
    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/approve",
    )
    assert response.status_code == 401


def test_unauthenticated_cannot_apply():
    """Unauthenticated requests to apply are rejected."""
    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/apply",
    )
    assert response.status_code == 401


def test_unauthenticated_cannot_reject():
    """Unauthenticated requests to reject are rejected."""
    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/reject",
        json={"reason": "Unauthorized attempt"},
    )
    assert response.status_code == 401


@patch("app.routers.evolution._get_repo")
def test_approve_db_returns_stale_maps_to_409(mock_get_repo):
    """When approve_proposal returns stale from DB, API returns 409 CONFLICT."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm()
    repo.get_proposal.return_value = _mock_proposal_wf_update(status="pending")
    repo.approve_proposal.return_value = {"status": "stale", "error": "stale"}

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/approve",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == "CONFLICT"


@patch("app.routers.evolution._get_repo")
def test_approve_db_returns_not_owner_maps_to_404(mock_get_repo):
    """When approve_proposal returns not_owner from DB, API returns 404 NOT_FOUND."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm()
    repo.get_proposal.return_value = _mock_proposal_wf_update(status="pending")
    repo.approve_proposal.return_value = {"status": "not_owner", "error": "not_owner"}

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/approve",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "NOT_FOUND"


@patch("app.routers.evolution._get_repo")
def test_apply_db_returns_stale_maps_to_409(mock_get_repo):
    """When apply_proposal returns stale from DB, API returns 409 CONFLICT."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm()
    repo.get_proposal.return_value = _mock_proposal_wf_update(status="approved")
    repo.get_workflow.return_value = _mock_workflow()
    repo.apply_proposal.return_value = {"status": "stale", "error": "stale"}

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/apply",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == "CONFLICT"


@patch("app.routers.evolution._get_repo")
def test_apply_db_returns_apply_failed_maps_to_500(mock_get_repo):
    """When apply_proposal returns apply_failed from DB, API returns 500 INTERNAL_ERROR."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm()
    repo.get_proposal.return_value = _mock_proposal_wf_update(status="approved")
    repo.get_workflow.return_value = _mock_workflow()
    repo.apply_proposal.return_value = {"status": "apply_failed", "error": "apply_failed"}

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/apply",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 500
    data = response.json()
    assert data["error"]["code"] == "INTERNAL_ERROR"


@patch("app.routers.evolution._get_repo")
def test_apply_db_returns_invalid_change_maps_to_422(mock_get_repo):
    """When apply_proposal returns invalid_change from DB, API returns 422 VALIDATION_ERROR."""
    repo = MagicMock()
    mock_get_repo.return_value = repo
    repo.get_crm.return_value = _mock_crm()
    repo.get_proposal.return_value = _mock_proposal_wf_update(status="approved")
    repo.get_workflow.return_value = _mock_workflow()
    repo.apply_proposal.return_value = {
        "status": "invalid_change: delay_hours out of range",
        "error": "invalid_change: delay_hours out of range",
    }

    response = client.post(
        f"/api/v1/crm/{CRM_ID}/evolution/proposals/{PROPOSAL_ID_WF_UPDATE}/apply",
        headers=auth_headers(USER_A_ID),
    )
    assert response.status_code == 422
    data = response.json()
    assert data["error"]["code"] == "VALIDATION_ERROR"


def test_approve_repository_uses_rpc_without_python_status_fallback():
    db = MagicMock()
    proposal = {"id": PROPOSAL_ID_WF_UPDATE, "status": "approved"}
    db.rpc.return_value.execute.return_value.data = "approved"
    db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [proposal]
    result = CRMRepository(client=db).approve_proposal(PROPOSAL_ID_WF_UPDATE, USER_A_ID)
    assert result == proposal
    db.rpc.assert_called_once_with("approve_evolution_proposal", {"proposal_uuid": PROPOSAL_ID_WF_UPDATE})
    db.table.assert_called_once_with("evolution_proposals")
    db.table.return_value.update.assert_not_called()


def test_approve_repository_propagates_rpc_failure_without_fallback():
    db = MagicMock()
    db.rpc.side_effect = RuntimeError("rpc unavailable")
    with pytest.raises(RuntimeError, match="rpc unavailable"):
        CRMRepository(client=db).approve_proposal(PROPOSAL_ID_WF_UPDATE, USER_A_ID)
    db.table.assert_not_called()

def test_proposal_response_does_not_expose_raw_application_error():
    from app.routers.evolution import _proposal_to_dict

    proposal = _mock_proposal_wf_update(status="failed")
    proposal["application_error"] = "internal SQL constraint details"
    response = _proposal_to_dict(proposal)
    assert response["status"] == "failed"
    assert "application_error" not in response
