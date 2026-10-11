# Member 3 — Frontend & Dynamic CRM UI

## Goal
Build a React UI that can render an approved CRM configuration and manage records through the documented API.

## Read first
- `AGENTS.md`
- `docs/API_CONTRACTS.md`
- `docs/CRM_SCHEMA.json`

## Scope
- Set up React + Vite + TypeScript + Tailwind CSS.
- Build the prompt screen for CRM generation.
- Show the generated configuration as a preview before save.
- Render dynamic tables/forms based on entity/field metadata.
- Build lead list, record create/edit views, pipeline and analytics panels.
- Use the documented API paths and success/error envelope.
- Use mock JSON and a mock API adapter until backend endpoints are ready.
- Add loading, empty, validation-error and API-error states.

## Dynamic rendering requirements
- Render forms and tables dynamically from CRM configuration JSON. Do not hardcode fields for a specific CRM.
- Use entity `name` (machine identifier) for API paths: `/api/v1/crm/{crm_id}/records/{entity_name}`.
- Use entity `label` (human-readable) for UI headers and labels.
- Use field `name` for data keys; use field `label` for form labels.
- Handle all field types defined in `docs/API_CONTRACTS.md`: text, textarea, email, number, date, boolean, select, phone.

## Authentication integration
- Use Supabase Auth for user authentication (sign up, sign in, sign out).
- Store the Supabase access token in a secure client-side store (e.g., memory, httpOnly cookie if configured).
- Include the token in all protected API requests: `Authorization: Bearer <token>`.
- Never expose Supabase service-role credentials or privileged keys in frontend code or environment variables.

## Mock adapter pattern
- Create a mock API adapter that returns schema-valid JSON responses matching `backend/app/schemas/crm.py`.
- The mock adapter should simulate network delays and error states for development.
- Switch between mock and real API via configuration (e.g., environment variable or feature flag).
- Ensure the UI works correctly with both mock and real backends.

## Do not
- hardcode a single CRM's fields as the only supported configuration.
- place model or Supabase service-role secrets in frontend code.
- invent endpoints or alter response formats locally.

## Acceptance criteria
- A sample configuration renders dynamic fields and table columns.
- The user can preview a generated configuration and save it through the API.
- Lead records can be listed and created through the API.
- UI handles API errors and loading states.
