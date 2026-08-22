-- Note: Profile-shared configuration must not be owned by the user who last edited or created it.
-- Personal per-profile UI preferences remain keyed by (user_id, profile_id); these tables become true profile-shared state with user ids kept only as audit metadata.
-- pytorrent:applied-if SELECT (EXISTS(SELECT 1 FROM pragma_table_info('disk_monitor_preferences') WHERE name='updated_by_user_id') AND EXISTS(SELECT 1 FROM pragma_table_info('labels') WHERE name='created_by_user_id') AND EXISTS(SELECT 1 FROM pragma_table_info('ratio_groups') WHERE name='created_by_user_id') AND EXISTS(SELECT 1 FROM pragma_table_info('automation_rules') WHERE name='created_by_user_id') AND EXISTS(SELECT 1 FROM pragma_table_info('download_plan_settings') WHERE name='updated_by_user_id') AND EXISTS(SELECT 1 FROM pragma_table_info('operation_log_settings') WHERE name='updated_by_user_id')) AS applied;

DROP INDEX IF EXISTS idx_disk_monitor_preferences_owner;
DROP INDEX IF EXISTS idx_labels_profile_name;
DROP INDEX IF EXISTS idx_ratio_groups_user_profile_enabled;
DROP INDEX IF EXISTS idx_ratio_groups_profile_enabled;
DROP INDEX IF EXISTS idx_ratio_groups_profile_name;
DROP INDEX IF EXISTS idx_automation_rules_user_profile_enabled;
DROP INDEX IF EXISTS idx_automation_rules_profile_enabled;
DROP INDEX IF EXISTS idx_download_plan_settings_profile;
DROP INDEX IF EXISTS idx_operation_log_settings_profile;
DROP TABLE IF EXISTS disk_monitor_preferences_new_0017;
DROP TABLE IF EXISTS labels_new_0017;
DROP TABLE IF EXISTS ratio_groups_new_0017;
DROP TABLE IF EXISTS ratio_group_id_map_0017;
DROP TABLE IF EXISTS automation_rules_new_0017;
DROP TABLE IF EXISTS download_plan_settings_new_0017;
DROP TABLE IF EXISTS operation_log_settings_new_0017;

CREATE TABLE disk_monitor_preferences_new_0017 (
  profile_id INTEGER PRIMARY KEY,
  updated_by_user_id INTEGER,
  paths_json TEXT,
  mode TEXT DEFAULT 'default',
  selected_path TEXT,
  stop_enabled INTEGER DEFAULT 0,
  stop_threshold INTEGER DEFAULT 98,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(profile_id) REFERENCES rtorrent_profiles(id)
);
INSERT INTO disk_monitor_preferences_new_0017(
  profile_id, updated_by_user_id, paths_json, mode, selected_path, stop_enabled, stop_threshold, created_at, updated_at
)
SELECT profile_id, user_id, paths_json, mode, selected_path, stop_enabled, stop_threshold, created_at, updated_at
FROM disk_monitor_preferences;
ALTER TABLE disk_monitor_preferences RENAME TO disk_monitor_preferences_legacy_0017;
ALTER TABLE disk_monitor_preferences_new_0017 RENAME TO disk_monitor_preferences;
DROP TABLE disk_monitor_preferences_legacy_0017;
CREATE INDEX IF NOT EXISTS idx_disk_monitor_preferences_updated_by ON disk_monitor_preferences(updated_by_user_id);

CREATE TABLE labels_new_0017 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_by_user_id INTEGER,
  updated_by_user_id INTEGER,
  profile_id INTEGER,
  name TEXT NOT NULL,
  color TEXT DEFAULT '#64748b',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
INSERT INTO labels_new_0017(id,created_by_user_id,updated_by_user_id,profile_id,name,color,created_at,updated_at)
SELECT id,user_id,user_id,profile_id,name,color,created_at,updated_at
FROM (
  SELECT l.*,
         ROW_NUMBER() OVER (
           PARTITION BY profile_id, lower(name)
           ORDER BY COALESCE(updated_at,'') DESC, id DESC
         ) AS rn
  FROM labels l
)
WHERE rn=1;
ALTER TABLE labels RENAME TO labels_legacy_0017;
ALTER TABLE labels_new_0017 RENAME TO labels;
DROP TABLE labels_legacy_0017;
CREATE UNIQUE INDEX IF NOT EXISTS idx_labels_profile_name ON labels(profile_id, name COLLATE NOCASE);

CREATE TABLE ratio_group_id_map_0017 (
  old_id INTEGER PRIMARY KEY,
  new_id INTEGER NOT NULL
);
INSERT INTO ratio_group_id_map_0017(old_id,new_id)
SELECT g.id, (
  SELECT g2.id
  FROM ratio_groups g2
  WHERE g2.profile_id IS g.profile_id AND lower(g2.name)=lower(g.name)
  ORDER BY COALESCE(g2.updated_at,'') DESC, g2.id DESC
  LIMIT 1
)
FROM ratio_groups g;
UPDATE ratio_assignments
SET group_id=(SELECT m.new_id FROM ratio_group_id_map_0017 m WHERE m.old_id=ratio_assignments.group_id)
WHERE group_id IN (SELECT old_id FROM ratio_group_id_map_0017 WHERE old_id<>new_id);
UPDATE ratio_history
SET group_id=(SELECT m.new_id FROM ratio_group_id_map_0017 m WHERE m.old_id=ratio_history.group_id)
WHERE group_id IN (SELECT old_id FROM ratio_group_id_map_0017 WHERE old_id<>new_id);

CREATE TABLE ratio_groups_new_0017 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_by_user_id INTEGER,
  updated_by_user_id INTEGER,
  profile_id INTEGER,
  name TEXT NOT NULL,
  min_ratio REAL DEFAULT 1.0,
  max_ratio REAL DEFAULT 2.0,
  seed_time_minutes INTEGER DEFAULT 0,
  min_seed_time_minutes INTEGER DEFAULT 0,
  ignore_private INTEGER DEFAULT 1,
  ignore_active_upload INTEGER DEFAULT 1,
  active_upload_min_bytes INTEGER DEFAULT 1024,
  move_path TEXT,
  set_label TEXT,
  action TEXT DEFAULT 'stop',
  enabled INTEGER DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
INSERT INTO ratio_groups_new_0017(
  id,created_by_user_id,updated_by_user_id,profile_id,name,min_ratio,max_ratio,seed_time_minutes,min_seed_time_minutes,
  ignore_private,ignore_active_upload,active_upload_min_bytes,move_path,set_label,action,enabled,created_at,updated_at
)
SELECT id,user_id,user_id,profile_id,name,min_ratio,max_ratio,seed_time_minutes,min_seed_time_minutes,
       ignore_private,ignore_active_upload,active_upload_min_bytes,move_path,set_label,action,enabled,created_at,updated_at
FROM (
  SELECT g.*,
         ROW_NUMBER() OVER (
           PARTITION BY profile_id, lower(name)
           ORDER BY COALESCE(updated_at,'') DESC, id DESC
         ) AS rn
  FROM ratio_groups g
)
WHERE rn=1;
ALTER TABLE ratio_groups RENAME TO ratio_groups_legacy_0017;
ALTER TABLE ratio_groups_new_0017 RENAME TO ratio_groups;
DROP TABLE ratio_groups_legacy_0017;
CREATE UNIQUE INDEX IF NOT EXISTS idx_ratio_groups_profile_name ON ratio_groups(profile_id, name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_ratio_groups_profile_enabled ON ratio_groups(profile_id, enabled, name);
DROP TABLE ratio_group_id_map_0017;

CREATE TABLE automation_rules_new_0017 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_by_user_id INTEGER,
  updated_by_user_id INTEGER,
  profile_id INTEGER,
  name TEXT NOT NULL,
  enabled INTEGER DEFAULT 1,
  conditions_json TEXT NOT NULL,
  effects_json TEXT NOT NULL,
  cooldown_minutes INTEGER DEFAULT 60,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
INSERT INTO automation_rules_new_0017(
  id,created_by_user_id,updated_by_user_id,profile_id,name,enabled,conditions_json,effects_json,cooldown_minutes,created_at,updated_at
)
SELECT id,user_id,user_id,profile_id,name,enabled,conditions_json,effects_json,cooldown_minutes,created_at,updated_at
FROM automation_rules;
ALTER TABLE automation_rules RENAME TO automation_rules_legacy_0017;
ALTER TABLE automation_rules_new_0017 RENAME TO automation_rules;
DROP TABLE automation_rules_legacy_0017;
CREATE INDEX IF NOT EXISTS idx_automation_rules_profile_enabled ON automation_rules(profile_id, enabled);
CREATE INDEX IF NOT EXISTS idx_automation_rules_created_by ON automation_rules(created_by_user_id, profile_id);

CREATE TABLE download_plan_settings_new_0017 (
  profile_id INTEGER PRIMARY KEY,
  updated_by_user_id INTEGER,
  settings_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
INSERT INTO download_plan_settings_new_0017(profile_id,updated_by_user_id,settings_json,updated_at)
SELECT profile_id,user_id,settings_json,updated_at
FROM (
  SELECT d.*,
         ROW_NUMBER() OVER (PARTITION BY profile_id ORDER BY COALESCE(updated_at,'') DESC, user_id ASC) AS rn
  FROM download_plan_settings d
)
WHERE rn=1;
ALTER TABLE download_plan_settings RENAME TO download_plan_settings_legacy_0017;
ALTER TABLE download_plan_settings_new_0017 RENAME TO download_plan_settings;
DROP TABLE download_plan_settings_legacy_0017;

CREATE TABLE operation_log_settings_new_0017 (
  profile_id INTEGER PRIMARY KEY,
  updated_by_user_id INTEGER,
  retention_mode TEXT DEFAULT 'days',
  retention_days INTEGER DEFAULT 30,
  retention_lines INTEGER DEFAULT 5000,
  retention_interval_hours INTEGER DEFAULT 24,
  job_retention_mode TEXT DEFAULT 'days',
  job_retention_days INTEGER DEFAULT 7,
  job_retention_lines INTEGER DEFAULT 2000,
  job_retention_interval_hours INTEGER DEFAULT 24,
  job_last_retention_run_at TEXT,
  job_last_retention_deleted INTEGER DEFAULT 0,
  operation_retention_mode TEXT DEFAULT 'days',
  operation_retention_days INTEGER DEFAULT 30,
  operation_retention_lines INTEGER DEFAULT 5000,
  operation_retention_interval_hours INTEGER DEFAULT 24,
  operation_last_retention_run_at TEXT,
  operation_last_retention_deleted INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
INSERT INTO operation_log_settings_new_0017(
  profile_id,updated_by_user_id,retention_mode,retention_days,retention_lines,retention_interval_hours,
  job_retention_mode,job_retention_days,job_retention_lines,job_retention_interval_hours,job_last_retention_run_at,job_last_retention_deleted,
  operation_retention_mode,operation_retention_days,operation_retention_lines,operation_retention_interval_hours,operation_last_retention_run_at,operation_last_retention_deleted,
  created_at,updated_at
)
SELECT profile_id,CASE WHEN user_id=0 THEN NULL ELSE user_id END,retention_mode,retention_days,retention_lines,retention_interval_hours,
       job_retention_mode,job_retention_days,job_retention_lines,job_retention_interval_hours,job_last_retention_run_at,job_last_retention_deleted,
       operation_retention_mode,operation_retention_days,operation_retention_lines,operation_retention_interval_hours,operation_last_retention_run_at,operation_last_retention_deleted,
       created_at,updated_at
FROM (
  SELECT o.*,
         ROW_NUMBER() OVER (
           PARTITION BY profile_id
           ORDER BY CASE WHEN user_id=0 THEN 0 ELSE 1 END, COALESCE(updated_at,'') DESC, user_id ASC
         ) AS rn
  FROM operation_log_settings o
)
WHERE rn=1;
ALTER TABLE operation_log_settings RENAME TO operation_log_settings_legacy_0017;
ALTER TABLE operation_log_settings_new_0017 RENAME TO operation_log_settings;
DROP TABLE operation_log_settings_legacy_0017;
