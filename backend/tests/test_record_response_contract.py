"""Regression tests for backend response contract field names.

These tests verify that:
- POST   /api/v1/crm/{crm_id}/records/{entity}             returns `id` (not `record_id`)
- GET    /api/v1/crm/{crm_id}/records/{entity}             returns items[].id (not record_id)
- PATCH  /api/v1/crm/{crm_id}/records/{entity}/{record_id} returns `id` (not `record_id`)
- DELETE /api/v1/crm/{crm_id}/records/{entity}/{record_id} returns `deleted: true`
- POST   /api/v1/crm/{crm_id}/records/{entity}/import      returns `created` (not `imported`)

All repository calls are mocked so these tests run fully offline (no Supabase required).
They exercise the real router serialisation layer -- not just the HTTP status.

Aligned with:
  - frontend/src/types/crm.ts::CRMRecord  (field `id`)
  - frontend/src/services/api.ts::CRMAdapter.importCsv  (field `created`)
  - backend/app/schemas/crm.py::CRMRecord  (field `id`)
  - backend/app/schemas/crm.py::ImportResult  (field `created`)
  - docs/API_CONTRACTS.md section 5.6 and 5.10
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from jose import jwt

from app.main import create_app
from app.config import get_settings

# ---------------------------------------------------------------------------
# App/client setup
# ---------------------------------------------------------------------------

settings = get_settings()
if not settings.supabase_jwt_secret:
    settings.supabase_jwt_secret = "test-jwt-secret-key-32-chars-minimum-length!"

app = create_app()
client = TestClient(app)

CRM_ID = str(uuid.uuid4())
RECORD_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())
NOW_ISO = datetime.now(timezone.utc).isoformat()

REPO_PATH = "app.routers.records.CRMRepository"


def _token(user_id: str = USER_ID) -> str:
    payload = {"sub": user_id, "aud": "authenticated", "exp": 9999999999}
    return jwt.encode(payload, settings.supabase_jwt_secret, algorithm="HS256")


def _headers(user_id: str = USER_ID) -> dict:
    return {"Authorization": f"Bearer {_token(user_id)}"}


def _mock_crm() -> dict:
    return {
        "id": CRM_ID,
        "owner_id": USER_ID,
        "name": "Test CRM",
        "status": "active",
        "configuration_version": 1,
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
                        {"name": "email",     "label": "Email",     "type": "email", "required": False},
                    ],
                }
            ],
            "pipeline_stages": ["New", "Won"],
        },
    }


def _mock_record(record_id: str = RECORD_ID) -> dict:
    return {
        "id": record_id,
        "crm_id": CRM_ID,
        "entity_name": "lead",
        "data": {"full_name": "Alice", "email": "alice@example.com"},
        "source": "Web UI",
        "owner_id": USER_ID,
        "created_at": NOW_ISO,
        "updated_at": NOW_ISO,
        "deleted_at": None,
    }


# ---------------------------------------------------------------------------
# POST create record
# ---------------------------------------------------------------------------

def test_create_record_response_uses_id_not_record_id():
    """POST create record must return `id`, not `record_id`, at data level."""
    mock_repo = MagicMock()
    mock_repo.get_crm.return_value = _mock_crm()
    mock_repo.create_record.return_value = _mock_record()

    with patch(REPO_PATH, return_value=mock_repo):
        resp = client.post(
            f"/api/v1/crm/{CRM_ID}/records/lead",
            headers=_headers(),
            json={"data": {"full_name": "Alice", "email": "alice@example.com"}},
        )

    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]

    assert "id" in data, f"Expected 'id' in response data, got keys: {list(data.keys())}"
    assert "record_id" not in data, "'record_id' must not appear in create record response"
    assert data["id"] == RECORD_ID
    assert data["crm_id"] == CRM_ID
    assert data["entity_name"] == "lead"
    assert "data" in data
    assert "created_at" in data
    assert "updated_at" in data


# ---------------------------------------------------------------------------
# GET list records
# ---------------------------------------------------------------------------

def test_list_records_response_items_use_id_not_record_id():
    """GET list records must return items[].id, not items[].record_id."""
    mock_repo = MagicMock()
    mock_repo.get_crm.return_value = _mock_crm()
    mock_repo.list_records.return_value = ([_mock_record()], 1)

    with patch(REPO_PATH, return_value=mock_repo):
        resp = client.get(
            f"/api/v1/crm/{CRM_ID}/records/lead",
            headers=_headers(),
        )

    assert resp.status_code == 200, resp.text
    outer = resp.json()["data"]

    assert "items" in outer
    assert len(outer["items"]) == 1
    item = outer["items"][0]

    assert "id" in item, f"Expected 'id' in list item, got keys: {list(item.keys())}"
    assert "record_id" not in item, "'record_id' must not appear in list records response"
    assert item["id"] == RECORD_ID
    assert outer["total"] == 1


# ---------------------------------------------------------------------------
# PATCH update record
# ---------------------------------------------------------------------------

def test_update_record_response_uses_id_not_record_id():
    """PATCH update record must return `id`, not `record_id`, at data level."""
    updated = _mock_record()
    updated["data"] = {"full_name": "Alice Updated", "email": "alice@example.com"}

    mock_repo = MagicMock()
    mock_repo.get_crm.return_value = _mock_crm()
    mock_repo.get_record.return_value = _mock_record()
    mock_repo.update_record.return_value = updated

    with patch(REPO_PATH, return_value=mock_repo):
        resp = client.patch(
            f"/api/v1/crm/{CRM_ID}/records/lead/{RECORD_ID}",
            headers=_headers(),
            json={"data": {"full_name": "Alice Updated"}},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert "id" in data, f"Expected 'id' in response data, got keys: {list(data.keys())}"
    assert "record_id" not in data, "'record_id' must not appear in update record response"
    assert data["id"] == RECORD_ID
    assert data["data"]["full_name"] == "Alice Updated"


# ---------------------------------------------------------------------------
# DELETE soft-delete record
# ---------------------------------------------------------------------------

def test_delete_record_response_shape():
    """DELETE soft-delete must return data.deleted=true and HTTP 200."""
    mock_repo = MagicMock()
    mock_repo.get_crm.return_value = _mock_crm()
    mock_repo.soft_delete_record.return_value = True

    with patch(REPO_PATH, return_value=mock_repo):
        resp = client.delete(
            f"/api/v1/crm/{CRM_ID}/records/lead/{RECORD_ID}",
            headers=_headers(),
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data.get("deleted") is True


# ---------------------------------------------------------------------------
# POST CSV import
# ---------------------------------------------------------------------------

def test_csv_import_response_uses_created_not_imported():
    """POST CSV import must return `created` count, not `imported`."""
    mock_repo = MagicMock()
    mock_repo.get_crm.return_value = _mock_crm()
    mock_repo.create_records_batch.return_value = [_mock_record(), _mock_record()]

    csv_content = "full_name,email\nAlice,alice@example.com\nBob,bob@example.com\n"
    csv_bytes = csv_content.encode("utf-8")

    with patch(REPO_PATH, return_value=mock_repo):
        resp = client.post(
            f"/api/v1/crm/{CRM_ID}/records/lead/import",
            headers=_headers(),
            files={"file": ("leads.csv", io.BytesIO(csv_bytes), "text/csv")},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert "created" in data, f"Expected 'created' in import response, got keys: {list(data.keys())}"
    assert "imported" not in data, "'imported' must not appear in import response"
    assert "skipped" in data
    assert "errors" in data
    assert data["created"] == 2
    assert data["skipped"] == 0
    assert data["errors"] == []


def test_csv_import_unknown_columns_rejected():
    """CSV with a column not in entity schema must return 422 VALIDATION_ERROR."""
    mock_repo = MagicMock()
    mock_repo.get_crm.return_value = _mock_crm()

    csv_content = "full_name,email,unknown_col\nAlice,alice@example.com,extra\n"
    csv_bytes = csv_content.encode("utf-8")

    with patch(REPO_PATH, return_value=mock_repo):
        resp = client.post(
            f"/api/v1/crm/{CRM_ID}/records/lead/import",
            headers=_headers(),
            files={"file": ("leads.csv", io.BytesIO(csv_bytes), "text/csv")},
        )

    assert resp.status_code == 422, resp.text
    err = resp.json()
    assert "error" in err
    assert err["error"]["code"] == "VALIDATION_ERROR"


def test_csv_import_empty_file_rejected():
    """An empty CSV file must return 422, not 200 with created=0."""
    mock_repo = MagicMock()
    mock_repo.get_crm.return_value = _mock_crm()

    with patch(REPO_PATH, return_value=mock_repo):
        resp = client.post(
            f"/api/v1/crm/{CRM_ID}/records/lead/import",
            headers=_headers(),
            files={"file": ("empty.csv", io.BytesIO(b""), "text/csv")},
        )

    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Schema-level regression (Pydantic field inspection)
# ---------------------------------------------------------------------------

def test_crm_record_schema_field_is_id():
    """CRMRecord Pydantic schema must have an `id` field, not `record_id`."""
    from app.schemas.crm import CRMRecord
    field_names = set(CRMRecord.model_fields.keys())
    assert "id" in field_names, f"CRMRecord must have 'id' field; got: {field_names}"
    assert "record_id" not in field_names, "CRMRecord must not have 'record_id' field"


def test_import_result_schema_field_is_created():
    """ImportResult Pydantic schema must have a `created` field, not `imported`."""
    from app.schemas.crm import ImportResult
    field_names = set(ImportResult.model_fields.keys())
    assert "created" in field_names, f"ImportResult must have 'created' field; got: {field_names}"
    assert "imported" not in field_names, "ImportResult must not have 'imported' field"
