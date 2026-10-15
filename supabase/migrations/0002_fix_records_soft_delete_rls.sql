-- ============================================================================
-- Migration: 0002_fix_records_soft_delete_rls.sql
-- Purpose: Fix records soft-delete RLS defect by decoupling tenant boundary
--          isolation from application-level soft-delete query filtering.
-- Author: Self-Evolving CRM Team
-- Target: public.records
-- ============================================================================

-- ----------------------------------------------------------------------------
-- SECTION 1: Replace Records SELECT Policy
-- ----------------------------------------------------------------------------
-- The initial policy required `deleted_at IS NULL` directly inside the SELECT
-- RLS rule. Under PostgreSQL RLS specification, an UPDATE with RETURNING *
-- (standard PostgREST behavior) evaluates the SELECT policy against the updated row.
-- Setting deleted_at = NOW() caused the updated row to fail the SELECT policy,
-- throwing error 42501 (new row violates row-level security policy).
--
-- This migration updates the SELECT policy to enforce tenant isolation strictly
-- via CRM ownership. Operational queries filter `deleted_at IS NULL` at the
-- application/query layer.
-- ----------------------------------------------------------------------------

DROP POLICY IF EXISTS records_select_isolation ON public.records;

CREATE POLICY records_select_isolation ON public.records FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.crms
            WHERE crms.id = records.crm_id
              AND crms.owner_id = auth.uid()
        )
    );

COMMENT ON POLICY records_select_isolation ON public.records IS 
    'Enforce tenant isolation on records by parent CRM ownership. Soft-delete filtering is maintained at the application query layer.';
