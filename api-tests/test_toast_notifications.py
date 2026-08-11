import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JS_DIR = ROOT / "pytorrent" / "static" / "js"
STYLES = ROOT / "pytorrent" / "static" / "styles.css"
INDEX = ROOT / "pytorrent" / "templates" / "index.html"
PREFERENCES = ROOT / "pytorrent" / "services" / "preferences.py"
SCHEMA = ROOT / "schema.sql"
MIGRATION = ROOT / "migrations" / "0011_notification_history_mode.sql"
TOAST_MODE_MIGRATION = ROOT / "migrations" / "0012_toast_notification_mode.sql"


def source_chunk(filename: str) -> str:
    # Note: Frontend modules export runtime code as a JSON string, so tests decode the original source before assertions.
    text = (JS_DIR / filename).read_text(encoding="utf-8")
    match = re.search(r"=\s*(\"(?:\\.|[^\"\\])*\")\s*;\s*$", text, flags=re.S)
    if not match:
        raise AssertionError(f"Cannot decode frontend source module: {filename}")
    return json.loads(match.group(1))


class ToastNotificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.shared = source_chunk("sharedUi.js")
        cls.messages = source_chunk("messages.js")
        cls.notifications = source_chunk("notificationCenter.js")
        cls.runtime = source_chunk("runtimeState.js")
        cls.styles = STYLES.read_text(encoding="utf-8")
        cls.index = INDEX.read_text(encoding="utf-8")
        cls.preferences = PREFERENCES.read_text(encoding="utf-8")
        cls.schema = SCHEMA.read_text(encoding="utf-8")
        cls.migration = MIGRATION.read_text(encoding="utf-8")
        cls.toast_mode_migration = TOAST_MODE_MIGRATION.read_text(encoding="utf-8")
        cls.action_state = source_chunk("torrentActionState.js")
        cls.snapshot = source_chunk("initialSnapshot.js")

    def test_existing_toast_api_and_history_are_preserved(self) -> None:
        # Note: Existing callers keep the same public Toast signature and history integration after the visual upgrade.
        self.assertIn('function toast(msg, type="secondary", options={})', self.shared)
        self.assertIn("rememberToastNotification(text,meta.type,options)", self.shared)
        self.assertIn("function toastMessage(key, type = 'secondary', params = {}, options = {})", self.messages)
        self.assertIn("toastGroups.get(key)", self.shared)


    def test_toasts_scale_with_interface_scale(self) -> None:
        # Note: Toast geometry and typography must follow the same --ui-scale variable as the main interface.
        self.assertIn("top: calc(var(--topbar) + (12px * var(--ui-scale)));", self.styles)
        self.assertIn("width: min(calc(400px * var(--ui-scale))", self.styles)
        self.assertIn("font-size: calc(13px * var(--ui-scale));", self.styles)
        self.assertIn("height: max(2px, calc(3px * var(--ui-scale)));", self.styles)

    def test_notification_history_supports_important_only_and_all_modes(self) -> None:
        # Note: Important-only filters the archive without suppressing visible Toasts; All preserves the prior archive behavior.
        self.assertIn("let notificationHistoryMode = window.PYTORRENT?.notificationHistoryMode === 'all' ? 'all' : 'important';", self.runtime)
        self.assertIn("function shouldRememberToastNotification(type,options={})", self.notifications)
        self.assertIn("toastNotificationImportance(type,options)==='important'", self.notifications)
        self.assertIn("return String(type||'secondary').toLowerCase()==='danger' ? 'important' : 'routine';", self.notifications)
        self.assertIn('id="notificationHistoryMode"', self.index)
        self.assertIn('value="important"', self.index)
        self.assertIn('value="all"', self.index)

    def test_important_actions_are_explicitly_classified(self) -> None:
        # Note: Destructive/data-moving torrent actions are promoted centrally while routine start/stop/recheck Toasts remain transient.
        self.assertIn("const IMPORTANT_TORRENT_ACTIONS = new Set(['remove','erase','delete','move','profile_transfer','recreate_files','set_limits']);", self.shared)
        self.assertIn("function toastHistoryOptionsForAction(action)", self.shared)
        self.assertNotIn("'start','pause','stop','resume'", self.shared)

    def test_history_mode_is_persisted_and_migrated_safely(self) -> None:
        # Note: New installs default to important-only; existing enabled histories retain All until the user changes the setting.
        self.assertIn("notification_history_mode TEXT DEFAULT 'important'", self.schema)
        self.assertIn('notification_history_mode = data.get("notification_history_mode")', self.preferences)
        self.assertIn('history_mode not in {"important", "all"}', self.preferences)
        self.assertIn("ALTER TABLE user_preferences ADD COLUMN notification_history_mode TEXT DEFAULT 'important';", self.migration)
        self.assertIn("UPDATE user_preferences SET notification_history_mode='all' WHERE notification_history_enabled=1;", self.migration)

    def test_toast_countdown_pauses_for_pointer_and_keyboard_focus(self) -> None:
        # Note: Hover and focus must pause both the timer and visual progress instead of only freezing the animation.
        self.assertIn("function pauseToastCountdown(entry)", self.shared)
        self.assertIn("function resumeToastCountdown(entry, key)", self.shared)
        self.assertIn("addEventListener('mouseenter'", self.shared)
        self.assertIn("addEventListener('mouseleave'", self.shared)
        self.assertIn("addEventListener('focusin'", self.shared)
        self.assertIn("addEventListener('focusout'", self.shared)
        self.assertIn("entry.remaining=Math.max(0,entry.remaining-elapsed)", self.shared)

    def test_toast_markup_has_readable_structure_and_manual_dismiss(self) -> None:
        # Note: Every Toast exposes a type icon, status title, message, repeat counter, close action and countdown bar.
        for class_name in (
            "toast-icon",
            "toast-title",
            "toast-message",
            "toast-count",
            "toast-dismiss",
            "toast-progress-bar",
        ):
            self.assertIn(class_name, self.shared)
        self.assertIn("Dismiss notification", self.shared)
        self.assertIn("aria-live", self.shared)

    def test_toast_css_is_single_source_and_motion_aware(self) -> None:
        # Note: Core Toast selectors stay single-source while state modifiers and accessibility rules remain separate.
        for selector in (
            ".toast-host {",
            ".toast-item {",
            ".toast-message {",
            ".toast-count {",
            ".toast-progress {",
            ".toast-progress-bar {",
        ):
            count = len(re.findall(rf"(?m)^\s*{re.escape(selector)}\s*$", self.styles))
            self.assertEqual(count, 1, selector)
        self.assertIn("@keyframes toast-countdown", self.styles)
        self.assertIn("@keyframes toast-leave", self.styles)
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.styles)

    def test_shared_messages_are_complete_action_sentences(self) -> None:
        # Note: Reusable messages describe the required action or completed outcome instead of relying on terse labels.
        self.assertIn("Select at least one torrent before running this action.", self.messages)
        self.assertIn("was queued for background processing.", self.messages)
        self.assertIn("completed successfully.", self.messages)
        self.assertNotIn("`${actionLabel(action)} done`", self.messages)

    def test_visible_toast_filter_is_independent_and_persisted(self) -> None:
        # Note: Visible Toast quiet mode does not reuse the history switch, so users can configure both surfaces independently.
        self.assertIn("let toastNotificationMode = window.PYTORRENT?.toastNotificationMode === 'important' ? 'important' : 'all';", self.runtime)
        self.assertIn("function shouldShowToastNotification(type,options={})", self.shared)
        self.assertIn("toastDisplayImportance(type,options)==='important'", self.shared)
        self.assertIn('id="toastNotificationMode"', self.index)
        self.assertIn("toast_notification_mode TEXT DEFAULT 'all'", self.schema)
        self.assertIn('toast_notification_mode = data.get("toast_notification_mode")', self.preferences)
        self.assertIn("ALTER TABLE user_preferences ADD COLUMN toast_notification_mode TEXT DEFAULT 'all';", self.toast_mode_migration)

    def test_job_lifecycle_reuses_one_stable_toast(self) -> None:
        # Note: A multi-part operation maps every job ID to one stable Toast key and replaces its content instead of stacking cards.
        self.assertIn("const jobToastCycles = new Map();", self.runtime)
        self.assertIn("const jobToastCycleByJobId = new Map();", self.runtime)
        self.assertIn("function registerJobToastCycle", self.action_state)
        self.assertIn("key:cycle?.toastKey,replace:true", self.action_state)
        self.assertIn("function updateJobToastLifecycle", self.action_state)
        self.assertIn("options?.key||toastKey", self.shared)
        self.assertIn("options?.replace!==true", self.shared)
        self.assertNotIn("toastMessage('toast.operationStarted','secondary'", self.snapshot)

    def test_retrying_job_does_not_emit_terminal_failure_toast(self) -> None:
        # Note: Retried worker failures update the same lifecycle Toast as retrying instead of claiming the whole operation failed.
        workers = (ROOT / "pytorrent" / "services" / "workers.py").read_text(encoding="utf-8")
        self.assertIn('"retrying": status == "pending"', workers)
        self.assertIn("if(msg?.retrying)", self.snapshot)
        self.assertIn("toast.operationRetrying", self.snapshot)

    def test_manual_background_jobs_join_the_same_toast_lifecycle(self) -> None:
        # Note: Smart Queue checks and speed-limit changes return job IDs and must reuse the queued Toast instead of adding a completion Toast.
        smart_events = source_chunk("smartQueueEvents.js")
        speed_controls = source_chunk("speedLimitControls.js")
        self.assertIn("markQueuedJobs(j,[],'smart_queue_check')", smart_events)
        self.assertIn("jobToastOptions(cycle,{remember:false})", smart_events)
        self.assertIn("markQueuedJobs(j,[],'set_limits')", speed_controls)
        self.assertIn("jobToastOptions(cycle,{remember:false})", speed_controls)
        self.assertIn("cycle.action==='smart_queue_check'", self.action_state)
        self.assertIn("smartQueueToastMessage(result)", self.action_state)

    def test_automatic_job_successes_do_not_duplicate_aggregate_toasts(self) -> None:
        # Note: Automation, RSS and ratio rules own aggregate/domain feedback, while their low-level retries and failures remain eligible for Toasts.
        self.assertIn("if(source==='automation') return automationToastsEnabled && (phase==='failed' || phase==='retrying');", self.shared)
        self.assertIn("if(source==='rss' || source==='ratio') return phase==='failed' || phase==='retrying';", self.shared)
        self.assertIn("shouldShowOperationToast(msg,'started')", self.snapshot)
        self.assertIn("shouldShowOperationToast(msg,'finished')", self.snapshot)
        self.assertIn("shouldShowOperationToast(msg,msg?.retrying?'retrying':'failed')", self.snapshot)


if __name__ == "__main__":
    unittest.main()
