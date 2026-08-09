-- Note: Footer visibility and order belong to the user/profile pair, matching other profile-scoped view preferences.
-- pytorrent:applied-if SELECT COUNT(*) = 2 AS applied FROM pragma_table_info('profile_preferences') WHERE name IN ('footer_items_json', 'footer_order_json');
ALTER TABLE profile_preferences ADD COLUMN footer_items_json TEXT;
ALTER TABLE profile_preferences ADD COLUMN footer_order_json TEXT;

-- Preserve the legacy per-user visibility for every existing profile on upgrade.
UPDATE profile_preferences
SET footer_items_json = COALESCE(
  footer_items_json,
  (SELECT user_preferences.footer_items_json
   FROM user_preferences
   WHERE user_preferences.user_id = profile_preferences.user_id),
  '{}'
);

-- Existing installs did not have a custom order, so start from the current visual order.
UPDATE profile_preferences
SET footer_order_json = COALESCE(
  footer_order_json,
  '["cpu","ram","usage_chart","disk","speed_down","speed_up","speed_peaks","limits","totals","version","port_check","sockets","rt_downloads","rt_uploads","rt_http","rt_files","rt_port","shown","selected","planner","clock","docs"]'
);
