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

## Do not
- hardcode a single CRM's fields as the only supported configuration.
- place model or Supabase service-role secrets in frontend code.
- invent endpoints or alter response formats locally.

## Acceptance criteria
- A sample configuration renders dynamic fields and table columns.
- The user can preview a generated configuration and save it through the API.
- Lead records can be listed and created through the API.
- UI handles API errors and loading states.
