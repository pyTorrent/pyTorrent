from __future__ import annotations

import ast
from pathlib import Path
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
WS_PATH = ROOT / "pytorrent/services/websocket.py"
INITIAL_SNAPSHOT_PATH = ROOT / "pytorrent/static/js/initialSnapshot.js"
SYSTEM_STATS_PATH = ROOT / "pytorrent/static/js/systemStatsSocket.js"


def _load_helper(name: str, extra_globals: dict | None = None):
    source = WS_PATH.read_text()
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = dict(extra_globals or {})
    exec(compile(module, str(WS_PATH), "exec"), namespace)
    return namespace[name]


class WebsocketOptimizationTests(unittest.TestCase):
    def test_noop_live_patch_is_suppressed(self):
        build = _load_helper("_live_patch_payload")
        self.assertIsNone(build(2, {"ok": True, "updated": [], "requires_full_refresh": False}))

    def test_live_patch_contains_only_torrent_delta_fields(self):
        build = _load_helper("_live_patch_payload")
        payload = build(2, {
            "ok": True,
            "updated": [{"hash": "abc", "up_rate": 12}],
            "requires_full_refresh": False,
            "speed_status": {"up_rate": 999},
        })
        self.assertEqual(payload, {
            "ok": True,
            "profile_id": 2,
            "updated": [{"hash": "abc", "up_rate": 12}],
            "requires_full_refresh": False,
        })
        self.assertNotIn("speed_status", payload)

    def test_full_refresh_signal_is_not_suppressed(self):
        build = _load_helper("_live_patch_payload")
        payload = build(2, {"updated": [], "requires_full_refresh": True})
        self.assertEqual(payload["updated"], [])
        self.assertTrue(payload["requires_full_refresh"])

    def test_live_aggregate_scans_rates_and_activity_together(self):
        recorded = []
        fake_rtorrent = types.SimpleNamespace(human_rate=lambda value: f"{value} B/s")
        fake_peaks = types.SimpleNamespace(record=lambda pid, down, up: recorded.append((pid, down, up)) or {"ok": True})
        aggregate = _load_helper(
            "_live_state_from_rows",
            {"rtorrent": fake_rtorrent, "speed_peaks": fake_peaks},
        )
        active, status = aggregate(7, [
            {"state": 1, "down_rate": 10, "up_rate": 2},
            {"state": 0, "down_rate": 5, "up_rate": 3},
            {"state": 1, "down_rate": 0, "up_rate": 0},
        ])
        self.assertTrue(active)
        self.assertEqual(status["down_rate"], 15)
        self.assertEqual(status["up_rate"], 5)
        self.assertEqual(recorded, [(7, 15, 5)])

    def test_frontend_accepts_dedicated_speed_event(self):
        source = INITIAL_SNAPSHOT_PATH.read_text()
        self.assertIn("socket.on('speed_status'", source)
        self.assertIn("applyLiveSpeedStats(msg)", source)

    def test_system_stats_does_not_render_peaks_twice(self):
        source = SYSTEM_STATS_PATH.read_text()
        self.assertNotIn("updateSpeedPeaks", source)
        self.assertEqual(source.count("applyLiveSpeedStats(s)"), 1)


if __name__ == "__main__":
    unittest.main()
