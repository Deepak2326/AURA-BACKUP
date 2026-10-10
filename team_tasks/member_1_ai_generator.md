# Member 1 — AI Brain & CRM Generator

## Goal
Implement `POST /api/v1/crm/generate` as a draft-generation service using the shared `CRMConfiguration` Pydantic model.

## Read first
- `AGENTS.md`
- `docs/API_CONTRACTS.md`
- `backend/app/schemas/crm.py`

## Scope
- Create a provider interface that can use a local Hugging Face/Ollama model or Grok API.
- Accept `GenerateCRMRequest`; return `GenerateCRMData`.
- Ask the model for JSON matching the shared schema.
- Parse and validate output with Pydantic.
- Retry or return a controlled validation/provider error; never return unvalidated model output.
- Keep generation separate from persistence. This endpoint returns a draft and must not save or activate the CRM.
- Add unit tests using a mocked provider; tests must not require a paid API key.

## Do not
- Generate or execute SQL.
- Write directly to the database.
- change shared schema/API contracts without team approval.
- Put secrets in source code or frontend variables.

## Acceptance criteria
- Valid prompt returns a schema-valid draft configuration.
- Invalid model output is rejected or safely retried.
- Provider errors become the documented error envelope.
- Tests pass and setup instructions are documented.
