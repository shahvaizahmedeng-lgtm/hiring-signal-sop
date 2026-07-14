-- Temporary sourcing_companies table for the audition task.
-- Exact schema from the SOP, with the nullable columns the spec demands:
-- only standardised_domain, company_name, sourcing_config_id are NOT NULL.
--
-- sourcing_config_id is TEXT (not UUID) so the SOP's sample placeholder
-- ("2asifsouahfaiusf-fsafnaa-asfasf") can be demoed as-is. Production
-- systems use a real UUID FK.

CREATE TABLE IF NOT EXISTS sourcing_companies (
    idx BIGINT GENERATED ALWAYS AS IDENTITY,
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sourcing_config_id TEXT NOT NULL,
    standardised_domain TEXT NOT NULL,
    company_name TEXT NOT NULL,
    company_linkedin_tag TEXT,
    geography TEXT,
    company_size TEXT,
    custom_fields JSONB,
    surfaced_at TIMESTAMPTZ DEFAULT now(),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- De-dupe support: the module de-dupes on custom_fields->>'job_id'
-- scoped to sourcing_config_id.
CREATE UNIQUE INDEX IF NOT EXISTS uq_sourcing_job_id
ON sourcing_companies (sourcing_config_id, (custom_fields->>'job_id'))
WHERE custom_fields->>'job_id' IS NOT NULL;

-- ---------------------------------------------------------------------------
-- ACCESS FOR THE AUDITION TEST
-- ---------------------------------------------------------------------------
-- PRODUCTION-CORRECT: put a service_role key in SUPABASE_SERVICE_KEY
-- (bypasses RLS). Nothing below is needed in that case.
--
-- TEMP TABLE WITH A PUBLISHABLE/ANON KEY: disable RLS and grant access.

ALTER TABLE sourcing_companies DISABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON sourcing_companies TO anon, authenticated;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO anon, authenticated;
