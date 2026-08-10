#!/usr/bin/env python3
"""Regression tests for safe profile/user deletion without importing Flask."""
from __future__ import annotations

import importlib.util
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = (ROOT / "schema.sql").read_text(encoding="utf-8")
DELETION_PATH = ROOT / "pytorrent" / "services" / "deletion.py"

_spec = importlib.util.spec_from_file_location("pytorrent_deletion_test", DELETION_PATH)
_deletion = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_deletion)


def _dict_factory(cursor, row):
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


class SafeDeletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = _dict_factory
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.now = "2026-08-10T21:00:00+00:00"
        self.conn.execute(
            "INSERT INTO users(id,username,role,is_active,created_at,updated_at) VALUES(1,'default','admin',1,?,?)",
            (self.now, self.now),
        )
        self.conn.execute(
            "INSERT INTO user_preferences(user_id,created_at,updated_at) VALUES(1,?,?)",
            (self.now, self.now),
        )

    def tearDown(self) -> None:
        self.conn.close()

    def _profile(self, profile_id: int, owner: int = 1, name: str | None = None, is_default: int = 0) -> None:
        self.conn.execute(
            """
            INSERT INTO rtorrent_profiles(id,user_id,name,scgi_url,is_default,created_at,updated_at)
            VALUES(?,?,?,'scgi://127.0.0.1:5000',?,?,?)
            """,
            (profile_id, owner, name or f"profile-{profile_id}", is_default, self.now, self.now),
        )

    def _user(self, user_id: int, username: str, role: str = "user") -> None:
        self.conn.execute(
            "INSERT INTO users(id,username,role,is_active,created_at,updated_at) VALUES(?,?,?,1,?,?)",
            (user_id, username, role, self.now, self.now),
        )
        self.conn.execute(
            "INSERT INTO user_preferences(user_id,created_at,updated_at) VALUES(?,?,?)",
            (user_id, self.now, self.now),
        )

    def test_profile_purge_cleans_fk_rows_scoped_rows_settings_and_active_profile(self) -> None:
        self._user(2, "reader")
        self._profile(10, is_default=1)
        self._profile(11)
        self.conn.execute("UPDATE user_preferences SET active_rtorrent_id=10 WHERE user_id IN (1,2)")
        for pid in (10, 11):
            self.conn.execute(
                "INSERT INTO user_profile_permissions(user_id,profile_id,access_level,created_at,updated_at) VALUES(2,?,'ro',?,?)",
                (pid, self.now, self.now),
            )

        self.conn.execute(
            "INSERT INTO profile_preferences(user_id,profile_id,created_at,updated_at) VALUES(1,10,?,?)",
            (self.now, self.now),
        )
        self.conn.execute(
            "INSERT INTO disk_monitor_preferences(profile_id,user_id,created_at,updated_at) VALUES(10,1,?,?)",
            (self.now, self.now),
        )
        self.conn.execute(
            "INSERT INTO poller_settings(profile_id,settings_json,updated_at) VALUES(10,'{}',?)",
            (self.now,),
        )
        self.conn.execute(
            "INSERT INTO jobs(id,user_id,profile_id,action,status,created_at,updated_at) VALUES('job-10',1,10,'noop','done',?,?)",
            (self.now, self.now),
        )
        self.conn.execute(
            "INSERT INTO labels(user_id,profile_id,name,created_at,updated_at) VALUES(1,10,'label-10',?,?)",
            (self.now, self.now),
        )
        self.conn.execute(
            "INSERT INTO operation_log_settings(user_id,profile_id,created_at,updated_at) VALUES(1,10,?,?)",
            (self.now, self.now),
        )
        for key in (
            "poller.settings.10",
            "download_planner.history.10",
            "download_planner.override_until.10",
            "backup:auto:profile:10",
            "backup:auto:profile:2:10",
            "port_check:10:6881:0",
        ):
            self.conn.execute("INSERT INTO app_settings(key,value) VALUES(?, 'x')", (key,))

        # Future FK naming is handled even if the child column is not called profile_id.
        self.conn.execute(
            "CREATE TABLE future_profile_ref(id INTEGER PRIMARY KEY, rt_id INTEGER NOT NULL REFERENCES rtorrent_profiles(id))"
        )
        self.conn.execute("INSERT INTO future_profile_ref(id,rt_id) VALUES(1,10)")

        result = _deletion.purge_profile(self.conn, 10)

        self.assertEqual(result["profile_id"], 10)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM rtorrent_profiles WHERE id=10").fetchone())
        for table in (
            "profile_preferences",
            "disk_monitor_preferences",
            "poller_settings",
            "jobs",
            "labels",
            "operation_log_settings",
            "user_profile_permissions",
            "future_profile_ref",
        ):
            column = "rt_id" if table == "future_profile_ref" else "profile_id"
            count = self.conn.execute(f'SELECT COUNT(*) AS c FROM "{table}" WHERE "{column}"=10').fetchone()["c"]
            self.assertEqual(count, 0, table)
        self.assertEqual(
            self.conn.execute("SELECT active_rtorrent_id FROM user_preferences WHERE user_id=1").fetchone()["active_rtorrent_id"],
            11,
        )
        self.assertEqual(
            self.conn.execute("SELECT active_rtorrent_id FROM user_preferences WHERE user_id=2").fetchone()["active_rtorrent_id"],
            11,
        )
        self.assertEqual(
            self.conn.execute("SELECT is_default FROM rtorrent_profiles WHERE id=11").fetchone()["is_default"],
            1,
        )
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS c FROM app_settings").fetchone()["c"], 0)

    def test_profile_purge_rolls_back_when_unknown_nested_dependency_blocks_delete(self) -> None:
        self._profile(20)
        self.conn.execute(
            "INSERT INTO profile_preferences(user_id,profile_id,created_at,updated_at) VALUES(1,20,?,?)",
            (self.now, self.now),
        )
        self.conn.execute(
            "CREATE TABLE future_parent(id INTEGER PRIMARY KEY, rt_id INTEGER NOT NULL REFERENCES rtorrent_profiles(id))"
        )
        self.conn.execute(
            "CREATE TABLE future_child(id INTEGER PRIMARY KEY, parent_id INTEGER NOT NULL REFERENCES future_parent(id))"
        )
        self.conn.execute("INSERT INTO future_parent(id,rt_id) VALUES(1,20)")
        self.conn.execute("INSERT INTO future_child(id,parent_id) VALUES(1,1)")

        with self.assertRaises(_deletion.DeletionError):
            _deletion.purge_profile(self.conn, 20)

        # Savepoint rollback restores rows deleted before the unknown dependency was discovered.
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM rtorrent_profiles WHERE id=20").fetchone())
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS c FROM profile_preferences WHERE profile_id=20").fetchone()["c"],
            1,
        )
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS c FROM future_parent WHERE rt_id=20").fetchone()["c"], 1)

    def test_user_purge_removes_owned_profiles_tokens_preferences_and_user_scoped_data(self) -> None:
        self._profile(30, owner=1)
        self._user(3, "remove-me")
        self._profile(31, owner=3)
        self._profile(32, owner=3)
        self.conn.execute(
            "INSERT INTO api_tokens(user_id,name,token_hash,token_prefix,created_at,updated_at) VALUES(3,'token','hash','prefix',?,?)",
            (self.now, self.now),
        )
        self.conn.execute(
            "INSERT INTO speed_limit_profiles(user_id,name,down_limit,up_limit,created_at,updated_at) VALUES(3,'slow',1,1,?,?)",
            (self.now, self.now),
        )
        self.conn.execute(
            "INSERT INTO labels(user_id,profile_id,name,created_at,updated_at) VALUES(3,30,'shared-profile-label',?,?)",
            (self.now, self.now),
        )
        self.conn.execute(
            "INSERT INTO profile_preferences(user_id,profile_id,created_at,updated_at) VALUES(3,31,?,?)",
            (self.now, self.now),
        )
        self.conn.execute("INSERT INTO app_settings(key,value) VALUES('backup:auto:app:3','{}')")

        result = _deletion.purge_user(self.conn, 3)

        self.assertEqual(result["deleted_profiles"], [31, 32])
        self.assertIsNone(self.conn.execute("SELECT 1 FROM users WHERE id=3").fetchone())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS c FROM api_tokens WHERE user_id=3").fetchone()["c"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS c FROM user_preferences WHERE user_id=3").fetchone()["c"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS c FROM speed_limit_profiles WHERE user_id=3").fetchone()["c"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS c FROM labels WHERE user_id=3").fetchone()["c"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS c FROM rtorrent_profiles WHERE user_id=3").fetchone()["c"], 0)
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM rtorrent_profiles WHERE id=30").fetchone())
        self.assertIsNone(self.conn.execute("SELECT 1 FROM app_settings WHERE key='backup:auto:app:3'").fetchone())

    def test_deletion_sources_keep_api_token_revoke_idempotent_and_profile_route_guarded(self) -> None:
        auth_source = (ROOT / "pytorrent" / "services" / "auth.py").read_text(encoding="utf-8")
        profile_route_source = (ROOT / "pytorrent" / "routes" / "profiles.py").read_text(encoding="utf-8")
        app_source = (ROOT / "pytorrent" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn('if row.get("revoked_at"):', auth_source)
        self.assertIn("except DeletionError as exc:", profile_route_source)
        self.assertIn("@app.errorhandler(sqlite3.IntegrityError)", app_source)
        self.assertIn("@app.errorhandler(sqlite3.OperationalError)", app_source)


if __name__ == "__main__":
    unittest.main()
