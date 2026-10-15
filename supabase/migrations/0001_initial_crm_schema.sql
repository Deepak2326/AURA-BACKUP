-- ============================================================================
-- Migration: 0001_initial_crm_schema.sql
-- Purpose: Create the multi-tenant CRM foundation with Row-Level Security
-- Author: Self-Evolving CRM Team
-- Version: 2.0 (atomic transactions, version increment, explicit allowlists, strict identity)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- SECTION 1: CRM Metadata Table
-- ----------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;

CREATE TABLE public.crms (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    configuration JSONB NOT NULL,
    configuration_version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT crms_status_check CHECK (status IN ('active', 'archived')),
    CONSTRAINT crms_configuration_version_positive CHECK (configuration_version >= 1)
);

COMMENT ON TABLE public.crms IS 'CRM metadata and configuration. Archived CRMs are read-only.';
COMMENT ON COLUMN public.crms.configuration_version IS 'Monotonically increasing version for optimistic locking and staleness detection.';

CREATE INDEX idx_crms_owner_id ON public.crms(owner_id);
CREATE INDEX idx_crms_status ON public.crms(status);

-- ----------------------------------------------------------------------------
-- SECTION 2: CRM Records Table
-- ----------------------------------------------------------------------------

CREATE TABLE public.records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    crm_id UUID NOT NULL REFERENCES public.crms(id) ON DELETE CASCADE,
    entity_name TEXT NOT NULL,
    data JSONB NOT NULL,
    source TEXT,
    owner_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,

    CONSTRAINT records_entity_name_format CHECK (entity_name ~ '^[a-z][a-z0-9_]{0,62}$')
);

COMMENT ON TABLE public.records IS 'Dynamic CRM records with JSONB field values. Shared across all CRMs.';

CREATE INDEX idx_records_crm_id ON public.records(crm_id);
CREATE INDEX idx_records_crm_entity ON public.records(crm_id, entity_name);
CREATE INDEX idx_records_deleted_at ON public.records(deleted_at);
CREATE INDEX idx_records_owner_id ON public.records(owner_id);

-- ----------------------------------------------------------------------------
-- SECTION 3: Workflows Table
-- ----------------------------------------------------------------------------

CREATE TABLE public.workflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    crm_id UUID NOT NULL REFERENCES public.crms(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    trigger JSONB NOT NULL,
    actions JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT workflows_status_check CHECK (status IN ('draft', 'active', 'disabled')),
    CONSTRAINT workflows_name_not_empty CHECK (length(trim(name)) > 0)
);

COMMENT ON TABLE public.workflows IS 'Automation workflows. AI-generated workflows start as drafts.';

CREATE INDEX idx_workflows_crm_id ON public.workflows(crm_id);
CREATE INDEX idx_workflows_status ON public.workflows(status);

-- ----------------------------------------------------------------------------
-- SECTION 4: Evolution Proposals Table
-- ----------------------------------------------------------------------------

CREATE TABLE public.evolution_proposals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    crm_id UUID NOT NULL REFERENCES public.crms(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending',
    summary TEXT NOT NULL,
    proposed_change JSONB NOT NULL,
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    configuration_version_at_creation INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at TIMESTAMPTZ,
    approved_by UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    applied_at TIMESTAMPTZ,
    applied_by UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    rejected_at TIMESTAMPTZ,
    rejected_by UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    rejection_reason TEXT,
    application_error TEXT,

    CONSTRAINT evolution_proposals_status_check CHECK (status IN ('pending', 'approved', 'applied', 'rejected', 'stale', 'failed')),
    CONSTRAINT evolution_proposals_summary_not_empty CHECK (length(trim(summary)) > 0),
    CONSTRAINT evolution_proposals_configuration_version_positive CHECK (configuration_version_at_creation >= 1),
    CONSTRAINT evolution_proposals_approved_consistency CHECK (
        (status IN ('approved', 'applied') AND approved_at IS NOT NULL AND approved_by IS NOT NULL)
        OR (status NOT IN ('approved', 'applied') AND approved_at IS NULL AND approved_by IS NULL)
    ),
    CONSTRAINT evolution_proposals_applied_consistency CHECK (
        (status = 'applied' AND applied_at IS NOT NULL AND applied_by IS NOT NULL)
        OR (status != 'applied' AND applied_at IS NULL AND applied_by IS NULL)
    ),
    CONSTRAINT evolution_proposals_rejected_consistency CHECK (
        (status = 'rejected' AND rejected_at IS NOT NULL AND rejected_by IS NOT NULL AND rejection_reason IS NOT NULL AND length(trim(rejection_reason)) >= 3)
        OR (status != 'rejected' AND rejected_at IS NULL AND rejected_by IS NULL)
    ),
    CONSTRAINT evolution_proposals_failed_consistency CHECK (
        (status = 'failed' AND application_error IS NOT NULL)
        OR (status != 'failed' AND application_error IS NULL)
    ),
    CONSTRAINT evolution_proposals_approval_rejection_exclusive CHECK (
        NOT (approved_at IS NOT NULL AND rejected_at IS NOT NULL)
    )
);

COMMENT ON TABLE public.evolution_proposals IS 'AI-generated improvement proposals with two-stage approval: approve → apply. Each stage is atomic with audit logging.';
COMMENT ON COLUMN public.evolution_proposals.status IS 'pending → approved → applied, or pending → rejected/stale/failed.';
COMMENT ON COLUMN public.evolution_proposals.proposed_change IS 'Allowlisted change: {"type": "workflow.update", "workflow_id": "...", "delay_hours": 12}.';
COMMENT ON COLUMN public.evolution_proposals.configuration_version_at_creation IS 'CRM version for staleness detection.';
COMMENT ON COLUMN public.evolution_proposals.applied_by IS 'User or system that applied the approved change.';
COMMENT ON COLUMN public.evolution_proposals.application_error IS 'Error message if application failed. Status = failed.';

CREATE INDEX idx_evolution_proposals_crm_id ON public.evolution_proposals(crm_id);
CREATE INDEX idx_evolution_proposals_status ON public.evolution_proposals(status);

-- ----------------------------------------------------------------------------
-- SECTION 5: Audit Logs Table
-- ----------------------------------------------------------------------------

CREATE TABLE public.audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    crm_id UUID REFERENCES public.crms(id) ON DELETE SET NULL,
    crm_owner_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id UUID,
    old_values JSONB,
    new_values JSONB,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT audit_logs_action_not_empty CHECK (length(trim(action)) > 0),
    CONSTRAINT audit_logs_entity_type_not_empty CHECK (length(trim(entity_type)) > 0)
);

COMMENT ON TABLE public.audit_logs IS 'Immutable audit trail. CRM deletion preserves history via ON DELETE SET NULL.';

CREATE INDEX idx_audit_logs_crm_id ON public.audit_logs(crm_id);
CREATE INDEX idx_audit_logs_user_id ON public.audit_logs(user_id);
CREATE INDEX idx_audit_logs_created_at ON public.audit_logs(created_at);
CREATE INDEX idx_audit_logs_crm_owner_id ON public.audit_logs(crm_owner_id);

-- ----------------------------------------------------------------------------
-- SECTION 6: Automatic Timestamp Updates
-- ----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.update_updated_at_column()
RETURNS TRIGGER 
SECURITY INVOKER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_crms_updated_at BEFORE UPDATE ON public.crms FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();
CREATE TRIGGER update_records_updated_at BEFORE UPDATE ON public.records FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();
CREATE TRIGGER update_workflows_updated_at BEFORE UPDATE ON public.workflows FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();
CREATE TRIGGER update_evolution_proposals_updated_at BEFORE UPDATE ON public.evolution_proposals FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

-- ----------------------------------------------------------------------------
-- SECTION 7: Helper Functions
-- ----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.is_crm_active(crm_uuid UUID)
RETURNS BOOLEAN
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE crm_status TEXT;
BEGIN
    SELECT crms.status INTO crm_status FROM public.crms WHERE crms.id = crm_uuid;
    RETURN crm_status = 'active';
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION public.is_crm_active(UUID) IS 'Check if CRM is active. SECURITY DEFINER for application-layer checks.';

CREATE OR REPLACE FUNCTION public.verify_crm_ownership(crm_uuid UUID, user_uuid UUID)
RETURNS BOOLEAN
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE owner UUID;
BEGIN
    SELECT crms.owner_id INTO owner FROM public.crms WHERE crms.id = crm_uuid;
    RETURN owner = user_uuid;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION public.verify_crm_ownership(UUID, UUID) IS 'Verify CRM ownership for service-role authorization.';

CREATE OR REPLACE FUNCTION public.log_audit_event(
    p_crm_id UUID,
    p_user_id UUID,
    p_action TEXT,
    p_entity_type TEXT,
    p_entity_id UUID DEFAULT NULL,
    p_old_values JSONB DEFAULT NULL,
    p_new_values JSONB DEFAULT NULL,
    p_metadata JSONB DEFAULT '{}'::jsonb
)
RETURNS UUID
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    log_id UUID;
    owner_id UUID;
    caller_user_id UUID;
    enriched_metadata JSONB;
BEGIN
    caller_user_id := auth.uid();
    -- Authenticated users must match auth.uid() to prevent forged attribution
    IF caller_user_id IS NOT NULL AND p_user_id IS DISTINCT FROM caller_user_id THEN
        RAISE EXCEPTION 'User ID mismatch: cannot log actions for another user'
            USING HINT = 'p_user_id must match auth.uid() for authenticated users', ERRCODE = '42501';
    END IF;

    -- Verify target CRM exists
    SELECT crms.owner_id INTO owner_id FROM public.crms WHERE crms.id = p_crm_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Target CRM not found'
            USING HINT = 'p_crm_id must reference an existing CRM', ERRCODE = 'P0002';
    END IF;

    enriched_metadata := COALESCE(p_metadata, '{}'::jsonb);
    IF caller_user_id IS NULL THEN
        enriched_metadata := jsonb_set(enriched_metadata, '{caller_role}', '"service_role"', true);
    ELSE
        enriched_metadata := jsonb_set(enriched_metadata, '{caller_role}', '"authenticated"', true);
    END IF;

    INSERT INTO public.audit_logs (crm_id, crm_owner_id, user_id, action, entity_type, entity_id, old_values, new_values, metadata)
    VALUES (p_crm_id, owner_id, p_user_id, p_action, p_entity_type, p_entity_id, p_old_values, p_new_values, enriched_metadata)
    RETURNING audit_logs.id INTO log_id;
    RETURN log_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION public.log_audit_event(UUID, UUID, TEXT, TEXT, UUID, JSONB, JSONB, JSONB) IS 'Insert audit log. Validates user_id matches auth.uid(). SECURITY DEFINER.';

-- ----------------------------------------------------------------------------
-- SECTION 8: Evolution Proposal State Management (Two-Stage)
-- ----------------------------------------------------------------------------

-- STAGE 1: Approve a proposal (pending → approved)
-- Validates authorization, staleness, and prepares for application
-- Does NOT apply the change - use apply_evolution_proposal() for that
CREATE OR REPLACE FUNCTION public.approve_evolution_proposal(
    proposal_uuid UUID,
    expected_version INTEGER DEFAULT NULL
)
RETURNS TEXT
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    proposal_rec RECORD;
    actor_uuid UUID;
BEGIN
    actor_uuid := auth.uid();
    IF actor_uuid IS NULL THEN RETURN 'unauthenticated'; END IF;
    
    SELECT ep.*, crms.owner_id AS crm_owner_id, crms.status AS crm_status, crms.configuration_version AS current_version
    INTO proposal_rec
    FROM public.evolution_proposals ep
    JOIN public.crms ON crms.id = ep.crm_id
    WHERE ep.id = proposal_uuid;
    
    IF NOT FOUND THEN RETURN 'not_found'; END IF;
    IF proposal_rec.crm_status != 'active' THEN RETURN 'crm_archived'; END IF;
    IF proposal_rec.crm_owner_id != actor_uuid THEN RETURN 'not_owner'; END IF;
    IF proposal_rec.status != 'pending' THEN RETURN 'not_pending'; END IF;
    
    -- Staleness check
    IF proposal_rec.current_version != proposal_rec.configuration_version_at_creation THEN
        UPDATE public.evolution_proposals SET status = 'stale', updated_at = NOW()
        WHERE id = proposal_uuid AND status = 'pending';
        INSERT INTO public.audit_logs (crm_id, crm_owner_id, user_id, action, entity_type, entity_id, new_values, metadata)
        VALUES (proposal_rec.crm_id, actor_uuid, actor_uuid, 'stale_detected', 'evolution_proposal', proposal_uuid,
                jsonb_build_object('status', 'stale', 'reason', 'configuration_changed'),
                jsonb_build_object('source', 'database_function'));
        RETURN 'stale';
    END IF;
    
    IF expected_version IS NOT NULL AND proposal_rec.current_version != expected_version THEN
        RETURN 'version_mismatch';
    END IF;
    
    -- Validate proposed_change structure and allowlist type
    IF NOT proposal_rec.proposed_change ? 'type' THEN
        RETURN 'invalid_change_type';
    END IF;
    
    -- Only allowlisted types are permitted
    IF proposal_rec.proposed_change->>'type' NOT IN ('workflow.update', 'workflow.create') THEN
        RETURN 'invalid_change_type';
    END IF;
    
    -- Transition to approved (not applied yet)
    UPDATE public.evolution_proposals
    SET status = 'approved', approved_at = NOW(), approved_by = actor_uuid, updated_at = NOW()
    WHERE id = proposal_uuid AND status = 'pending';
    
    IF NOT FOUND THEN RETURN 'not_pending'; END IF;
    
    INSERT INTO public.audit_logs (crm_id, crm_owner_id, user_id, action, entity_type, entity_id, new_values, metadata)
    VALUES (proposal_rec.crm_id, actor_uuid, actor_uuid, 'approve', 'evolution_proposal', proposal_uuid,
            jsonb_build_object('status', 'approved', 'approved_by', actor_uuid),
            jsonb_build_object('source', 'database_function'));
    
    RETURN 'approved';
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION public.approve_evolution_proposal(UUID, INTEGER) IS 'STAGE 1: Approve a proposal (pending → approved). Validates authorization and staleness. Does NOT apply the change. Returns: approved, stale, not_pending, not_owner, not_found, unauthenticated, crm_archived, version_mismatch, invalid_change_type.';

-- STAGE 2: Apply an approved proposal (approved → applied)
-- Atomically applies the allowlisted change and updates proposal status
-- This is the ONLY function that can modify data based on proposals
-- EXECUTION IDENTITY: Authenticated users only. Service_role must use backend authorization.
-- ATOMIC: All changes (workflow mutation, version increment, proposal status, audit) in one transaction
-- CONCURRENCY: Locks both proposal and CRM rows with SELECT FOR UPDATE
CREATE OR REPLACE FUNCTION public.apply_evolution_proposal(
    proposal_uuid UUID
)
RETURNS TEXT
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    proposal_rec RECORD;
    crm_rec RECORD;
    actor_uuid UUID;
    change_type TEXT;
    workflow_uuid UUID;
    new_delay_hours INTEGER;
    workflow_old JSONB;
    workflow_new JSONB;
    workflow_name TEXT;
    workflow_trigger JSONB;
    workflow_actions JSONB;
    workflow_id_text TEXT;
    delay_hours_text TEXT;
    trigger_type TEXT;
    action_rec RECORD;
    action_idx INTEGER;
BEGIN
    -- EXECUTION IDENTITY: Require authenticated user
    actor_uuid := auth.uid();
    IF actor_uuid IS NULL THEN
        RETURN 'unauthenticated';
    END IF;
    
    -- LOCK both proposal and CRM rows, then validate all preconditions
    -- This prevents concurrent modification and ensures consistent reads
    SELECT ep.* INTO proposal_rec
    FROM public.evolution_proposals ep
    WHERE ep.id = proposal_uuid
    FOR UPDATE;
    
    IF NOT FOUND THEN RETURN 'not_found'; END IF;
    
    -- Lock and fetch CRM with all required fields
    SELECT c.id, c.owner_id, c.status, c.configuration_version INTO crm_rec
    FROM public.crms c
    WHERE c.id = proposal_rec.crm_id
    FOR UPDATE;
    
    IF NOT FOUND THEN RETURN 'crm_not_found'; END IF;
    
    -- AUTHORIZATION: Verify ownership (must be CRM owner)
    IF crm_rec.owner_id != actor_uuid THEN RETURN 'not_owner'; END IF;
    
    -- ACTIVE CHECK: CRM must be active
    IF crm_rec.status != 'active' THEN RETURN 'crm_archived'; END IF;
    
    -- STATUS CHECK: Proposal must be approved
    IF proposal_rec.status != 'approved' THEN RETURN 'not_approved'; END IF;
    
    -- STALENESS CHECK: Configuration version must match
    IF crm_rec.configuration_version != proposal_rec.configuration_version_at_creation THEN
        UPDATE public.evolution_proposals 
        SET status = 'stale', updated_at = NOW()
        WHERE id = proposal_uuid;
        RETURN 'stale';
    END IF;
    
    change_type := proposal_rec.proposed_change->>'type';
    
    -- ALLOWLIST: Only specific change types are permitted
    -- Never execute arbitrary SQL or code from proposed_change JSON
    
    CASE change_type
        WHEN 'workflow.update' THEN
            -- VALIDATE: Required fields exist
            IF NOT proposal_rec.proposed_change ? 'workflow_id' OR NOT proposal_rec.proposed_change ? 'delay_hours' THEN
                RETURN 'invalid_change: missing workflow_id or delay_hours';
            END IF;
            
            -- VALIDATE: workflow_id is a valid UUID
            workflow_id_text := proposal_rec.proposed_change->>'workflow_id';
            BEGIN
                workflow_uuid := workflow_id_text::UUID;
            EXCEPTION WHEN OTHERS THEN
                RETURN 'invalid_change: workflow_id is not a valid UUID';
            END;
            
            -- VALIDATE: delay_hours is a valid integer in range
            delay_hours_text := proposal_rec.proposed_change->>'delay_hours';
            BEGIN
                new_delay_hours := delay_hours_text::INTEGER;
            EXCEPTION WHEN OTHERS THEN
                RETURN 'invalid_change: delay_hours is not a valid integer';
            END;
            
            IF new_delay_hours < 0 OR new_delay_hours > 8760 THEN
                RETURN 'invalid_change: delay_hours out of range (0-8760)';
            END IF;
            
            -- LOCK and fetch workflow, verify belongs to same CRM
            SELECT to_jsonb(w) INTO workflow_old
            FROM public.workflows w
            WHERE w.id = workflow_uuid AND w.crm_id = proposal_rec.crm_id
            FOR UPDATE;
            
            IF workflow_old IS NULL THEN
                RETURN 'invalid_change: workflow not found or does not belong to CRM';
            END IF;
            
            -- VALIDATE: workflow has actions array
            IF workflow_old->'actions' IS NULL OR jsonb_typeof(workflow_old->'actions') != 'array' THEN
                RETURN 'invalid_change: workflow has no actions array';
            END IF;
            
            IF jsonb_array_length(workflow_old->'actions') = 0 THEN
                RETURN 'invalid_change: workflow has no actions to update';
            END IF;
            
            -- VALIDATE: target action index exists and has delay_hours field
            IF NOT (workflow_old->'actions'->0 ? 'delay_hours') THEN
                RETURN 'invalid_change: first action has no delay_hours field';
            END IF;
            
            -- ATOMIC: Update workflow, increment version, update proposal, insert audit
            -- All in one transaction - any failure rolls back everything
            BEGIN
                -- Update workflow actions
                UPDATE public.workflows
                SET actions = jsonb_set(
                    actions,
                    '{0,delay_hours}',
                    to_jsonb(new_delay_hours)
                )
                WHERE id = workflow_uuid AND crm_id = proposal_rec.crm_id
                RETURNING to_jsonb(workflows) INTO workflow_new;
                
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'Workflow update failed';
                END IF;
                
                -- Increment CRM configuration version
                UPDATE public.crms
                SET configuration_version = configuration_version + 1, updated_at = NOW()
                WHERE id = proposal_rec.crm_id;
                
                -- Update proposal status
                UPDATE public.evolution_proposals
                SET status = 'applied', applied_at = NOW(), applied_by = actor_uuid, updated_at = NOW()
                WHERE id = proposal_uuid;
                
                -- Insert audit log with both old and new workflow states
                INSERT INTO public.audit_logs (crm_id, crm_owner_id, user_id, action, entity_type, entity_id, old_values, new_values, metadata)
                VALUES (
                    proposal_rec.crm_id,
                    crm_rec.owner_id,
                    actor_uuid,
                    'apply',
                    'evolution_proposal',
                    proposal_uuid,
                    jsonb_build_object('workflow', workflow_old, 'change_type', change_type),
                    jsonb_build_object('workflow', workflow_new, 'change_type', change_type, 'workflow_id', workflow_uuid),
                    jsonb_build_object('source', 'database_function', 'proposal_id', proposal_uuid)
                );
                
            EXCEPTION WHEN OTHERS THEN
                -- Transaction will roll back automatically
                -- Record failure in separate transaction
                BEGIN
                    UPDATE public.evolution_proposals 
                    SET status = 'failed', application_error = SQLERRM, updated_at = NOW()
                    WHERE id = proposal_uuid;
                EXCEPTION WHEN OTHERS THEN
                    -- If even this fails, we've lost the error but transaction is still atomic
                    NULL;
                END;
                RETURN 'apply_failed';
            END;
            
        WHEN 'workflow.create' THEN
            -- VALIDATE: Required fields
            IF NOT proposal_rec.proposed_change ? 'name' OR NOT proposal_rec.proposed_change ? 'trigger' THEN
                RETURN 'invalid_change: missing name or trigger';
            END IF;
            
            workflow_name := proposal_rec.proposed_change->>'name';
            workflow_trigger := proposal_rec.proposed_change->'trigger';
            workflow_actions := COALESCE(proposal_rec.proposed_change->'actions', '[{"type": "create_follow_up_task", "delay_hours": 24}]'::jsonb);
            
            -- VALIDATE: name is non-empty
            IF workflow_name IS NULL OR length(trim(workflow_name)) = 0 THEN
                RETURN 'invalid_change: workflow name must be non-empty';
            END IF;
            
            -- VALIDATE: trigger has 'type' field with allowlisted value
            IF workflow_trigger IS NULL OR jsonb_typeof(workflow_trigger) != 'object' THEN
                RETURN 'invalid_change: trigger must be a JSON object';
            END IF;
            
            IF NOT workflow_trigger ? 'type' THEN
                RETURN 'invalid_change: trigger must have a type field';
            END IF;
            
            trigger_type := workflow_trigger->>'type';
            -- ALLOWLIST: trigger types
            IF trigger_type NOT IN ('record.created', 'record.updated', 'record.stage_changed') THEN
                RETURN 'invalid_change: trigger type not allowlisted';
            END IF;
            
            -- VALIDATE: actions is an array
            IF jsonb_typeof(workflow_actions) != 'array' THEN
                RETURN 'invalid_change: actions must be an array';
            END IF;
            
            IF jsonb_array_length(workflow_actions) = 0 THEN
                RETURN 'invalid_change: actions must be a non-empty array';
            END IF;
            
            -- VALIDATE: each action has allowlisted type
            FOR action_idx IN SELECT generate_series(0, jsonb_array_length(workflow_actions) - 1) LOOP
                action_rec := jsonb_build_object(
                    'idx', action_idx,
                    'action', workflow_actions->action_idx
                );
                
                IF NOT (workflow_actions->action_idx ? 'type') THEN
                    RETURN format('invalid_change: action at index %s has no type field', action_idx);
                END IF;
                
                -- ALLOWLIST: action types
                IF (workflow_actions->action_idx->>'type') NOT IN ('create_follow_up_task', 'notify_owner', 'send_email') THEN
                    RETURN format('invalid_change: action type at index %s not allowlisted', action_idx);
                END IF;
                
                -- VALIDATE: create_follow_up_task requires delay_hours
                IF (workflow_actions->action_idx->>'type') = 'create_follow_up_task' 
                   AND NOT (workflow_actions->action_idx ? 'delay_hours') THEN
                    RETURN format('invalid_change: action at index %s missing delay_hours', action_idx);
                END IF;
            END LOOP;
            
            -- ATOMIC: Create workflow, increment version, update proposal, insert audit
            BEGIN
                INSERT INTO public.workflows (crm_id, name, trigger, actions, status)
                VALUES (
                    proposal_rec.crm_id,
                    workflow_name,
                    workflow_trigger,
                    workflow_actions,
                    'draft'  -- New workflows start as drafts for review
                )
                RETURNING id, to_jsonb(workflows) INTO workflow_uuid, workflow_new;
                
                -- Increment CRM configuration version
                UPDATE public.crms
                SET configuration_version = configuration_version + 1, updated_at = NOW()
                WHERE id = proposal_rec.crm_id;
                
                -- Update proposal status
                UPDATE public.evolution_proposals
                SET status = 'applied', applied_at = NOW(), applied_by = actor_uuid, updated_at = NOW()
                WHERE id = proposal_uuid;
                
                -- Insert audit log
                INSERT INTO public.audit_logs (crm_id, crm_owner_id, user_id, action, entity_type, entity_id, old_values, new_values, metadata)
                VALUES (
                    proposal_rec.crm_id,
                    crm_rec.owner_id,
                    actor_uuid,
                    'apply',
                    'evolution_proposal',
                    proposal_uuid,
                    NULL,
                    jsonb_build_object('workflow', workflow_new, 'change_type', change_type, 'workflow_id', workflow_uuid),
                    jsonb_build_object('source', 'database_function', 'proposal_id', proposal_uuid)
                );
                
            EXCEPTION WHEN OTHERS THEN
                BEGIN
                    UPDATE public.evolution_proposals 
                    SET status = 'failed', application_error = SQLERRM, updated_at = NOW()
                    WHERE id = proposal_uuid;
                EXCEPTION WHEN OTHERS THEN
                    NULL;
                END;
                RETURN 'apply_failed';
            END;
            
        ELSE
            RETURN 'invalid_change: unknown change type';
    END CASE;
    
    RETURN 'applied';
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION public.apply_evolution_proposal(UUID) IS 'STAGE 2: Apply an approved proposal (approved → applied). EXECUTION IDENTITY: Authenticated users only (auth.uid() required). ATOMIC: Locks proposal and CRM rows (SELECT FOR UPDATE), validates all preconditions, applies change, increments configuration_version, updates proposal status, and inserts audit log in one transaction. ALLOWLIST: trigger types (record.created, record.updated, record.stage_changed), action types (create_follow_up_task, notify_owner, send_email). Never executes arbitrary SQL. On failure, rolls back transaction and sets status to failed. Returns: applied, stale, not_approved, not_owner, unauthenticated, not_found, crm_not_found, crm_archived, invalid_change, apply_failed.';

-- Combined approve-and-apply function for convenience (optional)
CREATE OR REPLACE FUNCTION public.approve_and_apply_evolution_proposal(
    proposal_uuid UUID,
    expected_version INTEGER DEFAULT NULL
)
RETURNS TEXT
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    approval_result TEXT;
    apply_result TEXT;
BEGIN
    -- Stage 1: Approve
    approval_result := public.approve_evolution_proposal(proposal_uuid, expected_version);
    
    IF approval_result != 'approved' THEN
        RETURN approval_result;
    END IF;
    
    -- Stage 2: Apply
    apply_result := public.apply_evolution_proposal(proposal_uuid);
    
    RETURN apply_result;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION public.approve_and_apply_evolution_proposal(UUID, INTEGER) IS 'Convenience: Approve and apply in one call. Uses two-stage internally. Returns result of apply stage.';

-- Reject a proposal
CREATE OR REPLACE FUNCTION public.reject_evolution_proposal(
    proposal_uuid UUID,
    p_reason TEXT
)
RETURNS TEXT
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    proposal_rec RECORD;
    actor_uuid UUID;
BEGIN
    actor_uuid := auth.uid();
    IF actor_uuid IS NULL THEN RETURN 'unauthenticated'; END IF;
    IF p_reason IS NULL OR length(trim(p_reason)) < 3 THEN RETURN 'invalid_reason'; END IF;
    
    SELECT ep.*, crms.owner_id AS crm_owner_id, crms.status AS crm_status
    INTO proposal_rec
    FROM public.evolution_proposals ep
    JOIN public.crms ON crms.id = ep.crm_id
    WHERE ep.id = proposal_uuid;
    
    IF NOT FOUND THEN RETURN 'not_found'; END IF;
    IF proposal_rec.crm_status != 'active' THEN RETURN 'crm_archived'; END IF;
    IF proposal_rec.crm_owner_id != actor_uuid THEN RETURN 'not_owner'; END IF;
    IF proposal_rec.status NOT IN ('pending', 'approved') THEN RETURN 'not_pending_or_approved'; END IF;
    
    UPDATE public.evolution_proposals
    SET status = 'rejected', rejected_at = NOW(), rejected_by = actor_uuid, rejection_reason = p_reason, updated_at = NOW()
    WHERE id = proposal_uuid AND status IN ('pending', 'approved');
    
    IF NOT FOUND THEN RETURN 'not_pending_or_approved'; END IF;
    
    INSERT INTO public.audit_logs (crm_id, crm_owner_id, user_id, action, entity_type, entity_id, new_values, metadata)
    VALUES (proposal_rec.crm_id, actor_uuid, actor_uuid, 'reject', 'evolution_proposal', proposal_uuid,
            jsonb_build_object('status', 'rejected', 'reason', p_reason, 'rejected_by', actor_uuid),
            jsonb_build_object('source', 'database_function'));
    
    RETURN 'rejected';
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION public.reject_evolution_proposal(UUID, TEXT) IS 'Reject a pending or approved proposal. Returns: rejected, not_pending_or_approved, not_owner, not_found, unauthenticated, crm_archived, invalid_reason.';

-- Mark proposal as stale
CREATE OR REPLACE FUNCTION public.mark_proposal_stale(proposal_uuid UUID)
RETURNS BOOLEAN
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    actor_uuid UUID;
BEGIN
    actor_uuid := auth.uid();
    IF actor_uuid IS NULL THEN RAISE EXCEPTION 'Unauthenticated' USING ERRCODE = '42501'; END IF;

    UPDATE public.evolution_proposals AS ep
    SET status = 'stale', updated_at = NOW()
    FROM public.crms AS c
    WHERE ep.id = proposal_uuid
      AND c.id = ep.crm_id
      AND c.owner_id = actor_uuid
      AND c.status = 'active'
      AND ep.status IN ('pending', 'approved')
      AND c.configuration_version <> ep.configuration_version_at_creation;

    RETURN FOUND;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION public.mark_proposal_stale(UUID) IS 'Mark a pending/approved proposal as stale when configuration changes. Returns true if updated.';

-- ----------------------------------------------------------------------------
-- SECTION 9: Row-Level Security Policies
-- ----------------------------------------------------------------------------

ALTER TABLE public.crms ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.records ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.workflows ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.evolution_proposals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_logs ENABLE ROW LEVEL SECURITY;

-- CRM policies
CREATE POLICY crms_select_isolation ON public.crms FOR SELECT USING (owner_id = auth.uid());
CREATE POLICY crms_insert_isolation ON public.crms FOR INSERT WITH CHECK (owner_id = auth.uid());
CREATE POLICY crms_update_isolation ON public.crms FOR UPDATE USING (owner_id = auth.uid() AND status = 'active') WITH CHECK (owner_id = auth.uid() AND status = 'active');
CREATE POLICY crms_delete_isolation ON public.crms FOR DELETE USING (owner_id = auth.uid() AND status = 'active');

-- Records policies
CREATE POLICY records_select_isolation ON public.records FOR SELECT USING (deleted_at IS NULL AND EXISTS (SELECT 1 FROM public.crms WHERE crms.id = records.crm_id AND crms.owner_id = auth.uid()));
CREATE POLICY records_insert_isolation ON public.records FOR INSERT WITH CHECK (EXISTS (SELECT 1 FROM public.crms WHERE crms.id = records.crm_id AND crms.owner_id = auth.uid() AND crms.status = 'active'));
CREATE POLICY records_update_isolation ON public.records FOR UPDATE USING (EXISTS (SELECT 1 FROM public.crms WHERE crms.id = records.crm_id AND crms.owner_id = auth.uid() AND crms.status = 'active')) WITH CHECK (EXISTS (SELECT 1 FROM public.crms WHERE crms.id = records.crm_id AND crms.owner_id = auth.uid() AND crms.status = 'active'));
CREATE POLICY records_delete_isolation ON public.records FOR DELETE USING (EXISTS (SELECT 1 FROM public.crms WHERE crms.id = records.crm_id AND crms.owner_id = auth.uid() AND crms.status = 'active'));

-- Workflows policies (no direct UPDATE - must use evolution proposals)
CREATE POLICY workflows_select_isolation ON public.workflows FOR SELECT USING (EXISTS (SELECT 1 FROM public.crms WHERE crms.id = workflows.crm_id AND crms.owner_id = auth.uid()));
CREATE POLICY workflows_insert_isolation ON public.workflows FOR INSERT WITH CHECK (EXISTS (SELECT 1 FROM public.crms WHERE crms.id = workflows.crm_id AND crms.owner_id = auth.uid() AND crms.status = 'active'));
-- UPDATE blocked: workflow changes must go through evolution proposal approval process
CREATE POLICY workflows_update_restricted ON public.workflows FOR UPDATE USING (false) WITH CHECK (false);
CREATE POLICY workflows_delete_isolation ON public.workflows FOR DELETE USING (EXISTS (SELECT 1 FROM public.crms WHERE crms.id = workflows.crm_id AND crms.owner_id = auth.uid() AND crms.status = 'active'));

-- Evolution proposals policies (UPDATE blocked, must use functions)
CREATE POLICY evolution_proposals_select_isolation ON public.evolution_proposals FOR SELECT USING (EXISTS (SELECT 1 FROM public.crms WHERE crms.id = evolution_proposals.crm_id AND crms.owner_id = auth.uid()));
CREATE POLICY evolution_proposals_insert_isolation ON public.evolution_proposals FOR INSERT WITH CHECK (EXISTS (SELECT 1 FROM public.crms WHERE crms.id = evolution_proposals.crm_id AND crms.owner_id = auth.uid() AND crms.status = 'active'));
CREATE POLICY evolution_proposals_update_restricted ON public.evolution_proposals FOR UPDATE USING (false) WITH CHECK (false);
CREATE POLICY evolution_proposals_delete_isolation ON public.evolution_proposals FOR DELETE USING (EXISTS (SELECT 1 FROM public.crms WHERE crms.id = evolution_proposals.crm_id AND crms.owner_id = auth.uid() AND crms.status = 'active'));

-- Audit logs policies (read-only)
CREATE POLICY audit_logs_select_isolation ON public.audit_logs FOR SELECT USING (crm_owner_id = auth.uid() OR EXISTS (SELECT 1 FROM public.crms WHERE crms.id = audit_logs.crm_id AND crms.owner_id = auth.uid()));
CREATE POLICY audit_logs_insert_restricted ON public.audit_logs FOR INSERT WITH CHECK (false);
CREATE POLICY audit_logs_update_restricted ON public.audit_logs FOR UPDATE USING (false);
CREATE POLICY audit_logs_delete_restricted ON public.audit_logs FOR DELETE USING (false);

-- ----------------------------------------------------------------------------
-- SECTION 10: Grants
-- ----------------------------------------------------------------------------

REVOKE ALL ON FUNCTION public.update_updated_at_column() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.is_crm_active(UUID) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.verify_crm_ownership(UUID, UUID) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.log_audit_event(UUID, UUID, TEXT, TEXT, UUID, JSONB, JSONB, JSONB) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.approve_evolution_proposal(UUID, INTEGER) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.apply_evolution_proposal(UUID) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.approve_and_apply_evolution_proposal(UUID, INTEGER) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.reject_evolution_proposal(UUID, TEXT) FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION public.mark_proposal_stale(UUID) FROM PUBLIC, anon;

GRANT USAGE ON SCHEMA public TO authenticated;

-- Table grants: evolution proposals require function-based workflow changes
REVOKE ALL ON TABLE public.crms, public.records, public.workflows, public.evolution_proposals, public.audit_logs FROM anon;
REVOKE UPDATE ON public.workflows FROM authenticated;
REVOKE UPDATE ON public.evolution_proposals FROM authenticated;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, TRIGGER, REFERENCES ON public.audit_logs FROM authenticated;

GRANT SELECT ON public.crms, public.records, public.workflows, public.evolution_proposals, public.audit_logs TO authenticated;
GRANT INSERT, UPDATE, DELETE ON public.crms, public.records TO authenticated;
-- Workflows: INSERT only (create draft), SELECT, DELETE. No direct UPDATE - must use evolution proposals.
GRANT INSERT, DELETE ON public.workflows TO authenticated;
GRANT INSERT, DELETE ON public.evolution_proposals TO authenticated;

GRANT EXECUTE ON FUNCTION public.is_crm_active(UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION public.verify_crm_ownership(UUID, UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION public.approve_evolution_proposal(UUID, INTEGER) TO authenticated;
GRANT EXECUTE ON FUNCTION public.apply_evolution_proposal(UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION public.approve_and_apply_evolution_proposal(UUID, INTEGER) TO authenticated;
GRANT EXECUTE ON FUNCTION public.reject_evolution_proposal(UUID, TEXT) TO authenticated;
GRANT EXECUTE ON FUNCTION public.mark_proposal_stale(UUID) TO authenticated;

GRANT EXECUTE ON FUNCTION public.log_audit_event(UUID, UUID, TEXT, TEXT, UUID, JSONB, JSONB, JSONB) TO service_role;

-- ----------------------------------------------------------------------------
-- SECTION 11: Documentation
-- ----------------------------------------------------------------------------

/*
EVOLUTION PROPOSAL LIFECYCLE:

Two-Stage Approval Process:

STAGE 1 - APPROVE: pending → approved
- User calls approve_evolution_proposal(proposal_uuid, expected_version)
- System validates:
  * User is authenticated (auth.uid() != NULL)
  * CRM is active (status = 'active')
  * User owns the CRM (owner_id = auth.uid())
  * Proposal is pending (status = 'pending')
  * Proposal is not stale (configuration_version matches)
  * proposed_change.type is allowlisted (workflow.update, workflow.create)
- On success: status → 'approved', approved_at/approved_by set
- Returns: 'approved' on success, or error code

STAGE 2 - APPLY: approved → applied
- Authenticated user calls apply_evolution_proposal(proposal_uuid)
- EXECUTION IDENTITY: Authenticated users only (auth.uid() required). No service_role bypass.
- System validates:
  * User is authenticated (auth.uid() != NULL)
  * Proposal is approved (status = 'approved')
  * CRM is active and user owns it
  * Proposal is not stale (re-check configuration_version)
- CONCURRENCY: Locks both proposal and CRM rows (SELECT FOR UPDATE)
- ATOMIC TRANSACTION: All operations succeed or all roll back
  1. Workflow mutation (update actions or create new workflow)
  2. CRM configuration_version increment
  3. Proposal status transition to 'applied'
  4. Audit log insertion
- ALLOWLIST VALIDATION:
  * Change types: workflow.update, workflow.create
  * Trigger types: record.created, record.updated, record.stage_changed
  * Action types: create_follow_up_task, notify_owner, send_email
- On success: status → 'applied', applied_at/applied_by set, configuration_version incremented
- On failure: transaction rolls back, status → 'failed' with error details
- Returns: 'applied' on success, or error code

ALLOWLISTED CHANGE TYPES:

1. workflow.update:
   - Required: workflow_id (UUID), delay_hours (integer 0-8760)
   - Validates workflow exists, belongs to CRM, has actions array
   - Validates first action has delay_hours field
   - Updates workflow.actions[0].delay_hours
   - Increments CRM configuration_version
   - Captures old and new workflow state in audit log

2. workflow.create:
   - Required: name (non-empty string), trigger (object with 'type' field)
   - Optional: actions (array, each with 'type' field)
   - Trigger type must be allowlisted: record.created, record.updated, record.stage_changed
   - Action types must be allowlisted: create_follow_up_task, notify_owner, send_email
   - create_follow_up_task requires delay_hours
   - Creates new workflow in 'draft' status for review
   - Increments CRM configuration_version

SECURITY GUARANTEES:
- EXECUTION IDENTITY: Authenticated users only. No service_role bypass of ownership checks.
- Never executes arbitrary SQL from proposed_change
- All change types, trigger types, and action types are hardcoded and validated
- Actor derived from auth.uid() prevents impersonation
- RLS blocks direct UPDATE to evolution_proposals AND workflows
- All workflow changes must go through evolution proposal approval
- All transitions logged to audit_logs with old and new states
- Stale proposals detected at both approve and apply stages
- Concurrent application prevented by SELECT FOR UPDATE on proposal and CRM
- All type conversions validated with exception handling
- TRANSACTION ATOMICITY: Mutation, version increment, status change, and audit in one transaction

CONCURRENCY SAFETY:
- Row-level locking (SELECT FOR UPDATE) on both proposal and CRM
- Prevents simultaneous application of same proposal
- Prevents configuration changes during application
- Status checks use WHERE clauses to prevent race conditions
- Failed applications set 'failed' status after transaction rollback
- Stale detection re-checked at application time

GRANTS AND PRIVILEGES:
- Workflows: No direct UPDATE grant to authenticated users
- Workflows: INSERT (create draft), SELECT, DELETE only
- Evolution proposals: INSERT, SELECT, DELETE; UPDATE blocked
- All workflow modifications must go through apply_evolution_proposal()
- SECURITY DEFINER functions bypass RLS but enforce explicit authorization

STATUS TRANSITIONS:
pending → approved → applied (success)
pending → approved → failed (application error, transaction rolled back)
pending → rejected (user rejects)
pending → stale (configuration changed)
approved → stale (configuration changed before apply)
approved → rejected (user can reject approved proposals)
approved → failed (application error, transaction rolled back)

API CONTRACT NOTES:
- Approval endpoint: POST /api/v1/crm/{crm_id}/evolution/proposals/{proposal_id}/approve
  Returns status: "approved" (not "applied")
- Apply endpoint: POST /api/v1/crm/{crm_id}/evolution/proposals/{proposal_id}/apply
  Returns status: "applied" on success
- Convenience: approve_and_apply_evolution_proposal() combines both stages

FASTAPI INTEGRATION:
1. Approval endpoint calls approve_evolution_proposal()
2. If returns 'approved', call apply_evolution_proposal() in separate endpoint or internally
3. Handle all return codes with appropriate HTTP status codes:
   - 'applied': 200 OK
   - 'not_approved', 'not_owner', 'unauthenticated': 403/401
   - 'stale': 409 Conflict
   - 'invalid_change': 422 Unprocessable Entity
   - 'apply_failed': 500 Internal Server Error
*/
