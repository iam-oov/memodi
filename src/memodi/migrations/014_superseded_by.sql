-- superseded_by: marks an observation as replaced by a newer one without
-- deleting it, so the correction stays auditable ("why did we change this?")
-- via get_observation. Surfacing read paths filter it out; deleted_at stays
-- the separate mechanism for junk/test cleanup.
ALTER TABLE observations ADD COLUMN IF NOT EXISTS superseded_by UUID DEFAULT NULL;
