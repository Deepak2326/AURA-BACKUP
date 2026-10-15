"""Automated Integration & Security Test Suite for Live Development Database.

This suite executes against the live Supabase project ccoxnsdyhbrvbghcefkt.
It validates:
1. Two distinct authenticated test users (User A and User B) with genuine user-scoped JWTs.
2. CRM and record CRUD operations through user-scoped clients.
3. Database-enforced Row Level Security (RLS) and cross-tenant isolation.
4. Two-stage evolution proposal lifecycle (approve -> apply) via database stored procedures.
5. Atomic workflow mutation, version increment, and audit logging.
6. Safe cleanup strictly scoped to created test IDs without affecting any team data.

Run with:
    RUN_LIVE_DB_TESTS=1 python -m pytest tests/test_live_db.py -v
"""
from __future__ import annotations

import os
import uuid
import pytest
from typing import Any
from supabase import create_client, Client
from fastapi.testclient import TestClient

from app.config import get_settings
from app.database import CRMRepository
from app.main import create_app

api_client = TestClient(create_app())

# Mark all tests in this file as live-database tests
LIVE_TEST_ENV_VARS = (
    "LIVE_TEST_USER_A_EMAIL",
    "LIVE_TEST_USER_A_PASSWORD",
    "LIVE_TEST_USER_B_EMAIL",
    "LIVE_TEST_USER_B_PASSWORD",
)
LIVE_TEST_CONFIGURED = (
    os.environ.get("RUN_LIVE_DB_TESTS") == "1"
    and all(os.environ.get(name) for name in LIVE_TEST_ENV_VARS)
)
pytestmark = pytest.mark.skipif(
    not LIVE_TEST_CONFIGURED,
    reason="Live database tests require RUN_LIVE_DB_TESTS=1 and dedicated test-user credentials",
)

USER_A_EMAIL = os.environ.get("LIVE_TEST_USER_A_EMAIL", "")
USER_B_EMAIL = os.environ.get("LIVE_TEST_USER_B_EMAIL", "")
USER_A_PASSWORD = os.environ.get("LIVE_TEST_USER_A_PASSWORD", "")
USER_B_PASSWORD = os.environ.get("LIVE_TEST_USER_B_PASSWORD", "")


@pytest.fixture(scope="session")
def auth_context():
    """Authenticates two dedicated test users and provides user-scoped JWT clients."""
    settings = get_settings()
    target_url = settings.supabase_url
    assert target_url and "ccoxnsdyhbrvbghcefkt" in target_url, (
        f"Target URL must point to ccoxnsdyhbrvbghcefkt, got: {target_url}"
    )
    anon_key = settings.supabase_anon_key
    assert anon_key and anon_key != "mock-anon-key", "Requires valid Supabase anon key"

    # Base client for authentication
    auth_client = create_client(target_url, anon_key)

    # 1. Authenticate User A
    session_a = auth_client.auth.sign_in_with_password(
        {"email": USER_A_EMAIL, "password": USER_A_PASSWORD}
    )
    assert session_a.session and session_a.session.access_token, "User A authentication failed"
    token_a = session_a.session.access_token
    user_a_id = session_a.user.id

    client_a = create_client(target_url, anon_key)
    client_a.postgrest.auth(token_a)
    repo_a = CRMRepository(client=client_a)

    # 2. Authenticate User B
    session_b = auth_client.auth.sign_in_with_password(
        {"email": USER_B_EMAIL, "password": USER_B_PASSWORD}
    )
    assert session_b.session and session_b.session.access_token, "User B authentication failed"
    token_b = session_b.session.access_token
    user_b_id = session_b.user.id

    client_b = create_client(target_url, anon_key)
    client_b.postgrest.auth(token_b)
    repo_b = CRMRepository(client=client_b)

    assert user_a_id != user_b_id, "User A and User B must be distinct test accounts"

    return {
        "user_a": {"id": user_a_id, "client": client_a, "repo": repo_a, "access_token": token_a},
        "user_b": {"id": user_b_id, "client": client_b, "repo": repo_b, "access_token": token_b},
    }


def test_live_crm_crud_and_isolation(auth_context):
    """Test CRM creation, reading, and database-level RLS cross-tenant isolation."""
    user_a = auth_context["user_a"]
    user_b = auth_context["user_b"]
    repo_a: CRMRepository = user_a["repo"]
    repo_b: CRMRepository = user_b["repo"]
    client_b: Client = user_b["client"]

    unique_suffix = uuid.uuid4().hex[:8]
    config = {
        "schema_version": "1.0",
        "name": f"Live CRM {unique_suffix}",
        "industry": "technology",
        "entities": [
            {
                "name": "lead",
                "label": "Lead",
                "fields": [
                    {"name": "full_name", "label": "Full Name", "type": "text", "required": True},
                    {"name": "email", "label": "Email", "type": "email", "required": False},
                ],
            }
        ],
        "pipeline_stages": ["New", "Won"],
    }

    # 1. User A creates CRM via user-scoped client
    crm_a = repo_a.create_crm(
        owner_id=user_a["id"],
        name=f"User A CRM {unique_suffix}",
        configuration=config,
    )
    assert crm_a["id"]
    crm_id = crm_a["id"]

    try:
        # 2. User A can retrieve their own CRM
        retrieved_a = repo_a.get_crm(crm_id, owner_id=user_a["id"])
        assert retrieved_a is not None
        assert retrieved_a["id"] == crm_id

        # 3. User B CANNOT retrieve User A's CRM (Enforced by PostgreSQL RLS)
        retrieved_b = repo_b.get_crm(crm_id, owner_id=user_b["id"])
        assert retrieved_b is None

        # 4. User B cannot list User A's CRM
        crms_b, total_b = repo_b.list_crms(owner_id=user_b["id"])
        assert all(c["id"] != crm_id for c in crms_b)

        # 5. User B CANNOT mutate User A's CRM (Enforced by RLS)
        update_attempt = client_b.table("crms").update({"name": "Hacked"}).eq("id", crm_id).execute()
        assert len(update_attempt.data) == 0

        delete_attempt = client_b.table("crms").delete().eq("id", crm_id).execute()
        assert len(delete_attempt.data) == 0

    finally:
        # Strict scoped cleanup: only the created CRM ID is deleted
        user_a["client"].table("crms").delete().eq("id", crm_id).execute()


def test_live_records_crud_and_soft_delete(auth_context):
    """Test record creation, listing, updating, cross-tenant isolation, and soft deletion."""
    user_a = auth_context["user_a"]
    user_b = auth_context["user_b"]
    repo_a: CRMRepository = user_a["repo"]
    repo_b: CRMRepository = user_b["repo"]

    unique_suffix = uuid.uuid4().hex[:8]
    crm = repo_a.create_crm(
        owner_id=user_a["id"],
        name=f"Records CRM {unique_suffix}",
        configuration={"schema_version": "1.0", "name": "CRM", "industry": "tech", "entities": []},
    )
    crm_id = crm["id"]

    try:
        # 1. Create record via User A
        rec = repo_a.create_record(
            crm_id=crm_id,
            entity_name="lead",
            data={"full_name": "Alice Smith", "email": "alice@test.com"},
            source="Test Suite",
            owner_id=user_a["id"],
        )
        assert rec["id"]
        rec_id = rec["id"]
        assert rec["owner_id"] == user_a["id"]

        # 2. List records via User A
        records, total = repo_a.list_records(crm_id=crm_id, entity_name="lead")
        assert total >= 1
        assert any(r["id"] == rec_id for r in records)

        # 3. User B cannot access User A's record (Enforced by PostgreSQL RLS)
        rec_b = repo_b.get_record(rec_id, crm_id, "lead")
        assert rec_b is None

        # User B direct table queries are blocked by RLS
        direct_select_b = user_b["client"].table("records").select("*").eq("id", rec_id).execute()
        assert len(direct_select_b.data) == 0

        # User B cannot update or delete User A's record
        direct_update_b = user_b["client"].table("records").update({"data": {"tampered": True}}).eq("id", rec_id).execute()
        assert len(direct_update_b.data) == 0

        direct_delete_b = user_b["client"].table("records").delete().eq("id", rec_id).execute()
        assert len(direct_delete_b.data) == 0

        # 4. User A updates record
        updated = repo_a.update_record(
            rec_id, crm_id, {"full_name": "Alice Johnson", "email": "alice@test.com"}
        )
        assert updated is not None
        assert updated["data"]["full_name"] == "Alice Johnson"

        # 5. User A soft-deletes record
        deleted = repo_a.soft_delete_record(rec_id, crm_id, "lead")
        assert deleted is True

        # 6. Ensure soft-deleted record is excluded from active queries
        active_rec = repo_a.get_record(rec_id, crm_id, "lead")
        assert active_rec is None

        # Ensure list query excludes soft-deleted record
        records_after, total_after = repo_a.list_records(crm_id, "lead")
        assert not any(r["id"] == rec_id for r in records_after)

    finally:
        # Scoped cleanup
        user_a["client"].table("crms").delete().eq("id", crm_id).execute()


def test_live_evolution_proposal_two_stage_lifecycle(auth_context):
    """Test approval and atomic application of an evolution proposal via database procedures."""
    user_a = auth_context["user_a"]
    user_b = auth_context["user_b"]
    repo_a: CRMRepository = user_a["repo"]
    repo_b: CRMRepository = user_b["repo"]

    unique_suffix = uuid.uuid4().hex[:8]
    crm = repo_a.create_crm(
        owner_id=user_a["id"],
        name=f"Evolution CRM {unique_suffix}",
        configuration={"schema_version": "1.0", "name": "CRM", "industry": "tech", "entities": []},
    )
    crm_id = crm["id"]
    initial_version = crm["configuration_version"]

    try:
        # 1. Create a baseline workflow
        wf = repo_a.create_workflow(
            crm_id=crm_id,
            name="Follow-up Workflow",
            trigger={"type": "record.created", "entity": "lead"},
            actions=[{"type": "create_follow_up_task", "delay_hours": 48}],
            status="draft",
        )
        wf_id = wf["id"]

        # 2. Create evolution proposal targeting the workflow
        proposal = repo_a.create_proposal(
            crm_id=crm_id,
            summary="Reduce delay to 12 hours",
            proposed_change={
                "type": "workflow.update",
                "workflow_id": wf_id,
                "delay_hours": 12,
            },
            evidence=[{"metric": "conversion", "value": 0.15}],
            config_version=initial_version,
        )
        prop_id = proposal["id"]
        assert proposal["status"] == "pending"

        # 3. Cross-tenant protection: User B CANNOT approve User A's proposal
        unauthorized_approval = repo_b.approve_proposal(prop_id, actor_id=user_b["id"])
        assert unauthorized_approval is not None
        assert unauthorized_approval.get("error") == "not_owner"

        # 4. Stage 1: User A approves proposal via stored procedure
        approval_response = api_client.post(
            f"/api/v1/crm/{crm_id}/evolution/proposals/{prop_id}/approve",
            headers={"Authorization": f"Bearer {user_a['access_token']}"},
        )
        assert approval_response.status_code == 200, approval_response.text
        approved = approval_response.json()["data"]
        assert approved["status"] == "approved"
        assert approved["approved_by"] == user_a["id"]

        # 5. Stage 2: User A applies proposal via atomic stored procedure
        response = api_client.post(
            f"/api/v1/crm/{crm_id}/evolution/proposals/{prop_id}/apply",
            headers={"Authorization": f"Bearer {user_a['access_token']}"},
        )
        assert response.status_code == 200, response.text
        applied = response.json()["data"]
        assert applied["status"] == "applied"
        assert applied["applied_by"] == user_a["id"]

        # 6. Verify workflow action was updated by the database procedure
        updated_wf = repo_a.get_workflow(wf_id, crm_id)
        assert updated_wf is not None
        assert updated_wf["actions"][0]["delay_hours"] == 12

        # 7. Verify CRM configuration version was monotonically incremented
        updated_crm = repo_a.get_crm(crm_id, owner_id=user_a["id"])
        assert updated_crm is not None
        assert updated_crm["configuration_version"] == initial_version + 1

        # 8. Verify audit log entries were recorded for both stages
        audit_approve = (
            user_a["client"]
            .table("audit_logs")
            .select("*")
            .eq("entity_id", prop_id)
            .eq("action", "approve")
            .execute()
        )
        assert len(audit_approve.data) >= 1
        assert audit_approve.data[0]["action"] == "approve"
        assert audit_approve.data[0]["entity_type"] == "evolution_proposal"

        audit_apply = (
            user_a["client"]
            .table("audit_logs")
            .select("*")
            .eq("entity_id", prop_id)
            .eq("action", "apply")
            .execute()
        )
        assert len(audit_apply.data) >= 1
        assert audit_apply.data[0]["action"] == "apply"
        assert audit_apply.data[0]["entity_type"] == "evolution_proposal"

    finally:
        # Scoped cleanup
        user_a["client"].table("crms").delete().eq("id", crm_id).execute()


def test_live_evolution_proposal_workflow_create_lifecycle(auth_context):
    """Test approval and atomic application of a workflow.create evolution proposal via database procedure."""
    user_a = auth_context["user_a"]
    user_b = auth_context["user_b"]
    repo_a: CRMRepository = user_a["repo"]
    repo_b: CRMRepository = user_b["repo"]

    unique_suffix = uuid.uuid4().hex[:8]
    crm = repo_a.create_crm(
        owner_id=user_a["id"],
        name=f"Workflow Create CRM {unique_suffix}",
        configuration={"schema_version": "1.0", "name": "CRM", "industry": "tech", "entities": []},
    )
    crm_id = crm["id"]
    initial_version = crm["configuration_version"]

    try:
        # 1. Create evolution proposal with type workflow.create
        proposal = repo_a.create_proposal(
            crm_id=crm_id,
            summary="Add new follow-up workflow",
            proposed_change={
                "type": "workflow.create",
                "name": f"Automated Lead Follow-up {unique_suffix}",
                "trigger": {"type": "record.created", "entity": "lead"},
                "actions": [{"type": "create_follow_up_task", "delay_hours": 24}],
            },
            evidence=[{"metric": "lead_response_time", "value": 24}],
            config_version=initial_version,
        )
        prop_id = proposal["id"]
        assert proposal["status"] == "pending"

        # 2. Cross-tenant protection: User B CANNOT approve User A's proposal
        unauthorized = repo_b.approve_proposal(prop_id, actor_id=user_b["id"])
        assert unauthorized is not None
        assert unauthorized.get("error") == "not_owner"

        # 3. Stage 1: User A approves proposal via stored procedure
        approval_response = api_client.post(
            f"/api/v1/crm/{crm_id}/evolution/proposals/{prop_id}/approve",
            headers={"Authorization": f"Bearer {user_a['access_token']}"},
        )
        assert approval_response.status_code == 200, approval_response.text
        approved = approval_response.json()["data"]
        assert approved["status"] == "approved"
        assert approved["approved_by"] == user_a["id"]

        # 4. Stage 2: User A applies proposal via atomic stored procedure
        response = api_client.post(
            f"/api/v1/crm/{crm_id}/evolution/proposals/{prop_id}/apply",
            headers={"Authorization": f"Bearer {user_a['access_token']}"},
        )
        assert response.status_code == 200, response.text
        applied = response.json()["data"]
        assert applied["status"] == "applied"
        assert applied["applied_by"] == user_a["id"]

        # 5. Verify exactly one new workflow was created in draft status
        workflows = repo_a.list_workflows(crm_id)
        matching_wf = [w for w in workflows if w["name"] == f"Automated Lead Follow-up {unique_suffix}"]
        assert len(matching_wf) == 1
        new_wf = matching_wf[0]
        assert new_wf["status"] == "draft"
        assert new_wf["actions"][0]["delay_hours"] == 24

        # 6. Verify CRM configuration version was monotonically incremented
        updated_crm = repo_a.get_crm(crm_id, owner_id=user_a["id"])
        assert updated_crm is not None
        assert updated_crm["configuration_version"] == initial_version + 1

        # 7. Verify audit log entry was recorded by the database procedure
        audit_apply = (
            user_a["client"]
            .table("audit_logs")
            .select("*")
            .eq("entity_id", prop_id)
            .eq("action", "apply")
            .execute()
        )
        assert len(audit_apply.data) >= 1
        assert audit_apply.data[0]["action"] == "apply"
        assert audit_apply.data[0]["entity_type"] == "evolution_proposal"

    finally:
        # Scoped cleanup
        user_a["client"].table("crms").delete().eq("id", crm_id).execute()

def test_live_approved_stale_proposal_clears_approval_without_mutations(auth_context):
    """A stale apply clears approval metadata without changing workflows or version again."""
    user_a = auth_context["user_a"]
    repo_a: CRMRepository = user_a["repo"]
    suffix = uuid.uuid4().hex[:8]
    crm = repo_a.create_crm(
        owner_id=user_a["id"],
        name=f"Stale Apply CRM {suffix}",
        configuration={"schema_version": "1.0", "name": "CRM", "industry": "tech", "entities": []},
    )
    crm_id = crm["id"]
    initial_version = crm["configuration_version"]
    workflow = repo_a.create_workflow(
        crm_id=crm_id,
        name=f"Stale Workflow {suffix}",
        trigger={"type": "record.created", "entity": "lead"},
        actions=[{"type": "create_follow_up_task", "delay_hours": 48}],
        status="draft",
    )
    proposal = repo_a.create_proposal(
        crm_id=crm_id,
        summary="Update a workflow that will become stale",
        proposed_change={"type": "workflow.update", "workflow_id": workflow["id"], "delay_hours": 12},
        evidence=[],
        config_version=initial_version,
    )
    try:
        approval = api_client.post(
            f"/api/v1/crm/{crm_id}/evolution/proposals/{proposal['id']}/approve",
            headers={"Authorization": f"Bearer {user_a['access_token']}"},
        )
        assert approval.status_code == 200, approval.text
        user_a["client"].table("crms").update(
            {"configuration_version": initial_version + 1}
        ).eq("id", crm_id).execute()

        response = api_client.post(
            f"/api/v1/crm/{crm_id}/evolution/proposals/{proposal['id']}/apply",
            headers={"Authorization": f"Bearer {user_a['access_token']}"},
        )
        assert response.status_code == 409, response.text
        saved = repo_a.get_proposal(proposal["id"], crm_id)
        assert saved["status"] == "stale"
        assert saved["approved_at"] is None
        assert saved["approved_by"] is None
        saved_crm = repo_a.get_crm(crm_id, owner_id=user_a["id"])
        assert saved_crm["configuration_version"] == initial_version + 1
        saved_workflow = repo_a.get_workflow(workflow["id"], crm_id)
        assert saved_workflow["actions"][0]["delay_hours"] == 48
        apply_audit = user_a["client"].table("audit_logs").select("id").eq(
            "entity_id", proposal["id"]
        ).eq("action", "apply").execute()
        assert not apply_audit.data
    finally:
        user_a["client"].table("crms").delete().eq("id", crm_id).execute()


def test_live_failed_apply_rolls_back_and_clears_approval_metadata(auth_context):
    """An integer overflow after workflow creation rolls the subtransaction back."""
    user_a = auth_context["user_a"]
    repo_a: CRMRepository = user_a["repo"]
    suffix = uuid.uuid4().hex[:8]
    crm = repo_a.create_crm(
        owner_id=user_a["id"],
        name=f"Failed Apply CRM {suffix}",
        configuration={"schema_version": "1.0", "name": "CRM", "industry": "tech", "entities": []},
    )
    crm_id = crm["id"]
    max_version = 2147483647
    user_a["client"].table("crms").update(
        {"configuration_version": max_version}
    ).eq("id", crm_id).execute()
    workflow_name = f"Rolled Back Workflow {suffix}"
    proposal = repo_a.create_proposal(
        crm_id=crm_id,
        summary="Create a workflow that fails during version increment",
        proposed_change={
            "type": "workflow.create",
            "name": workflow_name,
            "trigger": {"type": "record.created", "entity": "lead"},
            "actions": [{"type": "create_follow_up_task", "delay_hours": 24}],
        },
        evidence=[],
        config_version=max_version,
    )
    try:
        approval = api_client.post(
            f"/api/v1/crm/{crm_id}/evolution/proposals/{proposal['id']}/approve",
            headers={"Authorization": f"Bearer {user_a['access_token']}"},
        )
        assert approval.status_code == 200, approval.text
        response = api_client.post(
            f"/api/v1/crm/{crm_id}/evolution/proposals/{proposal['id']}/apply",
            headers={"Authorization": f"Bearer {user_a['access_token']}"},
        )
        assert response.status_code == 500, response.text
        saved = repo_a.get_proposal(proposal["id"], crm_id)
        assert saved["status"] == "failed"
        assert saved["approved_at"] is None
        assert saved["approved_by"] is None
        assert saved["application_error"]
        saved_crm = repo_a.get_crm(crm_id, owner_id=user_a["id"])
        assert saved_crm["configuration_version"] == max_version
        workflows = repo_a.list_workflows(crm_id)
        assert all(w["name"] != workflow_name for w in workflows)
        apply_audit = user_a["client"].table("audit_logs").select("id").eq(
            "entity_id", proposal["id"]
        ).eq("action", "apply").execute()
        assert not apply_audit.data
    finally:
        user_a["client"].table("crms").delete().eq("id", crm_id).execute()
