# Member 2 — Backend, Database & Authentication

## Goal
Implement the FastAPI foundation, Supabase/PostgreSQL persistence, JWT authentication and shared REST API.

## Read first
- `AGENTS.md`
- `docs/API_CONTRACTS.md`
- `backend/app/schemas/crm.py`

## Scope
- Set up FastAPI and a consistent success/error response envelope.
- Implement Supabase JWT verification and derive the user identity from the verified token.
- Create migrations for CRM metadata, shared CRM records (JSONB), workflows, evolution proposals and audit logs.
- Implement CRM create/list/get and dynamic record create/list/update/delete.
- Scope every query by authenticated ownership/membership.
- Validate records against the saved CRM configuration before persistence.
- Expose OpenAPI at `/openapi.json` and interactive docs at `/docs`.
- Add tests for authentication, ownership, validation and missing resources.

## Do not
- Trust a user ID from the request body for authorization.
- expose Supabase service-role keys to the browser.
- allow LLM-generated SQL.
- silently change API response contracts.

## Acceptance criteria
- CRUD endpoints match `docs/API_CONTRACTS.md`.
- Unauthorized users cannot access another user's CRM or records.
- Migrations are repeatable and documented.
- Tests pass with local/mock configuration where feasible.
