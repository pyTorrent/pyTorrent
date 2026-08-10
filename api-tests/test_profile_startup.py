#!/usr/bin/env python3
"""Regression tests for deterministic profile resolution during application startup."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_DIR = ROOT / "pytorrent" / "static" / "js"
PREFERENCES = ROOT / "pytorrent" / "services" / "preferences.py"
SHARED = ROOT / "pytorrent" / "routes" / "_shared.py"
INDEX = ROOT / "pytorrent" / "templates" / "index.html"


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
        cls.shared = SHARED.read_text(encoding="utf-8")
        cls.index = INDEX.read_text(encoding="utf-8")

    def test_bootstrap_does_not_render_false_no_profile_state(self) -> None:
        # Note: The neutral loader remains visible until profile resolution has completed.
        self.assertNotIn("if(!hasActiveProfile) renderNoProfileState()", self.bootstrap)
        self.assertIn("profileSetupCheckPromise = null", self.runtime)

    def test_no_profile_ui_is_only_rendered_after_resolved_empty_list(self) -> None:
        # Note: A failed or pending profile request must never be interpreted as an empty installation.
        self.assertIn("return {state:profiles.length?'select':'empty',profiles}", self.selection)
        empty_branch = re.search(
            r"if\(resolved\.state==='select'\).*?return;\n\s*}\n\s*setConnectionBadgeState\('reconnecting','setup required'\);(.*?)\n\s*}\s*catch",
            self.selection,
            flags=re.S,
        )
        self.assertIsNotNone(empty_branch)
        self.assertIn("renderNoProfileState()", empty_branch.group(1))
        catch_block = re.search(r"catch\(e\)\{(.*?)\n\s*}\n\s*}\n", self.selection, flags=re.S)
        self.assertIsNotNone(catch_block)
        self.assertNotIn("renderNoProfileState()", catch_block.group(1))

    def test_single_bypass_profile_is_unambiguous(self) -> None:
        # Note: One accessible profile starts normally; only multiple bypass profiles require a picker.
        self.assertIn("if auth.auth_bypassed_request() and len(profiles) > 1:", self.preferences)
        self.assertNotIn("if auth.auth_bypassed_request() and profiles:\n            return None", self.preferences)

    def test_read_api_has_no_hard_coded_profile_one_fallback(self) -> None:
        # Note: HTTP reads and Socket.IO now use the same active-profile resolver.
        self.assertNotIn("auth.can_access_profile(1, user_id)", self.shared)
        self.assertNotIn("preferences.get_profile(1, user_id)", self.shared)
        self.assertIn("profile = preferences.active_profile(user_id)", self.shared)

    def test_socket_profile_required_resynchronizes_profile_room(self) -> None:
        # Note: If Socket.IO reports a missing profile while HTTP already resolved one, the client explicitly rejoins it.
        self.assertIn("socket.on('profile_required',()=>showFirstRunSetup(true))", self.snapshot)
        self.assertIn("socket.emit('select_profile',{profile_id:Number(activeProfileId)})", self.selection)

    def test_topbar_does_not_claim_add_when_profiles_exist(self) -> None:
        self.assertIn("('Select rTorrent' if profiles else 'Add rTorrent')", self.index)


if __name__ == "__main__":
    unittest.main()
