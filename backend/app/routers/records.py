"""Records CRUD routes.

Endpoints:
- POST   /api/v1/crm/{crm_id}/records/{entity_name}           — Create record
- GET    /api/v1/crm/{crm_id}/records/{entity_name}           — List records
- PATCH  /api/v1/crm/{crm_id}/records/{entity_name}/{record_id} — Update record
- DELETE /api/v1/crm/{crm_id}/records/{entity_name}/{record_id} — Soft-delete record
- POST   /api/v1/crm/{crm_id}/records/{entity_name}/import    — CSV import
"""
from __future__ import annotations

import csv
import io
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, UploadFile, File

from app.auth import CurrentUser
from app.database import CRMRepository
from app.routers.responses import success_response, error_response
from app.schemas.crm import (
    CRMConfiguration,
    CreateRecordRequest,
    UpdateRecordRequest,
)
from app.services.validation import find_entity, validate_record_data, validate_csv_row

router = APIRouter(prefix="/crm/{crm_id}/records", tags=["Records"])

# Limits
MAX_CSV_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_CSV_ROWS = 10000


def _get_repo() -> CRMRepository:
    return CRMRepository()


def _verify_crm_access(repo: CRMRepository, crm_id: str, user_id: str) -> dict[str, Any] | None:
    """Get CRM and verify ownership. Returns None if not found/not owned."""
    return repo.get_crm(crm_id, user_id)


@router.post("/{entity_name}")
async def create_record(
    crm_id: UUID,
    entity_name: str,
    body: CreateRecordRequest,
    user_id: CurrentUser,
):
    """Create a record in the specified CRM entity."""
    repo = _get_repo()
    crm = _verify_crm_access(repo, str(crm_id), user_id)
    if not crm:
        return error_response(404, "NOT_FOUND", "CRM not found.")
    if crm["status"] != "active":
        return error_response(403, "FORBIDDEN", "CRM is archived.")

    config = CRMConfiguration(**crm["configuration"])
    entity = find_entity(config, entity_name)
    if not entity:
        return error_response(404, "NOT_FOUND", f"Entity '{entity_name}' not found in CRM configuration.")

    validated_data, errors = validate_record_data(entity, body.data)
    if errors:
        return error_response(422, "VALIDATION_ERROR", "Record validation failed.", errors)

    try:
        record = repo.create_record(
            crm_id=str(crm_id),
            entity_name=entity_name,
            data=validated_data,
            source=body.source,
            owner_id=user_id,
        )
        return success_response(
            {
                "id": record["id"],
                "crm_id": record["crm_id"],
                "entity_name": record["entity_name"],
                "data": record["data"],
                "source": record.get("source"),
                "created_at": record["created_at"],
                "updated_at": record["updated_at"],
            },
            status_code=201,
        )
    except Exception:
        return error_response(500, "INTERNAL_ERROR", "Failed to create record.")


@router.get("/{entity_name}")
async def list_records(
    crm_id: UUID,
    entity_name: str,
    user_id: CurrentUser,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """List records for a CRM entity."""
    repo = _get_repo()
    crm = _verify_crm_access(repo, str(crm_id), user_id)
    if not crm:
        return error_response(404, "NOT_FOUND", "CRM not found.")

    config = CRMConfiguration(**crm["configuration"])
    entity = find_entity(config, entity_name)
    if not entity:
        return error_response(404, "NOT_FOUND", f"Entity '{entity_name}' not found in CRM configuration.")

    try:
        items, total = repo.list_records(str(crm_id), entity_name, limit=limit, offset=offset)
        return success_response({
            "items": [
                {
                    "id": r["id"],
                    "crm_id": r["crm_id"],
                    "entity_name": r["entity_name"],
                    "data": r["data"],
                    "source": r.get("source"),
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
                for r in items
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        })
    except Exception:
        return error_response(500, "INTERNAL_ERROR", "Failed to list records.")


@router.patch("/{entity_name}/{record_id}")
async def update_record(
    crm_id: UUID,
    entity_name: str,
    record_id: UUID,
    body: UpdateRecordRequest,
    user_id: CurrentUser,
):
    """Update a record (partial merge)."""
    repo = _get_repo()
    crm = _verify_crm_access(repo, str(crm_id), user_id)
    if not crm:
        return error_response(404, "NOT_FOUND", "CRM not found.")
    if crm["status"] != "active":
        return error_response(403, "FORBIDDEN", "CRM is archived.")

    config = CRMConfiguration(**crm["configuration"])
    entity = find_entity(config, entity_name)
    if not entity:
        return error_response(404, "NOT_FOUND", f"Entity '{entity_name}' not found.")

    # Validate partial update
    validated_data, errors = validate_record_data(entity, body.data, partial=True)
    if errors:
        return error_response(422, "VALIDATION_ERROR", "Validation failed.", errors)

    # Fetch existing record
    existing = repo.get_record(str(record_id), str(crm_id), entity_name)
    if not existing:
        return error_response(404, "NOT_FOUND", "Record not found.")

    # Merge data
    merged_data = {**existing["data"], **validated_data}

    try:
        updated = repo.update_record(str(record_id), str(crm_id), merged_data)
        if not updated:
            return error_response(404, "NOT_FOUND", "Record not found.")
        return success_response({
            "id": updated["id"],
            "crm_id": updated["crm_id"],
            "entity_name": updated["entity_name"],
            "data": updated["data"],
            "source": updated.get("source"),
            "created_at": updated["created_at"],
            "updated_at": updated["updated_at"],
        })
    except Exception:
        return error_response(500, "INTERNAL_ERROR", "Failed to update record.")


@router.delete("/{entity_name}/{record_id}")
async def delete_record(
    crm_id: UUID,
    entity_name: str,
    record_id: UUID,
    user_id: CurrentUser,
):
    """Soft-delete a record."""
    repo = _get_repo()
    crm = _verify_crm_access(repo, str(crm_id), user_id)
    if not crm:
        return error_response(404, "NOT_FOUND", "CRM not found.")
    if crm["status"] != "active":
        return error_response(403, "FORBIDDEN", "CRM is archived.")

    deleted = repo.soft_delete_record(str(record_id), str(crm_id), entity_name)
    if not deleted:
        return error_response(404, "NOT_FOUND", "Record not found.")

    return success_response({"deleted": True})


@router.post("/{entity_name}/import")
async def import_csv(
    crm_id: UUID,
    entity_name: str,
    user_id: CurrentUser,
    file: UploadFile = File(...),
):
    """Import records from a CSV file into the specified entity."""
    repo = _get_repo()
    crm = _verify_crm_access(repo, str(crm_id), user_id)
    if not crm:
        return error_response(404, "NOT_FOUND", "CRM not found.")
    if crm["status"] != "active":
        return error_response(403, "FORBIDDEN", "CRM is archived.")

    config = CRMConfiguration(**crm["configuration"])
    entity = find_entity(config, entity_name)
    if not entity:
        return error_response(404, "NOT_FOUND", f"Entity '{entity_name}' not found.")

    # Read file with size limit
    try:
        content = await file.read()
    except Exception:
        return error_response(400, "BAD_REQUEST", "Failed to read uploaded file.")

    if len(content) > MAX_CSV_SIZE:
        return error_response(413, "PAYLOAD_TOO_LARGE", f"File exceeds {MAX_CSV_SIZE // (1024*1024)}MB limit.")

    if not content.strip():
        return error_response(422, "VALIDATION_ERROR", "CSV file is empty.")

    # Parse CSV
    try:
        text = content.decode("utf-8-sig")  # Handle BOM
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            return error_response(422, "VALIDATION_ERROR", "CSV has no header row.")
    except Exception:
        return error_response(422, "VALIDATION_ERROR", "Failed to parse CSV file.")

    # Validate header
    field_names = {f.name for f in entity.fields}
    csv_fields = set(reader.fieldnames or [])
    unknown_fields = csv_fields - field_names
    if unknown_fields:
        return error_response(422, "VALIDATION_ERROR", f"Unknown CSV columns: {', '.join(unknown_fields)}")

    # Process rows
    imported_count = 0
    skipped_count = 0
    all_errors: list[dict[str, Any]] = []
    valid_rows: list[dict[str, Any]] = []

    for row_num, row in enumerate(reader, start=2):  # Row 1 is header
        if row_num - 1 > MAX_CSV_ROWS:
            return error_response(413, "PAYLOAD_TOO_LARGE", f"CSV exceeds {MAX_CSV_ROWS} row limit.")

        # Remove empty keys (can happen with trailing commas)
        clean_row = {k: v for k, v in row.items() if k and k in field_names}

        coerced, row_errors = validate_csv_row(entity, clean_row, row_num)
        if row_errors:
            all_errors.extend(row_errors)
            skipped_count += 1
        elif coerced:
            valid_rows.append({"data": coerced, "source": "CSV"})

    # Batch insert valid rows
    if valid_rows:
        try:
            repo.create_records_batch(str(crm_id), entity_name, valid_rows, owner_id=user_id)
            imported_count = len(valid_rows)
        except Exception:
            return error_response(500, "INTERNAL_ERROR", "Failed to import records.")

    return success_response({
        "created": imported_count,
        "skipped": skipped_count,
        "errors": [
            {"row": e["row"], "code": e["code"], "message": e["message"]}
            for e in all_errors[:100]  # Cap error details
        ],
    })
