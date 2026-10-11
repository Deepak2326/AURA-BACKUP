import re
import pytest
from pathlib import Path

MIGRATION_PATH = Path(__file__).resolve().parent.parent.parent / "supabase" / "migrations" / "0001_initial_crm_schema.sql"

@pytest.fixture(scope="module")
def sql_content():
    assert MIGRATION_PATH.exists(), f"Migration file not found at {MIGRATION_PATH}"
    return MIGRATION_PATH.read_text(encoding="utf-8")

def test_migration_file_exists_and_non_empty(sql_content):
    assert len(sql_content) > 1000

def test_required_tables_created(sql_content):
    expected_tables = [
        "public.crms",
        "public.records",
        "public.workflows",
        "public.evolution_proposals",
        "public.audit_logs",
    ]
    for table in expected_tables:
        pattern = rf"CREATE TABLE {re.escape(table)}\s*\("
        assert re.search(pattern, sql_content, re.IGNORECASE), f"Missing CREATE TABLE for {table}"

def test_records_table_columns(sql_content):
    # Verify records table has owner_id, crm_id, entity_name, data, source, created_at, updated_at, deleted_at
    records_match = re.search(r"CREATE TABLE public\.records\s*\((.*?)\);", sql_content, re.DOTALL | re.IGNORECASE)
    assert records_match, "public.records table definition not found"
    cols = records_match.group(1)
    assert "crm_id UUID NOT NULL REFERENCES public.crms(id) ON DELETE CASCADE" in cols
    assert "entity_name TEXT NOT NULL" in cols
    assert "data JSONB NOT NULL" in cols
    assert "owner_id UUID" in cols
    assert "deleted_at TIMESTAMPTZ" in cols

def test_required_indexes_present(sql_content):
    expected_indexes = [
        "idx_crms_owner_id",
        "idx_records_crm_id",
        "idx_records_crm_entity",
        "idx_records_deleted_at",
        "idx_records_owner_id",
        "idx_workflows_crm_id",
        "idx_evolution_proposals_crm_id",
        "idx_audit_logs_crm_id",
        "idx_audit_logs_user_id",
        "idx_audit_logs_crm_owner_id",
    ]
    for idx in expected_indexes:
        assert idx in sql_content, f"Missing index {idx}"

def test_rls_enabled_on_all_tables(sql_content):
    tables = [
        "public.crms",
        "public.records",
        "public.workflows",
        "public.evolution_proposals",
        "public.audit_logs",
    ]
    for table in tables:
        pattern = rf"ALTER TABLE {re.escape(table)} ENABLE ROW LEVEL SECURITY;"
        assert re.search(pattern, sql_content, re.IGNORECASE), f"RLS not enabled on {table}"

def test_security_definer_functions_set_search_path(sql_content):
    # Any SECURITY DEFINER function must have a strict search_path set to prevent search_path hijacking
    sec_definer_matches = re.finditer(r"CREATE OR REPLACE FUNCTION ([^\(]+)\(.*?\).*?SECURITY DEFINER.*?(AS \$\$|\bBEGIN\b)", sql_content, re.DOTALL | re.IGNORECASE)
    for match in sec_definer_matches:
        func_def = match.group(0)
        assert "SET search_path = pg_catalog, public, pg_temp" in func_def, f"SECURITY DEFINER function missing search_path in: {func_def[:80]}"

def test_two_stage_approval_functions_present(sql_content):
    assert "public.approve_evolution_proposal" in sql_content
    assert "public.apply_evolution_proposal" in sql_content
    assert "public.reject_evolution_proposal" in sql_content
    assert "public.mark_proposal_stale" in sql_content

def test_grants_structure(sql_content):
    # Verify authenticated gets usage, but cannot directly update workflows or evolution proposals
    assert "GRANT USAGE ON SCHEMA public TO authenticated;" in sql_content
    assert "GRANT INSERT, DELETE ON public.workflows TO authenticated;" in sql_content
    assert "GRANT INSERT, DELETE ON public.evolution_proposals TO authenticated;" in sql_content
    assert "GRANT EXECUTE ON FUNCTION public.approve_evolution_proposal(UUID, INTEGER) TO authenticated;" in sql_content
    assert "GRANT EXECUTE ON FUNCTION public.apply_evolution_proposal(UUID) TO authenticated;" in sql_content
    assert "GRANT EXECUTE ON FUNCTION public.log_audit_event(UUID, UUID, TEXT, TEXT, UUID, JSONB, JSONB, JSONB) TO service_role;" in sql_content

def test_workflow_update_restricted_by_rls(sql_content):
    # Verify RLS blocks direct UPDATE on workflows and evolution_proposals
    assert "CREATE POLICY workflows_update_restricted ON public.workflows FOR UPDATE USING (false) WITH CHECK (false);" in sql_content
    assert "CREATE POLICY evolution_proposals_update_restricted ON public.evolution_proposals FOR UPDATE USING (false) WITH CHECK (false);" in sql_content

def test_audit_logs_immutability_policies(sql_content):
    # Audit logs cannot be inserted, updated, or deleted by normal users
    assert "CREATE POLICY audit_logs_insert_restricted ON public.audit_logs FOR INSERT WITH CHECK (false);" in sql_content
    assert "CREATE POLICY audit_logs_update_restricted ON public.audit_logs FOR UPDATE USING (false);" in sql_content
    assert "CREATE POLICY audit_logs_delete_restricted ON public.audit_logs FOR DELETE USING (false);" in sql_content

def test_log_audit_event_security_hardening(sql_content):
    # Ensure log_audit_event validates CRM existence and handles caller roles
    assert "p_user_id IS DISTINCT FROM caller_user_id" in sql_content
    assert "Target CRM not found" in sql_content
    assert "caller_role" in sql_content

def test_workflow_draft_status_constraint(sql_content):
    # Verify workflows start with allowed status values
    assert "CONSTRAINT workflows_status_check CHECK (status IN ('draft', 'active', 'disabled'))" in sql_content

def test_evolution_proposal_state_constraints(sql_content):
    # Verify strict lifecycle state constraints
    assert "CONSTRAINT evolution_proposals_status_check CHECK (status IN ('pending', 'approved', 'applied', 'rejected', 'stale', 'failed'))" in sql_content
    assert "CONSTRAINT evolution_proposals_approved_consistency CHECK" in sql_content
    assert "CONSTRAINT evolution_proposals_applied_consistency CHECK" in sql_content
    assert "CONSTRAINT evolution_proposals_rejected_consistency CHECK" in sql_content
    assert "CONSTRAINT evolution_proposals_approval_rejection_exclusive CHECK" in sql_content

def test_apply_evolution_proposal_row_locking(sql_content):
    # Verify SELECT ... FOR UPDATE is used for row locking on both proposal and CRM
    assert "FROM public.evolution_proposals ep" in sql_content
    assert "FOR UPDATE" in sql_content

def test_migration_0002_fix_records_soft_delete_policy():
    migration_0002 = Path(__file__).resolve().parent.parent.parent / "supabase" / "migrations" / "0002_fix_records_soft_delete_rls.sql"
    assert migration_0002.exists(), f"Migration 0002 not found at {migration_0002}"
    content = migration_0002.read_text(encoding="utf-8")
    assert "DROP POLICY IF EXISTS records_select_isolation ON public.records;" in content
    assert "CREATE POLICY records_select_isolation ON public.records FOR SELECT" in content
    assert "crms.owner_id = auth.uid()" in content
    # RLS policy must decouple tenant isolation from soft-delete filtering.
    # Strip comment lines (-- ...) before checking so the assertion only
    # applies to executable SQL, not the explanatory header comments.
    executable_lines = "\n".join(
        line for line in content.splitlines()
        if not line.lstrip().startswith("--")
    )
    assert "deleted_at IS NULL" not in executable_lines, (
        "records_select_isolation policy body must not contain deleted_at IS NULL"
    )


