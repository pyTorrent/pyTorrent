-- Note: Trusted scheduler jobs retain profile authority after the initiating or audit user's permissions change.
-- pytorrent:applied-if SELECT EXISTS(SELECT 1 FROM pragma_table_info('jobs') WHERE name='system_managed') AS applied;

ALTER TABLE jobs ADD COLUMN system_managed INTEGER NOT NULL DEFAULT 0 CHECK(system_managed IN (0,1));
