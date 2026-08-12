-- pytorrent:applied-if SELECT COUNT(*) = 0 AS applied FROM user_preferences WHERE pytorrent_theme IS NULL OR trim(pytorrent_theme) = '' OR pytorrent_theme = 'default-beta';

UPDATE user_preferences
SET pytorrent_theme = 'default'
WHERE pytorrent_theme IS NULL OR trim(pytorrent_theme) = '' OR pytorrent_theme = 'default-beta';
