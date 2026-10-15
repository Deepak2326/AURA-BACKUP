-- Migration: 0005_fix_evolution_proposal_terminal_state_approval_fields.sql
-- Keep approved_at/approved_by consistent when approved proposals become stale
-- or failed. This is a forward-only replacement of the 0004 function.
-- ============================================================================
-- Migration: 0005_fix_evolution_proposal_terminal_state_approval_fields.sql
-- Purpose: Preserve approval-field consistency on stale and failed application.
--          In migration 0003, the action validation loop contained:
--              action_rec := jsonb_build_object('idx', action_idx, 'action', ...);
--          where action_rec was declared as RECORD. In PL/pgSQL, assigning
--          a JSONB value to an untyped RECORD variable causes error 0A000:
--          "input of anonymous composite types is not implemented".
--          Furthermore, action_rec was completely unused in subsequent checks.
--          This migration also clears approval metadata on stale/failed
--          transitions while preserving validation, authorization, locking,
--          and atomic subtransaction logic.
-- Author: Self-Evolving CRM Team
-- Target: public.apply_evolution_proposal function
-- ============================================================================

-- Replace the apply_evolution_proposal function with the corrected version
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
    action_idx INTEGER;
    action_delay INTEGER;
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
        SET status = 'stale', approved_at = NULL, approved_by = NULL, updated_at = NOW()
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
                -- Avoid RETURNING to_jsonb(workflows) which references the table
                -- name and creates an anonymous composite type (error 0A000).
                -- Instead, SELECT the updated row into JSONB after the UPDATE.
                UPDATE public.workflows
                SET actions = jsonb_set(
                    actions,
                    '{0,delay_hours}',
                    to_jsonb(new_delay_hours)
                )
                WHERE id = workflow_uuid AND crm_id = proposal_rec.crm_id;
                
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'Workflow update failed';
                END IF;
                
                -- Build workflow_new from the updated row (avoids composite type issue)
                SELECT to_jsonb(w) INTO workflow_new
                FROM public.workflows w
                WHERE w.id = workflow_uuid;
                
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
                    SET status = 'failed', approved_at = NULL, approved_by = NULL, application_error = SQLERRM, updated_at = NOW()
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
            
            IF jsonb_typeof(workflow_trigger->'type') IS DISTINCT FROM 'string' THEN
                RETURN 'invalid_change: trigger type must be a string';
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
            
            -- VALIDATE: each action has allowlisted type and valid structure
            FOR action_idx IN SELECT generate_series(0, jsonb_array_length(workflow_actions) - 1) LOOP
                IF jsonb_typeof(workflow_actions->action_idx) IS DISTINCT FROM 'object' THEN
                    RETURN format('invalid_change: action at index %s must be a JSON object', action_idx);
                END IF;

                IF NOT (workflow_actions->action_idx ? 'type') THEN
                    RETURN format('invalid_change: action at index %s has no type field', action_idx);
                END IF;
                
                IF jsonb_typeof(workflow_actions->action_idx->'type') IS DISTINCT FROM 'string' THEN
                    RETURN format('invalid_change: action type at index %s must be a string', action_idx);
                END IF;

                -- ALLOWLIST: action types
                IF (workflow_actions->action_idx->>'type') NOT IN ('create_follow_up_task', 'notify_owner', 'send_email') THEN
                    RETURN format('invalid_change: action type at index %s not allowlisted', action_idx);
                END IF;
                
                -- VALIDATE: create_follow_up_task requires delay_hours as valid integer in range 0-8760
                IF (workflow_actions->action_idx->>'type') = 'create_follow_up_task' THEN
                    IF NOT (workflow_actions->action_idx ? 'delay_hours') THEN
                        RETURN format('invalid_change: action at index %s missing delay_hours', action_idx);
                    END IF;
                    BEGIN
                        action_delay := (workflow_actions->action_idx->>'delay_hours')::INTEGER;
                    EXCEPTION WHEN OTHERS THEN
                        RETURN format('invalid_change: action at index %s delay_hours is not a valid integer', action_idx);
                    END;
                    IF action_delay < 0 OR action_delay > 8760 THEN
                        RETURN format('invalid_change: action at index %s delay_hours out of range (0-8760)', action_idx);
                    END IF;
                END IF;
            END LOOP;
            
            -- ATOMIC: Create workflow, increment version, update proposal, insert audit
            BEGIN
                -- Insert workflow and get the generated ID
                INSERT INTO public.workflows (crm_id, name, trigger, actions, status)
                VALUES (
                    proposal_rec.crm_id,
                    workflow_name,
                    workflow_trigger,
                    workflow_actions,
                    'draft'  -- New workflows start as drafts for review
                )
                RETURNING id INTO workflow_uuid;
                
                -- Build workflow_new JSONB manually from known values (avoids composite type issue)
                workflow_new := jsonb_build_object(
                    'id', workflow_uuid,
                    'crm_id', proposal_rec.crm_id,
                    'name', workflow_name,
                    'trigger', workflow_trigger,
                    'actions', workflow_actions,
                    'status', 'draft',
                    'created_at', NOW(),
                    'updated_at', NOW()
                );
                
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
                    SET status = 'failed', approved_at = NULL, approved_by = NULL, application_error = SQLERRM, updated_at = NOW()
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
