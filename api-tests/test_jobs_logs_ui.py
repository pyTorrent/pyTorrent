#!/usr/bin/env python3
"""Static regression tests for the Jobs and Logs UI refresh behavior."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "pytorrent" / "templates" / "index.html"
STYLES = ROOT / "pytorrent" / "static" / "styles.css"
JS_DIR = ROOT / "pytorrent" / "static" / "js"


def source_chunk(filename: str) -> str:
    """Return the executable source stored in one generated frontend module."""
    raw = (JS_DIR / filename).read_text(encoding="utf-8")
    match = re.fullmatch(r"export const \w+ = (.*);\s*", raw, flags=re.S)
    if not match:
        raise AssertionError(f"Cannot decode frontend source module: {filename}")
    return json.loads(match.group(1))


class JobsLogsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = INDEX.read_text(encoding="utf-8")
        cls.styles = STYLES.read_text(encoding="utf-8")
        cls.jobs = source_chunk("jobTools.js")
        cls.logs = source_chunk("operationLogs.js")
        cls.shared = source_chunk("sharedUi.js")

    def test_existing_controls_and_handlers_are_preserved(self) -> None:
        # Note: Existing IDs are the public contract between the template and current event handlers.
        control_ids = [
            "jobsModal",
            "refreshJobsBtn",
            "clearJobsBtn",
            "emergencyClearJobsBtn",
            "jobsShowDetails",
            "jobsTable",
            "logsModal",
            "refreshOperationLogsBtn",
            "operationLogTypeFilter",
            "operationLogSearch",
            "operationLogHideJobs",
            "operationLogHideAutomations",
            "operationLogShowDetails",
            "operationLogsTable",
            "saveOperationLogRetentionBtn",
            "applyOperationLogRetentionBtn",
            "clearOperationLogsBtn",
        ]
        for control_id in control_ids:
            self.assertEqual(self.index.count(f'id="{control_id}"'), 1, control_id)

        self.assertIn("'/api/jobs/clear'", self.jobs)
        self.assertIn("'/api/jobs/clear?force=1'", self.jobs)
        self.assertIn("'/api/operation-logs/clear'", self.logs)

    def test_records_use_stable_viewports_and_quiet_controls(self) -> None:
        # Note: Fixed record viewports prevent table height changes from moving the rest of the modal.
        self.assertIn('id="jobsTable" class="records-table-viewport"', self.index)
        self.assertIn('id="operationLogsTable" class="records-table-viewport mt-3"', self.index)
        self.assertIn('id="refreshJobsBtn" class="btn btn-sm records-refresh-button"', self.index)
        self.assertIn('id="refreshOperationLogsBtn" class="btn btn-sm records-refresh-button"', self.index)
        self.assertIn('class="dropdown jobs-actions-menu"', self.index)
        self.assertIn("height: clamp(18rem, 52vh, 38rem);", self.styles)
        self.assertIn("scrollbar-gutter: stable both-edges;", self.styles)


    def test_records_empty_states_and_overflow_layout(self) -> None:
        # Note: Empty states stay centered and record wrappers only overflow when table minimum width requires it.
        self.assertIn(".records-table-viewport>.empty-state {", self.styles)
        self.assertIn("min-height: 100%;", self.styles)
        self.assertIn("#jobsTable {", self.styles)
        records_wrapper = re.search(r"\.records-table-viewport>\.responsive-table-wrap \{([^}]*)\}", self.styles, flags=re.S)
        self.assertIsNotNone(records_wrapper)
        self.assertIn("width: 100%;", records_wrapper.group(1))
        self.assertNotIn("width: max-content;", records_wrapper.group(1))

    def test_refresh_keeps_existing_content_visible(self) -> None:
        # Note: Loading feedback must not replace populated Jobs or Logs tables with a spinner.
        self.assertIn("setRecordsRefreshState(box, 'jobsRefreshState'", self.jobs)
        self.assertIn("setRecordsRefreshState(box, 'operationLogsRefreshState'", self.logs)
        self.assertNotIn("replaceHtmlPreserveScroll(box, '<span class=\"spinner-border spinner-border-sm\"></span> Loading jobs...')", self.jobs)
        self.assertNotIn("replaceHtmlPreserveScroll(box, '<span class=\"spinner-border spinner-border-sm\"></span> Loading logs...')", self.logs)
        self.assertIn("Refresh feedback lives outside the table", self.shared)

    def test_stale_async_results_are_ignored(self) -> None:
        # Note: Request sequence guards keep older responses from overwriting newer state or filters.
        self.assertIn("jobsRequestSequence", self.jobs)
        self.assertIn("requestGeneration !== profileViewGeneration", self.jobs)
        self.assertIn("operationLogsRequestSequence", self.logs)
        self.assertIn("requestGeneration !== profileViewGeneration", self.logs)

    def test_jobs_detail_toggle_uses_cached_rows(self) -> None:
        # Note: Show-details is presentation-only and should not create another network refresh.
        detail_events = re.findall(r"^.*jobsShowDetails.*addEventListener.*$", self.jobs, flags=re.M)
        self.assertEqual(len(detail_events), 1)
        self.assertIn("jobsLastData", detail_events[0])
        self.assertIn("renderJobsTable", detail_events[0])
        self.assertNotIn("loadJobs", detail_events[0])

    def test_new_css_rules_are_defined_once(self) -> None:
        # Note: Shared Jobs/Logs selectors must stay centralized instead of accumulating overrides.
        selectors = [
            ".nav-btn-quiet {",
            ".records-modal-body {",
            ".records-toolbar {",
            ".records-refresh-button {",
            ".records-refresh-state {",
            ".records-table-viewport {",
            ".records-pager {",
            ".job-row-action {",
        ]
        for selector in selectors:
            self.assertEqual(self.styles.count(selector), 1, selector)


if __name__ == "__main__":
    unittest.main()
