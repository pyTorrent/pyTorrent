#!/usr/bin/env python3
"""Regression tests for the original pyTorrent startup behavior."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_DIR = ROOT / "pytorrent" / "static" / "js"
PREFERENCES = ROOT / "pytorrent" / "services" / "preferences.py"
INDEX = ROOT / "pytorrent" / "templates" / "index.html"
APP = JS_DIR / "app.js"


def source_chunk(filename: str) -> str:
    raw = (JS_DIR / filename).read_text(encoding="utf-8")
    match = re.fullmatch(r"export const \w+ = (.*);\s*", raw, flags=re.S)
    if not match:
        raise AssertionError(f"Cannot decode frontend source module: {filename}")
    return json.loads(match.group(1))


class ProfileStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runtime = source_chunk("runtimeState.js")
        cls.bootstrap = source_chunk("bootstrapRuntime.js")
        cls.selection = source_chunk("profileSelection.js")
        cls.snapshot = source_chunk("initialSnapshot.js")
        cls.preferences = PREFERENCES.read_text(encoding="utf-8")
        cls.index = INDEX.read_text(encoding="utf-8")
        cls.app = APP.read_text(encoding="utf-8")

    def test_server_bootstrap_jinja_is_not_corrupted(self) -> None:
        # Note: Spaced Jinja delimiters leave window.PYTORRENT undefined and break startup preferences.
        bootstrap_line = next(line for line in self.index.splitlines() if "window.PYTORRENT" in line)
        self.assertNotIn("authProvider: { {", bootstrap_line)
        self.assertNotIn("activeProfile: { {", bootstrap_line)
        self.assertNotIn("easterEggEnabled: { {", bootstrap_line)
        self.assertIn("authProvider: {{ auth_provider | tojson }}", bootstrap_line)
        self.assertIn("activeProfile: {{ active_profile.id if active_profile else 'null' }}", bootstrap_line)
        self.assertIn("easterEggEnabled: {{ 1 if prefs and prefs.easter_egg_enabled else 0 }}", bootstrap_line)
        self.assertIn("notificationHistoryEnabled: {{ 1 if prefs and prefs.notification_history_enabled else 0 }}", bootstrap_line)

    def test_missing_bootstrap_config_cannot_start_partial_ui(self) -> None:
        # Note: If server bootstrap data is missing, fail closed instead of running with false default profile/preferences.
        self.assertIn("if(!window.PYTORRENT || typeof window.PYTORRENT !== 'object')", self.app)
        self.assertIn("pyTorrent bootstrap configuration is missing", self.app)

    def test_original_bootstrap_no_profile_order_is_restored(self) -> None:
        # Note: Keep the supplied known-good startup order instead of introducing a second profile-resolution state machine.
        self.assertIn("if(!hasActiveProfile) renderNoProfileState()", self.bootstrap)
        self.assertNotIn("profileSetupCheckPromise", self.runtime)
        self.assertNotIn("resolveStartupProfile", self.selection)
        self.assertNotIn("syncResolvedActiveProfile", self.selection)

    def test_original_first_run_profile_flow_is_restored(self) -> None:
        self.assertIn("async function showFirstRunSetup()", self.selection)
        self.assertIn("window.PYTORRENT.activeProfile=Number(j.active.id)", self.selection)
        self.assertIn("socket.on('profile_required',()=>showFirstRunSetup())", self.snapshot)
        self.assertNotIn("showFirstRunSetup(true)", self.snapshot)

    def test_original_bypass_profile_resolution_is_restored(self) -> None:
        self.assertIn("if auth.auth_bypassed_request() and profiles:", self.preferences)
        self.assertNotIn("len(profiles) > 1", self.preferences)

    def test_easter_egg_bootstrap_remains_server_driven_until_runtime(self) -> None:
        # Note: The loading image is rendered from saved preferences before app.js starts.
        self.assertIn("prefs.easter_egg_enabled and prefs.easter_egg_loading_image_url", self.index)
        self.assertIn("applyInitialLoaderEasterEgg()", source_chunk("sharedUi.js"))


if __name__ == "__main__":
    unittest.main()
