-- Note: Ordered heavy jobs remain sequential by default while allowing an explicit per-profile concurrency limit from Job scheduling.
-- pytorrent:applied-if SELECT EXISTS(SELECT 1 FROM pragma_table_info('rtorrent_profiles') WHERE name='ordered_parallel_jobs') AS applied;
ALTER TABLE rtorrent_profiles ADD COLUMN ordered_parallel_jobs INTEGER DEFAULT 1;
UPDATE rtorrent_profiles SET ordered_parallel_jobs=1 WHERE ordered_parallel_jobs IS NULL;
