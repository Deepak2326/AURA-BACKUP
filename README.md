# Self-Evolving CRM — Contract Pack

## Included
- `docs/API_CONTRACTS.md`: MVP API contract and implementation rules.
- `backend/app/schemas/crm.py`: Shared Pydantic v2 transport/configuration models.
- `team_tasks/`: Bounded implementation instructions for all four members.
- `AGENTS.md`: Shared coding-agent instructions.
- `.env.example`: Placeholder environment variables only.

## Suggested next steps
1. Review the contract as a team and commit it before parallel implementation.
2. Confirm Python version. The supplied models target Python 3.10+ and Pydantic v2.
3. Install `fastapi`, `uvicorn`, and `pydantic`; add tests and provider/database dependencies as needed.
4. Have Member 2 implement the response envelope, auth and persistence foundation.
5. Have each member work on their own feature branch and open pull requests.

The Pydantic models are a starting point and should be exercised by tests before production use.
