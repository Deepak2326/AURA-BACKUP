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

## Implementation milestones

Complete these in order. Each milestone should have passing tests before moving to the next.

### Milestone 1: Project setup and health check
- Initialize FastAPI application with `/api/v1` prefix.
- Implement the `SuccessEnvelope` and `ErrorEnvelope` response models from `backend/app/schemas/crm.py`.
- Add a health-check endpoint: `GET /api/v1/health` returns `{"data": {"status": "ok"}, "meta": {"request_id": "..."}}`.
- Expose OpenAPI at `/openapi.json` and interactive docs at `/docs`.
- Document local development setup (Python version, dependency installation, running the server).
- **Test**: `GET /api/v1/health` returns 200 with correct envelope.

### Milestone 2: Database migrations and schema
- Create Supabase PostgreSQL migrations for:
  - `crms` table with columns: `id`, `owner_id`, `name`, `configuration`, `configuration_version`, `status`, `created_at`, `updated_at`.
  - `records` table with columns: `id`, `crm_id`, `entity_name`, `data`, `source`, `owner_id`, `created_at`, `updated_at`, `deleted_at`.
  - Indexes: `idx_crms_owner_id`, `idx_records_crm_id`, `idx_records_crm_entity`, `idx_records_deleted_at`.
  - Foreign key: `records.crm_id` → `crms.id` with `ON DELETE CASCADE`.
- Document how to apply migrations locally and in Supabase.
- **Test**: Migrations run cleanly on a fresh database; tables exist with correct columns and indexes.

### Milestone 3: Authentication and authorization foundation
- Implement Supabase JWT verification:
  - Fetch Supabase JWKS or use configured JWT secret.
  - Verify token signature, expiration, and issuer.
  - Extract `user_id` from the token's `sub` claim.
  - Reject requests with invalid/missing tokens with `401`.
- Create a dependency that injects the authenticated `user_id` into protected routes.
- **Test**: Protected endpoint returns `401` without token; returns `200` with valid token; `user_id` matches token subject.

### Milestone 4: CRM CRUD with ownership
- Implement `POST /api/v1/crm` (create CRM):
  - Validate configuration against `CRMConfiguration` schema.
  - Derive `owner_id` from verified token, not request body.
  - Store CRM and return `201` with `CRMSummary`.
- Implement `GET /api/v1/crm` (list CRMs):
  - Filter by `owner_id = authenticated_user_id`.
  - Support pagination (`limit`, `offset`).
- Implement `GET /api/v1/crm/{crm_id}` (get CRM):
  - Return `404` if CRM doesn't exist or caller is not the owner.
- **Test**: User can create/list/get their own CRMs; cannot retrieve another user's CRM (returns `404`).

### Milestone 5: Record CRUD with validation
- Implement `POST /api/v1/crm/{crm_id}/records/{entity_name}` (create record):
  - Verify CRM ownership.
  - Retrieve entity definition from CRM configuration.
  - Validate `data` against entity fields (required, type, select options).
  - Reject unknown fields.
  - Store in `records` table with `entity_name` as lowercase machine identifier.
- Implement `GET /api/v1/crm/{crm_id}/records/{entity_name}` (list records):
  - Filter by CRM ownership and entity name.
  - Support pagination.
- Implement `PATCH /api/v1/crm/{crm_id}/records/{entity_name}/{record_id}` (update record):
  - Validate partial updates against entity definition.
  - Merge updates into existing `data`.
- Implement `DELETE /api/v1/crm/{crm_id}/records/{entity_name}/{record_id}` (soft delete):
  - Set `deleted_at` timestamp; filter out in queries.
- **Test**: Record CRUD works for CRM owner; validation rejects invalid data; another user cannot access records (returns `404`).

### Milestone 6: Row-Level Security and authorization verification
- Create RLS policies on `crms` and `records` tables (see `docs/API_CONTRACTS.md` section 1.2).
- Document whether the backend uses user-level JWTs (RLS enforced) or service-role credentials (application-enforced authorization).
- **If using service-role credentials**: Ensure every query includes an explicit `owner_id` filter.
- **Test**: Cross-customer isolation tests pass:
  - Customer A cannot read Customer B's CRM or records.
  - Customer A cannot create/update/delete Customer B's records.
  - All operations return `403` or `404` as appropriate.

### Milestone 7: Environment configuration and documentation
- Create `backend/.env.example` with documented environment variables:
  - `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_JWT_SECRET` (or JWKS URL).
  - `DATABASE_URL` for direct PostgreSQL connection.
  - `SUPABASE_SERVICE_ROLE_KEY` (if used, document security implications).
- Document local development setup in `backend/README.md`:
  - Python version, virtual environment setup.
  - Dependency installation (`pip install -r requirements.txt` or `poetry install`).
  - Running migrations.
  - Starting the development server.
  - Running tests.
- **Test**: A new developer can set up and run the backend locally using the documentation.

## Source of truth
The API contract in `docs/API_CONTRACTS.md` is the authoritative reference for endpoint paths, request/response formats, and error codes. If implementation requires a deviation, escalate to the team before changing the contract.

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
