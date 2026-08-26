-- Fold existing workspace and project names to lower case, so the rows match
-- what normalize_name() produces from here on. Without this, the first session
-- after the change creates `tirielinc` alongside `TirielInc` and every memory
-- written before the fold becomes unreachable.
--
-- Rows that would COLLIDE after folding are left alone on purpose: two
-- projects differing only in case are a pre-existing duplicate, and merging
-- them moves observations between owners' data. That is a decision for
-- memodi_merge_projects (which has dry_run), not for a migration nobody read.

UPDATE workspaces w
SET name = lower(trim(w.name))
WHERE w.name <> lower(trim(w.name))
  AND NOT EXISTS (
      SELECT 1 FROM workspaces other
      WHERE other.id <> w.id
        AND other.owner_user_id IS NOT DISTINCT FROM w.owner_user_id
        AND lower(trim(other.name)) = lower(trim(w.name))
  );

UPDATE projects p
SET name = lower(trim(p.name))
WHERE p.name <> lower(trim(p.name))
  AND NOT EXISTS (
      SELECT 1 FROM projects other
      WHERE other.id <> p.id
        AND other.workspace_id IS NOT DISTINCT FROM p.workspace_id
        AND lower(trim(other.name)) = lower(trim(p.name))
  );

-- metadata.affects holds project NAMES, matched with `?` against the same
-- folded values. Leaving them cased would silently drop every cross-repo
-- observation out of the scope predicate that reads them.
UPDATE observations o
SET metadata = jsonb_set(
        o.metadata,
        '{affects}',
        (
            SELECT jsonb_agg(DISTINCT lower(trim(name)))
            FROM jsonb_array_elements_text(o.metadata->'affects') AS name
        )
    )
WHERE o.metadata ? 'affects'
  AND jsonb_typeof(o.metadata->'affects') = 'array'
  AND jsonb_array_length(o.metadata->'affects') > 0;
