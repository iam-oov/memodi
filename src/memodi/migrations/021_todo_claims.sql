ALTER TABLE observations ADD COLUMN IF NOT EXISTS claimed_by TEXT;
ALTER TABLE observations ADD COLUMN IF NOT EXISTS claimed_label TEXT;
ALTER TABLE observations ADD COLUMN IF NOT EXISTS claimed_until TIMESTAMPTZ;
ALTER TABLE observations ADD COLUMN IF NOT EXISTS claim_minutes INTEGER;
ALTER TABLE observations ADD COLUMN IF NOT EXISTS plan_ref TEXT;
ALTER TABLE observations ADD COLUMN IF NOT EXISTS phase_current INTEGER;
ALTER TABLE observations ADD COLUMN IF NOT EXISTS phase_total INTEGER;
ALTER TABLE observations ADD COLUMN IF NOT EXISTS phase_label TEXT;
ALTER TABLE observations ADD COLUMN IF NOT EXISTS outcome TEXT;

ALTER TABLE observations ADD CONSTRAINT observations_phase_bounds CHECK (
    (phase_current IS NULL OR phase_current >= 1)
    AND (phase_total IS NULL OR phase_total >= 1)
    AND (phase_current IS NULL OR phase_total IS NULL OR phase_current <= phase_total)
);
