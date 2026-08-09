-- Note: Run only when migration 0007 actually added the columns; databases that already had them keep their saved values.
-- pytorrent:applied-if SELECT COUNT(*) = 1 AS applied FROM schema_migrations WHERE version = 7 AND source = 'baseline';
UPDATE user_preferences
SET free_space_check_enabled = 0
WHERE free_space_check_enabled = 1
  AND EXISTS (
    SELECT 1
    FROM schema_migrations
    WHERE version = 7
      AND source = 'file'
      AND COALESCE(user_preferences.updated_at, '') < applied_at
  );
