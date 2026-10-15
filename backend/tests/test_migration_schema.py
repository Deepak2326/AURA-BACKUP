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


# ── Migration 0003 tests ────────────────────────────────────────────


MIGRATION_0003_PATH = Path(__file__).resolve().parent.parent.parent / "supabase" / "migrations" / "0003_fix_apply_evolution_proposal_workflow_create.sql"


@pytest.fixture(scope="module")
def sql_0003():
    if not MIGRATION_0003_PATH.exists():
        pytest.skip(f"Migration 0003 not found at {MIGRATION_0003_PATH}")
    return MIGRATION_0003_PATH.read_text(encoding="utf-8")


def test_migration_0003_file_exists_and_non_empty(sql_0003):
    assert len(sql_0003) > 1000, "Migration 0003 should be non-trivial"


def test_migration_0003_replaces_apply_function(sql_0003):
    """0003 must CREATE OR REPLACE the apply_evolution_proposal function."""
    assert "CREATE OR REPLACE FUNCTION public.apply_evolution_proposal(" in sql_0003


def test_migration_0003_function_returns_text(sql_0003):
    """The function must return TEXT, consistent with migration 0001."""
    assert "RETURNS TEXT" in sql_0003


def test_migration_0003_security_definer_with_search_path(sql_0003):
    """SECURITY DEFINER must have a safe search_path to prevent hijacking."""
    assert "SECURITY DEFINER" in sql_0003
    assert "SET search_path = pg_catalog, public, pg_temp" in sql_0003


def test_migration_0003_no_anonymous_composite_returning(sql_0003):
    """The fix must eliminate RETURNING ... to_jsonb(workflows) patterns that cause error 0A000.

    The specific anti-patterns are:
    1. RETURNING id, to_jsonb(workflows) — multi-column RETURNING with table-name to_jsonb
    2. RETURNING to_jsonb(workflows)    — single-column RETURNING using table name as row ref
    """
    # Strip comments to check only executable SQL
    executable_lines = "\n".join(
        line for line in sql_0003.splitlines()
        if not line.lstrip().startswith("--")
    )
    # The broken patterns used 'to_jsonb(workflows)' in a RETURNING clause
    returning_blocks = re.findall(r"RETURNING\b.*?;", executable_lines, re.DOTALL | re.IGNORECASE)
    for block in returning_blocks:
        assert "to_jsonb(workflows)" not in block, (
            f"RETURNING clause still contains to_jsonb(workflows) — "
            f"this causes the anonymous composite type error 0A000: {block[:120]}"
        )


def test_migration_0003_workflow_create_returns_id_only(sql_0003):
    """workflow.create path must use RETURNING id INTO (not multi-column RETURNING)."""
    # The fixed pattern should have RETURNING id INTO workflow_uuid
    assert re.search(r"RETURNING\s+id\s+INTO\s+workflow_uuid", sql_0003, re.IGNORECASE), (
        "workflow.create should use RETURNING id INTO workflow_uuid"
    )


def test_migration_0003_workflow_create_builds_jsonb_manually(sql_0003):
    """workflow.create must build workflow_new via jsonb_build_object, not RETURNING to_jsonb."""
    assert "jsonb_build_object(" in sql_0003, (
        "workflow.create should construct workflow_new using jsonb_build_object"
    )


def test_migration_0003_workflow_update_uses_separate_select(sql_0003):
    """workflow.update must fetch updated row via separate SELECT to_jsonb(w),
    not via RETURNING to_jsonb(workflows)."""
    # The fix should have a SELECT to_jsonb(w) FROM public.workflows w WHERE w.id = workflow_uuid
    assert re.search(r"SELECT\s+to_jsonb\(w\)\s+INTO\s+workflow_new", sql_0003, re.IGNORECASE), (
        "workflow.update should fetch updated workflow via SELECT to_jsonb(w) INTO workflow_new"
    )


def test_migration_0003_locks_proposal_and_crm(sql_0003):
    """Both proposal and CRM rows must be locked with FOR UPDATE."""
    for_update_count = sql_0003.count("FOR UPDATE")
    assert for_update_count >= 2, (
        f"Expected at least 2 FOR UPDATE clauses (proposal + CRM), found {for_update_count}"
    )


def test_migration_0003_validates_auth_uid(sql_0003):
    """The function must require auth.uid() to be non-NULL."""
    assert "auth.uid()" in sql_0003
    assert "'unauthenticated'" in sql_0003


def test_migration_0003_validates_proposal_status(sql_0003):
    """The function must check proposal status is 'approved' before applying."""
    assert "'not_approved'" in sql_0003


def test_migration_0003_validates_ownership(sql_0003):
    """The function must verify CRM ownership."""
    assert "'not_owner'" in sql_0003


def test_migration_0003_staleness_check(sql_0003):
    """The function must detect stale proposals."""
    assert "'stale'" in sql_0003
    assert "configuration_version" in sql_0003


def test_migration_0003_allowlists_trigger_types(sql_0003):
    """Trigger types must be explicitly allowlisted."""
    for trigger_type in ["record.created", "record.updated", "record.stage_changed"]:
        assert trigger_type in sql_0003, f"Missing allowlisted trigger type: {trigger_type}"


def test_migration_0003_allowlists_action_types(sql_0003):
    """Action types must be explicitly allowlisted."""
    for action_type in ["create_follow_up_task", "notify_owner", "send_email"]:
        assert action_type in sql_0003, f"Missing allowlisted action type: {action_type}"


def test_migration_0003_handles_unknown_change_type(sql_0003):
    """Unknown change types must be rejected."""
    assert "'invalid_change: unknown change type'" in sql_0003


def test_migration_0003_audit_log_insertion(sql_0003):
    """Both change type paths must insert audit logs."""
    audit_insert_count = sql_0003.count("INSERT INTO public.audit_logs")
    assert audit_insert_count >= 2, (
        f"Expected at least 2 audit log insertions (workflow.update + workflow.create), found {audit_insert_count}"
    )


def test_migration_0003_exception_handler_marks_failed(sql_0003):
    """Exception handler must mark proposal as 'failed' with SQLERRM."""
    assert "SQLERRM" in sql_0003
    assert "'failed'" in sql_0003
    assert "'apply_failed'" in sql_0003


def test_migration_0003_workflow_create_validates_delay_hours_numeric_and_range(sql_0003):
    """workflow.create must validate delay_hours is an integer and in range 0-8760."""
    assert "action_delay := (workflow_actions->action_idx->>'delay_hours')::INTEGER;" in sql_0003
    assert "action_delay < 0 OR action_delay > 8760" in sql_0003


def test_migration_0003_subtransaction_rollback_guarantee(sql_0003):
    """Both workflow.update and workflow.create must use subtransactions (BEGIN...EXCEPTION)."""
    assert sql_0003.count("EXCEPTION WHEN OTHERS THEN") >= 4


# ── Migration 0004 tests ────────────────────────────────────────────


MIGRATION_0004_PATH = Path(__file__).resolve().parent.parent.parent / "supabase" / "migrations" / "0004_fix_workflow_create_record_assignment.sql"


@pytest.fixture(scope="module")
def sql_0004():
    if not MIGRATION_0004_PATH.exists():
        pytest.skip(f"Migration 0004 not found at {MIGRATION_0004_PATH}")
    return MIGRATION_0004_PATH.read_text(encoding="utf-8")


def test_migration_0004_file_exists_and_non_empty(sql_0004):
    """Migration 0004 must exist and be non-empty."""
    assert len(sql_0004) > 1000, "Migration 0004 should be non-trivial"


def test_migration_0004_no_action_rec_declaration(sql_0004):
    """0004 must completely eliminate action_rec RECORD declaration from executable SQL."""
    executable_lines = "\n".join(
        line for line in sql_0004.splitlines()
        if not line.lstrip().startswith("--")
    )
    assert "action_rec" not in executable_lines, (
        "action_rec variable should be completely removed from executable SQL in migration 0004"
    )


def test_migration_0004_no_anonymous_record_assignment(sql_0004):
    """0004 must not contain any RECORD variable assignments that cause error 0A000."""
    executable_lines = "\n".join(
        line for line in sql_0004.splitlines()
        if not line.lstrip().startswith("--")
    )
    assert "action_rec :=" not in executable_lines
    # Only workflow_new JSONB variable should be assigned via jsonb_build_object
    for line in executable_lines.splitlines():
        if ":= jsonb_build_object(" in line:
            assert "workflow_new :=" in line, f"Unexpected jsonb_build_object assignment: {line}"


def test_migration_0004_security_definer_and_search_path(sql_0004):
    """0004 must preserve SECURITY DEFINER and safe search_path."""
    assert "SECURITY DEFINER" in sql_0004
    assert "SET search_path = pg_catalog, public, pg_temp" in sql_0004


def test_migration_0004_replaces_apply_function(sql_0004):
    """0004 must CREATE OR REPLACE the apply_evolution_proposal function."""
    assert "CREATE OR REPLACE FUNCTION public.apply_evolution_proposal(" in sql_0004


def test_migration_0004_workflow_create_delay_hours_validation(sql_0004):
    """0004 must preserve delay_hours integer and range validation in workflow.create."""
    assert "action_delay := (workflow_actions->action_idx->>'delay_hours')::INTEGER;" in sql_0004
    assert "action_delay < 0 OR action_delay > 8760" in sql_0004


def test_migration_0004_subtransaction_rollback_guarantee(sql_0004):
    """0004 must preserve subtransaction exception blocks in both mutation paths."""
    assert sql_0004.count("EXCEPTION WHEN OTHERS THEN") >= 4



# Migration 0005 terminal-state consistency checks.
MIGRATION_0005_PATH = Path(__file__).resolve().parent.parent.parent / "supabase" / "migrations" / "0005_fix_evolution_proposal_terminal_state_approval_fields.sql"


@pytest.fixture(scope="module")
def sql_0005():
    if not MIGRATION_0005_PATH.exists():
        pytest.skip(f"Migration 0005 not found at {MIGRATION_0005_PATH}")
    return MIGRATION_0005_PATH.read_text(encoding="utf-8")


def test_migration_0005_replaces_apply_function_with_same_security(sql_0005):
    assert "CREATE OR REPLACE FUNCTION public.apply_evolution_proposal(" in sql_0005
    assert "RETURNS TEXT" in sql_0005
    assert "SECURITY DEFINER" in sql_0005
    assert "SET search_path = pg_catalog, public, pg_temp" in sql_0005


def test_migration_0005_stale_transition_clears_approval_metadata(sql_0005):
    assert "SET status = 'stale', approved_at = NULL, approved_by = NULL, updated_at = NOW()" in sql_0005


def test_migration_0005_failed_transitions_clear_approval_metadata(sql_0005):
    assert sql_0005.count("SET status = 'failed', approved_at = NULL, approved_by = NULL, application_error = SQLERRM, updated_at = NOW()") == 2


def test_migration_0005_preserves_both_atomic_paths(sql_0005):
    assert "WHEN 'workflow.update' THEN" in sql_0005
    assert "WHEN 'workflow.create' THEN" in sql_0005
    assert "configuration_version = configuration_version + 1" in sql_0005
    assert sql_0005.count("INSERT INTO public.audit_logs") == 2
    assert sql_0005.count("EXCEPTION WHEN OTHERS THEN") >= 4
