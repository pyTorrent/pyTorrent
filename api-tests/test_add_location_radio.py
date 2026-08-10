#!/usr/bin/env python3
"""Regression tests for the Add modal default-location radio action."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "pytorrent" / "templates" / "index.html"
JS_DIR = ROOT / "pytorrent" / "static" / "js"


def source_chunk(filename: str) -> str:
    """Return the executable source stored in one generated frontend module."""
    raw = (JS_DIR / filename).read_text(encoding="utf-8")
    match = re.fullmatch(r"export const \w+ = (.*);\s*", raw, flags=re.S)
    if not match:
        raise AssertionError(f"Cannot decode frontend source module: {filename}")
    return json.loads(match.group(1))


class AddLocationRadioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = INDEX.read_text(encoding="utf-8")
        cls.path_tools = source_chunk("pathPickerTools.js")
        cls.preferences = source_chunk("preferencesTools.js")
        cls.events = source_chunk("preferenceEvents.js")
        cls.torrent_add = source_chunk("torrentAdd.js")

    def test_radio_restores_persisted_remember_last_state(self) -> None:
        # Note: The radio must reflect the saved state instead of flashing or resetting to unchecked.
        self.assertIn('id="rememberAddPathDefaultRadio"', self.index)
        self.assertIn("prefs.download_remember_last_enabled and prefs.download_location_mode == 'remember_last'", self.index)
        expected = "checked=downloadLocationMode==='remember_last' && !!downloadRememberLastEnabled"
        self.assertIn(expected, self.path_tools)
        self.assertIn(expected, self.preferences)

    def test_radio_saves_default_and_remember_last_together(self) -> None:
        # Note: The Add action preserves the former remember-last behavior while explicitly saving the same default path.
        save_block = re.search(
            r"async function saveAddPathDefaultPreference\(\)\{(.*?)\n  \}\n  async function applyDefaultDownloadPath",
            self.path_tools,
            flags=re.S,
        )
        self.assertIsNotNone(save_block)
        body = save_block.group(1)
        self.assertIn("default_download_path: currentPath", body)
        self.assertIn("download_location_mode: 'remember_last'", body)
        self.assertIn("download_remember_last_enabled: true", body)
        self.assertIn("download_last_path: currentPath", body)
        self.assertIn("action.checked=true", body)
        self.assertNotIn("action.checked=false", body)

    def test_already_selected_radio_can_save_again(self) -> None:
        # Note: A click handler lets the one-way radio action run again after the save path changes.
        self.assertIn("addEventListener('click',saveAddPathDefaultPreference)", self.events)
        self.assertNotIn("addEventListener('change',saveAddPathDefaultPreference)", self.events)

    def test_add_form_no_longer_forces_radio_unchecked(self) -> None:
        # Note: Clearing, submitting or reopening Add must not erase the persisted radio state.
        self.assertNotIn("$('rememberAddPathDefaultRadio').checked=false", self.torrent_add)
        self.assertNotIn("show.bs.modal',()=>{ if($('rememberAddPathDefaultRadio'))", self.torrent_add)


if __name__ == "__main__":
    unittest.main()
