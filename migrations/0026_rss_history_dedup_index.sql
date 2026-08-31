-- pytorrent:applied-if SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_rss_history_unique_success') AS applied;
-- Note: Upgrade databases must enforce the same pre-enqueue RSS deduplication invariant as fresh schema databases.
UPDATE rss_history AS r
SET status='skipped',
    message=CASE
      WHEN COALESCE(message,'')='' THEN 'Deduplicated while enabling RSS queue uniqueness'
      ELSE message || '; deduplicated while enabling RSS queue uniqueness'
    END
WHERE r.status IN ('queued','added')
  AND r.link IS NOT NULL
  AND EXISTS (
    SELECT 1
    FROM rss_history AS k
    WHERE k.profile_id=r.profile_id
      AND COALESCE(k.rule_id,0)=COALESCE(r.rule_id,0)
      AND k.link=r.link
      AND k.status IN ('queued','added')
      AND (
        (k.status='added' AND r.status='queued')
        OR (k.status=r.status AND k.id<r.id)
      )
  );

CREATE UNIQUE INDEX IF NOT EXISTS idx_rss_history_unique_success
ON rss_history(profile_id, COALESCE(rule_id,0), link)
WHERE status IN ('queued','added');
