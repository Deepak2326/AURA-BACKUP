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

## Output requirements
- Return a `CRMConfiguration` object validated against `backend/app/schemas/crm.py`.
- Use lowercase machine identifiers for entity names and field names (e.g., `lead`, not `Lead`).
- Include both `name` (machine identifier) and `label` (human-readable) for entities and fields.
- Ensure `select` fields include a non-empty, unique `options` list.
- Ensure all required fields are marked appropriately.

## Integration constraints
- Do not write directly to Supabase. Generation is read-only from the AI provider's perspective.
- Do not generate executable SQL, code, or formulas. The output is configuration JSON only.
- Return a draft configuration that the frontend can preview. The frontend is responsible for saving via `POST /api/v1/crm`.
- Use a mocked model provider for early development and testing. The mock should return schema-valid JSON without requiring a paid API key or network access.

## Error handling
- If the model returns invalid JSON, attempt repair if safe, or return `422 VALIDATION_ERROR` with details.
- If the model provider fails (timeout, network error, rate limit), return `502 AI_PROVIDER_ERROR` with a generic message.
- Never return raw model output that hasn't been validated against the Pydantic schema.

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
