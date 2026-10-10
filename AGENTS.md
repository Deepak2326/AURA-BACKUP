# Self-Evolving CRM — Shared Coding-Agent Instructions

Read `docs/API_CONTRACTS.md`, `docs/CRM_SCHEMA.json`, and the relevant task file in `team_tasks/` before coding.

## Stack
- Frontend: React, Vite, TypeScript, Tailwind CSS
- Backend: Python, FastAPI, Pydantic v2
- Database/auth: Supabase PostgreSQL and Supabase Auth
- AI provider: provider interface for local Hugging Face/Ollama or Grok API
- Charts: Recharts

## Architecture
- CRM configuration is validated JSON metadata.
- Dynamic records live in a shared records table with JSONB values.
- Keep system identifiers, tenant/owner IDs and timestamps in normal columns.
- Never execute arbitrary SQL/code from the LLM.
- Validate all AI-generated configurations/actions.
- AI-generated configuration/workflow changes remain drafts until approved.
- Enforce authentication and CRM ownership on every scoped operation.
- Never expose privileged Supabase credentials in the frontend.

## Collaboration
- Implement only the assigned module.
- Do not change shared API contracts or schemas without team approval.
- Use mocks for unfinished dependencies.
- Add tests for new behavior and run them before claiming completion.
- Do not commit `.env` or secrets.
- Report changed files, commands run, test results and remaining limitations.
