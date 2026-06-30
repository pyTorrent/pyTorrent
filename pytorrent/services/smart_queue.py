from __future__ import annotations
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any
import json
import os
import time
from ..config import BASE_DIR, SMART_QUEUE_LABEL, SMART_QUEUE_STALLED_LABEL
from ..db import connect, default_user_id, utcnow
from . import rtorrent
from .preferences import active_profile, get_profile


SMART_QUEUE_START_BATCH_SIZE = 40
SMART_QUEUE_START_BATCH_PAUSE_SECONDS = 0.75
SMART_QUEUE_START_VERIFY_ATTEMPTS = 30
SMART_QUEUE_START_VERIFY_DELAY_SECONDS = 2.0
SMART_QUEUE_DIAGNOSTICS_LOG = BASE_DIR / 'data' / 'smart_queue.log'
SMART_QUEUE_DIAGNOSTICS_MAX_ITEMS = 200


def _diagnostics_mode() -> str:
    raw = os.getenv('PYTORRENT_SMART_QUEUE_DIAGNOSTICS', 'none').strip().lower()
    aliases = {
        '': 'none',
        '0': 'none',
        'false': 'none',
        'off': 'none',
        'disabled': 'none',
        'debbug': 'debug',
        'full': 'debug',
        '1': 'debug',
        'true': 'debug',
        'yes': 'debug',
        'on': 'debug',
    }
    mode = aliases.get(raw, raw)
    return mode if mode in {'none', 'short', 'debug'} else 'none'


def _diagnostics_max_items() -> int:
    try:
        return max(1, int(os.getenv('PYTORRENT_SMART_QUEUE_DIAGNOSTICS_MAX_ITEMS', str(SMART_QUEUE_DIAGNOSTICS_MAX_ITEMS))))
    except (TypeError, ValueError):
        return SMART_QUEUE_DIAGNOSTICS_MAX_ITEMS


def _diagnostics_sample(items: list[Any] | tuple[Any, ...] | set[Any], limit: int | None = None) -> list[Any]:
    max_items = _diagnostics_max_items() if limit is None else max(1, int(limit))
    return list(items)[:max_items]


def _diagnostics_torrent(t: dict[str, Any] | None) -> dict[str, Any]:
    if not t:
        return {}
    return {
        'hash': str(t.get('hash') or ''),
        'name': str(t.get('name') or ''),
        'state': int(t.get('state') or 0),
        'active': int(t.get('active') or 0),
        'complete': int(t.get('complete') or 0),
        'status': str(t.get('status') or ''),
        'paused': bool(t.get('paused')),
        'hashing': int(t.get('hashing') or 0),
        'priority': int(t.get('priority') or 0),
        'down_rate': down_rate,
        'effective_down_rate': down_rate if effective_down_rate is None else max(0, int(effective_down_rate or 0)),
        'up_rate': int(t.get('up_rate') or 0),
        'last_activity': int(t.get('last_activity') or 0),
        'peers': int(t.get('peers') or 0),
        'seeds': int(t.get('seeds') or 0),
        'label': str(t.get('label') or ''),
        'message': str(t.get('message') or ''),
    }


def _diagnostics_torrents(torrents: list[dict[str, Any]], limit: int | None = None) -> list[dict[str, Any]]:
    return [_diagnostics_torrent(t) for t in _diagnostics_sample(torrents, limit)]


def _pending_reason_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(item.get('pending_reason') or 'unknown') for item in items))


def _hash_sample(values: list[str] | set[str], limit: int = 100) -> list[str]:
    """Return a bounded hash list for UI logs without storing oversized diagnostics."""
    return [str(v) for v in list(values)[:max(1, int(limit))] if str(v)]


def _decision_text(stopped: int, started: int, stalled_detected: int, stalled_stopped: int, protected_stalled: int) -> str:
    """Build a compact Smart Queue decision label for the history table."""
    parts = [f"stopped {stopped}", f"started {started}"]
    if stalled_detected:
        stalled_part = f"stalled {stalled_stopped}/{stalled_detected} stopped"
        if protected_stalled:
            stalled_part += f", {protected_stalled} protected"
        parts.append(stalled_part)
    return "; ".join(parts)


def _diagnostics_write(event: str, summary: dict[str, Any], debug: dict[str, Any] | None = None) -> None:
    mode = _diagnostics_mode()
    if mode == 'none':
        return
    payload: dict[str, Any] = {
        'timestamp': utcnow(),
        'event': event,
        'mode': mode,
        **summary,
    }
    if mode == 'debug' and debug:
        payload['debug'] = debug
    try:
        SMART_QUEUE_DIAGNOSTICS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with SMART_QUEUE_DIAGNOSTICS_LOG.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True) + '\n')
    except Exception:
        # Diagnostics must never break Smart Queue execution.
        return


def _ts(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except Exception:
        return 0.0


def _int_setting(data: dict[str, Any], current: dict[str, Any], key: str, default: int, minimum: int = 0) -> int:
    raw = data.get(key) if key in data else current.get(key)
    try:
        return max(minimum, int(raw if raw is not None and raw != '' else default))
    except (TypeError, ValueError):
        return max(minimum, int(default))


def _default_settings(profile_id: int) -> dict[str, Any]:
    return {
        'profile_id': profile_id,
        'enabled': 0,
        'max_active_downloads': 5,
        'stalled_seconds': 300,
        'min_speed_bytes': 1024,
        'min_seeds': 1,
        'min_peers': 0,
        'ignore_seed_peer': 0,
        'ignore_speed': 0,
        'manage_stopped': 1,
        'cooldown_minutes': 10,
        'last_run_at': None,
        'refill_enabled': 1,
        'refill_interval_minutes': 0,
        'last_refill_at': None,
        'surge_refill_enabled': 0,
        'surge_refill_interval_minutes': 1440,
        'surge_refill_batch_size': 2000,
        'last_surge_refill_at': None,
        'stop_batch_size': 50,
        'start_grace_seconds': 900,
        'protect_active_below_cap': 1,
        'prefer_partial_progress': 1,
        'auto_stop_idle': 0,
        'updated_at': utcnow(),
    }


def get_settings(profile_id: int, user_id: int | None = None) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            'SELECT * FROM smart_queue_settings WHERE profile_id=?',
            (profile_id,),
        ).fetchone()
    settings = dict(row or _default_settings(profile_id))
    return settings


def save_settings(profile_id: int, data: dict[str, Any], user_id: int | None = None) -> dict[str, Any]:
    current = get_settings(profile_id, user_id)
    settings = {
        'enabled': 1 if data.get('enabled', current.get('enabled')) else 0,
        'max_active_downloads': _int_setting(data, current, 'max_active_downloads', 5, 1),
        'stalled_seconds': _int_setting(data, current, 'stalled_seconds', 300, 30),
        'min_speed_bytes': _int_setting(data, current, 'min_speed_bytes', 0, 0),
        'min_seeds': _int_setting(data, current, 'min_seeds', 0, 0),
        # Note: Min peers is optional; when set, stalled detection requires low speed, low seeds and low peers.
        'min_peers': _int_setting(data, current, 'min_peers', 0, 0),
        # Note: Ignore seed/peer removes source counts from stalled detection; start attempts do not rely on stale source counts.
        'ignore_seed_peer': 1 if data.get('ignore_seed_peer', current.get('ignore_seed_peer')) else 0,
        # Note: Ignore speed removes low transfer rate from stalled detection; with both ignores enabled only the stalled timer matters.
        'ignore_speed': 1 if data.get('ignore_speed', current.get('ignore_speed')) else 0,
        # Note: Compatibility field retained; enabled Smart Queue always manages stopped torrents and never manages user-paused torrents.
        'manage_stopped': 1,
        # Note: User-visible cooldown limits noisy Smart Queue runs while manual checks can still force execution.
        'cooldown_minutes': _int_setting(data, current, 'cooldown_minutes', 10, 1),
        # Note: Limits one Smart Queue pass from stopping too many stalled items at once.
        'stop_batch_size': _int_setting(data, current, 'stop_batch_size', 50, 1),
        # Note: Newly queue-started torrents are protected from stalled detection while rTorrent and trackers settle.
        'start_grace_seconds': _int_setting(data, current, 'start_grace_seconds', 900, 0),
        # Note: When below the target cap, prefer refilling first instead of reducing active slots by stopping stalled downloads.
        'protect_active_below_cap': 1 if data.get('protect_active_below_cap', current.get('protect_active_below_cap', 1)) else 0,
        # Note: Prefer partially downloaded stopped torrents so Smart Queue finishes existing work before opening fresh downloads.
        'prefer_partial_progress': 1 if data.get('prefer_partial_progress', current.get('prefer_partial_progress', 1)) else 0,
        # Note: Optional safety valve that disables Smart Queue when there are no active or waiting downloads to manage.
        'auto_stop_idle': 1 if data.get('auto_stop_idle', current.get('auto_stop_idle', 0)) else 0,
    }
    refill_mode = str(data.get('refill_mode') or '').strip().lower()
    if refill_mode not in {'auto', 'custom', 'off'}:
        if not int(current.get('refill_enabled') or 0):
            refill_mode = 'off'
        elif int(current.get('refill_interval_minutes') or 0) > 0:
            refill_mode = 'custom'
        else:
            refill_mode = 'auto'
    # Note: Refill can be disabled, use the existing poller cadence, or run on a user-defined minute interval.
    settings['refill_enabled'] = 0 if refill_mode == 'off' else 1
    settings['refill_interval_minutes'] = _int_setting(data, current, 'refill_interval_minutes', 5, 1) if refill_mode == 'custom' else 0
    # Note: Surge refill is a separate periodic over-cap starter; it never changes the normal target limit.
    settings['surge_refill_enabled'] = 1 if data.get('surge_refill_enabled', current.get('surge_refill_enabled', 0)) else 0
    settings['surge_refill_interval_minutes'] = _int_setting(data, current, 'surge_refill_interval_minutes', 1440, 1)
    settings['surge_refill_batch_size'] = _int_setting(data, current, 'surge_refill_batch_size', 2000, 1)
    now = utcnow()
    with connect() as conn:
        conn.execute(
            '''INSERT INTO smart_queue_settings(profile_id,enabled,max_active_downloads,stalled_seconds,min_speed_bytes,min_seeds,min_peers,ignore_seed_peer,ignore_speed,manage_stopped,cooldown_minutes,stop_batch_size,start_grace_seconds,protect_active_below_cap,prefer_partial_progress,auto_stop_idle,refill_enabled,refill_interval_minutes,surge_refill_enabled,surge_refill_interval_minutes,surge_refill_batch_size,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(profile_id) DO UPDATE SET
               enabled=excluded.enabled,
               max_active_downloads=excluded.max_active_downloads,
               stalled_seconds=excluded.stalled_seconds,
               min_speed_bytes=excluded.min_speed_bytes,
               min_seeds=excluded.min_seeds,
               min_peers=excluded.min_peers,
               ignore_seed_peer=excluded.ignore_seed_peer,
               ignore_speed=excluded.ignore_speed,
               manage_stopped=excluded.manage_stopped,
               cooldown_minutes=excluded.cooldown_minutes,
               stop_batch_size=excluded.stop_batch_size,
               start_grace_seconds=excluded.start_grace_seconds,
               protect_active_below_cap=excluded.protect_active_below_cap,
               prefer_partial_progress=excluded.prefer_partial_progress,
               auto_stop_idle=excluded.auto_stop_idle,
               refill_enabled=excluded.refill_enabled,
               refill_interval_minutes=excluded.refill_interval_minutes,
               surge_refill_enabled=excluded.surge_refill_enabled,
               surge_refill_interval_minutes=excluded.surge_refill_interval_minutes,
               surge_refill_batch_size=excluded.surge_refill_batch_size,
               updated_at=excluded.updated_at''',
            (profile_id, settings['enabled'], settings['max_active_downloads'], settings['stalled_seconds'], settings['min_speed_bytes'], settings['min_seeds'], settings['min_peers'], settings['ignore_seed_peer'], settings['ignore_speed'], settings['manage_stopped'], settings['cooldown_minutes'], settings['stop_batch_size'], settings['start_grace_seconds'], settings['protect_active_below_cap'], settings['prefer_partial_progress'], settings['auto_stop_idle'], settings['refill_enabled'], settings['refill_interval_minutes'], settings['surge_refill_enabled'], settings['surge_refill_interval_minutes'], settings['surge_refill_batch_size'], now),
        )
    return get_settings(profile_id, user_id)


def list_exclusions(profile_id: int, user_id: int | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        return conn.execute(
            'SELECT * FROM smart_queue_exclusions WHERE profile_id=? ORDER BY created_at DESC',
            (profile_id,),
        ).fetchall()


def set_exclusion(profile_id: int, torrent_hash: str, excluded: bool, reason: str = '', user_id: int | None = None) -> None:
    now = utcnow()
    with connect() as conn:
        if excluded:
            conn.execute(
                'INSERT OR REPLACE INTO smart_queue_exclusions(profile_id,torrent_hash,reason,created_at) VALUES(?,?,?,?)',
                (profile_id, torrent_hash, reason, now),
            )
        else:
            conn.execute(
                'DELETE FROM smart_queue_exclusions WHERE profile_id=? AND torrent_hash=?',
                (profile_id, torrent_hash),
            )



def add_history(profile_id: int, event: str, paused: list[str] | None = None, resumed: list[str] | None = None, checked: int = 0, details: dict[str, Any] | None = None, user_id: int | None = None) -> None:
    paused = paused or []
    resumed = resumed or []
    details = details or {}
    with connect() as conn:
        conn.execute(
            'INSERT INTO smart_queue_history(profile_id,event,paused_count,resumed_count,checked_count,details_json,created_at) VALUES(?,?,?,?,?,?,?)',
            (profile_id, event, len(paused), len(resumed), int(checked or 0), json.dumps({**details, 'paused': paused, 'resumed': resumed}), utcnow()),
        )

def list_history(profile_id: int, user_id: int | None = None, limit: int = 30) -> list[dict[str, Any]]:
    with connect() as conn:
        return conn.execute(
            'SELECT * FROM smart_queue_history WHERE profile_id=? ORDER BY created_at DESC LIMIT ?',
            (profile_id, max(1, min(int(limit or 30), 100))),
        ).fetchall()


def clear_history(profile_id: int, user_id: int | None = None) -> int:
    """Delete Smart Queue history rows for the current profile and return the removed count."""
    # Note: Manual cleanup only removes audit history; settings, exclusions and pending queue state stay untouched.
    with connect() as conn:
        row = conn.execute(
            'SELECT COUNT(*) AS count FROM smart_queue_history WHERE profile_id=?',
            (profile_id,),
        ).fetchone()
        count = int((row or {}).get('count') or 0)
        conn.execute(
            'DELETE FROM smart_queue_history WHERE profile_id=?',
            (profile_id,),
        )
    return count


def count_history(profile_id: int, user_id: int | None = None) -> int:
    with connect() as conn:
        row = conn.execute(
            'SELECT COUNT(*) AS count FROM smart_queue_history WHERE profile_id=?',
            (profile_id,),
        ).fetchone()
    return int((row or {}).get('count') or 0)


def _latest_history_event(profile_id: int, user_id: int | None = None) -> str:
    """Return the newest Smart Queue history event for duplicate suppression."""
    # Note: Disabled Smart Queue should leave one waiting marker, not a poller-generated log stream.
    with connect() as conn:
        row = conn.execute(
            'SELECT event FROM smart_queue_history WHERE profile_id=? ORDER BY created_at DESC LIMIT 1',
            (profile_id,),
        ).fetchone()
    return str((row or {}).get('event') or '')


def _record_disabled_waiting_once(profile_id: int, user_id: int, details: dict[str, Any] | None = None) -> bool:
    """Record one disabled-state history row until Smart Queue runs or changes state again."""
    # Note: This keeps the UI audit trail useful without creating repeated disabled logs on every poll.
    if _latest_history_event(profile_id, user_id) in {'disabled_waiting_start', 'auto_stopped_idle'}:
        return False
    payload = {
        'decision': 'Smart Queue disabled, waiting for start',
        'enabled': False,
        **(details or {}),
    }
    add_history(profile_id, 'disabled_waiting_start', [], [], 0, payload, user_id)
    return True


def _excluded_hashes(profile_id: int, user_id: int | None = None) -> set[str]:
    return {r['torrent_hash'] for r in list_exclusions(profile_id)}



def _label_names(value: str | None) -> list[str]:
    names: list[str] = []
    for part in str(value or '').replace(';', ',').replace('|', ',').split(','):
        label = part.strip()
        if label and label not in names:
            names.append(label)
    return names


def _label_value(labels: list[str]) -> str:
    output: list[str] = []
    for label in labels:
        item = str(label or '').strip()
        if item and item not in output:
            output.append(item)
    return ', '.join(output)


def _has_smart_queue_label(value: str | None) -> bool:
    return SMART_QUEUE_LABEL in _label_names(value)


def _without_smart_queue_label(value: str | None) -> str:
    return _label_value([label for label in _label_names(value) if label != SMART_QUEUE_LABEL])


def _smart_queue_label_cleanup_value(live_label: str | None, previous_label: str | None = None) -> str:
    """Return label value with only the Smart Queue technical marker removed.

    User labels present in rTorrent are preserved. The previous-label fallback is used only
    when the live value contains no user label after removing the technical marker, which
    protects torrents that were labeled by older builds that overwrote custom1.
    """
    live_user_labels = [label for label in _label_names(live_label) if label != SMART_QUEUE_LABEL]
    if live_user_labels:
        return _label_value(live_user_labels)
    previous_user_labels = [label for label in _label_names(previous_label) if label != SMART_QUEUE_LABEL]
    return _label_value(previous_user_labels)


def _has_stalled_label(value: str | None) -> bool:
    # Note: Stalled is an exact technical label; lower-case variants are normal user labels.
    return SMART_QUEUE_STALLED_LABEL in _label_names(value)


def _without_queue_technical_labels(value: str | None) -> str:
    return _label_value([label for label in _label_names(value) if label != SMART_QUEUE_LABEL])


def _ensure_stalled_label(client: Any, torrent_hash: str, current_label: str = '') -> bool:
    labels = [label for label in _label_names(current_label) if label != SMART_QUEUE_LABEL]
    changed = False
    if SMART_QUEUE_STALLED_LABEL not in labels:
        labels.append(SMART_QUEUE_STALLED_LABEL)
        changed = True
    if SMART_QUEUE_LABEL in _label_names(current_label):
        changed = True
    if not changed:
        return True
    try:
        # Note: Stalled marking is idempotent; it adds Stalled and removes only the Smart Queue technical marker.
        client.call('d.custom1.set', torrent_hash, _label_value(labels))
        return True
    except Exception:
        return False


def _without_stalled_label(value: str | None) -> str:
    """Return labels without Smart Queue's Stalled marker."""
    # Note: This keeps user labels intact while clearing only the automatic stalled state.
    return _label_value([label for label in _label_names(value) if label != SMART_QUEUE_STALLED_LABEL])


def _clear_stalled_label(client: Any, torrent_hash: str, current_label: str = '') -> bool:
    """Remove the Stalled marker from a torrent that is active again."""
    labels = _label_names(current_label)
    if SMART_QUEUE_STALLED_LABEL not in labels:
        return False
    try:
        # Note: Active downloads must not keep the Stalled marker after they resume transferring.
        client.call('d.custom1.set', torrent_hash, _without_stalled_label(current_label))
        return True
    except Exception:
        return False



def _remember_auto_label(profile_id: int, torrent_hash: str, previous_label: str) -> None:
    now = utcnow()
    with connect() as conn:
        row = conn.execute(
            'SELECT previous_label FROM smart_queue_auto_labels WHERE profile_id=? AND torrent_hash=?',
            (profile_id, torrent_hash),
        ).fetchone()
        if row:
            conn.execute(
                'UPDATE smart_queue_auto_labels SET updated_at=? WHERE profile_id=? AND torrent_hash=?',
                (now, profile_id, torrent_hash),
            )
        else:
            conn.execute(
                'INSERT INTO smart_queue_auto_labels(profile_id,torrent_hash,previous_label,created_at,updated_at) VALUES(?,?,?,?,?)',
                (profile_id, torrent_hash, previous_label, now, now),
            )


def _read_label(client: Any, torrent_hash: str, fallback: str = '') -> str:
    try:
        return str(client.call('d.custom1', torrent_hash) or '')
    except Exception:
        return fallback


def _restore_auto_label(client: Any, profile_id: int, torrent_hash: str, current_label: str | None = None) -> bool:
    """Remove only Smart Queue's technical marker while preserving user labels."""
    with connect() as conn:
        row = conn.execute(
            'SELECT previous_label FROM smart_queue_auto_labels WHERE profile_id=? AND torrent_hash=?',
            (profile_id, torrent_hash),
        ).fetchone()
        previous_label = str((row or {}).get('previous_label') or '')
        live_label = _read_label(client, torrent_hash, current_label or '')
        if not row and not _has_smart_queue_label(live_label):
            return False
        try:
            if _has_smart_queue_label(live_label) or row:
                # Note: Remove Smart Queue only. Never clear unrelated labels when a torrent enters downloading.
                client.call('d.custom1.set', torrent_hash, _smart_queue_label_cleanup_value(live_label, previous_label))
            if row:
                conn.execute('DELETE FROM smart_queue_auto_labels WHERE profile_id=? AND torrent_hash=?', (profile_id, torrent_hash))
            return True
        except Exception:
            return False





def _call_rtorrent_setter(client: Any, method: str, value: int) -> bool:
    """Set a scalar rTorrent setting while tolerating XMLRPC signature differences."""
    for args in ((int(value),), ('', int(value))):
        try:
            client.call(method, *args)
            return True
        except Exception:
            continue
    return False


def _ensure_rtorrent_download_cap(client: Any, max_active: int) -> dict[str, Any]:
    """Raise rTorrent download caps that can silently limit Smart Queue to one item."""
    result: dict[str, Any] = {'checked': False, 'updated': False, 'items': []}
    # Note: rTorrent may have separate global and per-throttle limits. When div=1,
    # starts can effectively stop at one active torrent even when the target is 100.
    for key in ('throttle.max_downloads.global', 'throttle.max_downloads.div'):
        item: dict[str, Any] = {'key': key, 'checked': False, 'updated': False}
        try:
            current = int(client.call(key) or 0)
            item.update({'checked': True, 'current': current, 'target': int(max_active)})
            result['checked'] = True
            # Note: 0 means unlimited; raise only positive limits lower than the target.
            if 0 < current < max_active:
                ok = _call_rtorrent_setter(client, f'{key}.set', int(max_active))
                item['updated'] = ok
                if ok:
                    result['updated'] = True
                    item['new'] = int(max_active)
                    result.setdefault('current', current)
                    result['new'] = int(max_active)
        except Exception as exc:
            item.update({'error': str(exc)})
        result['items'].append(item)
    return result


def _start_download(client: Any, profile_id: int, torrent: dict[str, Any]) -> dict[str, Any]:
    """Start only stopped Smart Queue candidates; paused torrents are a user decision."""
    h = str(torrent.get('hash') or '')
    if not h:
        return {'hash': h, 'ok': False, 'error': 'missing hash'}
    if _is_user_paused(torrent):
        # Note: Smart Queue never unpauses user-paused torrents; it manages only stopped items.
        return {'hash': h, 'ok': False, 'skipped': 'user_paused'}
    # Note: Remove Smart Queue's technical hold before d.open/d.start. Some rTorrent/ruTorrent setups
    # attach behavior to labels, so the queue must start the same item state the manual Start sees.
    label_cleanup = _restore_auto_label(client, profile_id, h, str(torrent.get('label') or ''))
    # Note: Smart Queue selected this candidate as stopped, so force the real start path.
    # A live state=1/active=0 after auto-check is not necessarily a user pause.
    result = rtorrent.start_or_resume_hash(client, h, prefer_start=True)
    result['label_cleanup'] = bool(label_cleanup)
    return result


def _verify_started_downloads(
    client: Any,
    hashes: list[str],
    attempts: int = SMART_QUEUE_START_VERIFY_ATTEMPTS,
    delay: float = SMART_QUEUE_START_VERIFY_DELAY_SECONDS,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Verify started torrents with a slower, lightweight confirmation loop.

    rTorrent can accept a large batch of d.start commands immediately but expose
    d.state/d.is_active gradually. The verifier therefore waits longer than the
    old five-second window and polls only cheap state fields during the loop.
    Detailed diagnostics are read only for torrents that still did not confirm.
    """
    pending = [h for h in hashes if h]
    seen_started: set[str] = set()
    checks = max(1, int(attempts or 1))
    wait = max(0.1, float(delay or 0.1))

    for attempt in range(checks):
        if attempt:
            time.sleep(wait)
        for h in list(pending):
            if _read_live_started_flag(client, h):
                seen_started.add(h)
                pending.remove(h)
        if not pending:
            break

    started = [h for h in hashes if h in seen_started]
    no_effect: list[dict[str, Any]] = []
    for h in hashes:
        if h and h not in seen_started:
            live = _read_live_start_state(client, h)
            live['verify_attempts'] = checks
            live['verify_delay_seconds'] = wait
            no_effect.append(live)
    return started, no_effect


def _read_live_started_flag(client: Any, torrent_hash: str) -> bool:
    """Return True when rTorrent reports that a download has left the stopped state."""
    for method in ('d.state', 'd.is_active'):
        try:
            if int(client.call(method, torrent_hash) or 0):
                return True
        except Exception:
            continue
    return False


def _start_and_verify_downloads(client: Any, profile_id: int, torrents: list[dict[str, Any]]) -> dict[str, Any]:
    """Start Smart Queue candidates in moderate batches and verify them after rTorrent catches up."""
    start_failed: list[dict[str, str]] = []
    start_requested: list[str] = []
    start_results: list[dict[str, Any]] = []
    batch_size = max(1, int(SMART_QUEUE_START_BATCH_SIZE))
    pause = max(0.0, float(SMART_QUEUE_START_BATCH_PAUSE_SECONDS))

    for offset in range(0, len(torrents), batch_size):
        batch = torrents[offset:offset + batch_size]
        for t in batch:
            h = str(t.get('hash') or '')
            if not h:
                continue
            try:
                result = _start_download(client, profile_id, t)
                start_results.append(result)
                if result.get('ok', True):
                    start_requested.append(h)
                else:
                    start_failed.append({'hash': h, 'error': str(result.get('error') or result.get('skipped') or 'start rejected')})
            except Exception as exc:
                start_failed.append({'hash': h, 'error': str(exc)})
        if offset + batch_size < len(torrents) and pause:
            time.sleep(pause)

    active_verified, start_pending_confirmation = _verify_started_downloads(
        client,
        start_requested,
        SMART_QUEUE_START_VERIFY_ATTEMPTS,
        SMART_QUEUE_START_VERIFY_DELAY_SECONDS,
    )
    # Note: A successful d.start/d.resume RPC is the queue outcome. rTorrent may keep the item idle/queued
    # for longer than the verification window, so unconfirmed accepted starts are pending confirmation,
    # not a failed/no-effect start.
    return {
        'active_verified': active_verified,
        'start_failed': start_failed,
        'start_no_effect': [],
        'start_pending_confirmation': start_pending_confirmation,
        'start_requested': start_requested,
        'start_results': start_results,
        'start_batch_size': batch_size,
        'start_batch_pause_seconds': pause,
        'start_verify_attempts': SMART_QUEUE_START_VERIFY_ATTEMPTS,
        'start_verify_delay_seconds': SMART_QUEUE_START_VERIFY_DELAY_SECONDS,
    }


def _read_live_start_state(client: Any, torrent_hash: str) -> dict[str, Any]:
    result: dict[str, Any] = {'hash': torrent_hash}
    fields = (
        ('name', 'd.name'),
        ('state', 'd.state'),
        ('active', 'd.is_active'),
        ('open', 'd.is_open'),
        ('complete', 'd.complete'),
        ('hashing', 'd.hashing'),
        ('priority', 'd.priority'),
        ('down_rate', 'd.down.rate'),
        ('peers', 'd.peers_connected'),
        ('seeds', 'd.peers_complete'),
        ('message', 'd.message'),
        ('label', 'd.custom1'),
    )
    for key, method in fields:
        try:
            value = client.call(method, torrent_hash)
            result[key] = int(value or 0) if key in {'state', 'active', 'open', 'complete', 'hashing', 'priority', 'down_rate', 'peers', 'seeds'} else str(value or '')
        except Exception as exc:
            result[f'{key}_error'] = str(exc)
    # Note: Manual Start in rTorrent is successful when d.state becomes 1.
    # d.is_active can stay 0 for queued/idle downloads, so it must not be used as the only success check.
    result['started'] = bool(int(result.get('state') or 0) or int(result.get('active') or 0))
    result['pending_reason'] = _classify_pending_start_state(result)
    return result


def _classify_pending_start_state(state: dict[str, Any]) -> str:
    if any(str(key).endswith('_error') for key in state):
        return 'rpc_error'
    if int(state.get('hashing') or 0):
        return 'checking'
    if int(state.get('complete') or 0):
        return 'complete'
    if int(state.get('priority') or 0) <= 0:
        return 'priority_off'
    if not int(state.get('state') or 0):
        return 'stopped' if int(state.get('open') or 0) else 'closed'
    if int(state.get('seeds') or 0) <= 0 and int(state.get('peers') or 0) <= 0:
        return 'no_sources'
    if str(state.get('message') or '').strip():
        return 'message'
    if not int(state.get('active') or 0):
        return 'inactive'
    return 'unknown'


def _is_user_paused(torrent: dict[str, Any]) -> bool:
    """Return True for torrents paused by the user; Smart Queue must not touch them."""
    status = str(torrent.get('status') or '').lower()
    return bool(torrent.get('paused')) or status == 'paused'

def _set_smart_queue_label(client: Any, torrent_hash: str, current_label: str = '', attempts: int = 3) -> bool:
    for attempt in range(max(1, attempts)):
        try:
            # Always merge with the live rTorrent label. The snapshot passed by Smart Queue can be
            # stale when a user labels a newly added torrent around the same time as auto-check/refill.
            live_label = _read_label(client, torrent_hash, current_label or '')
            labels = _label_names(live_label)
            if SMART_QUEUE_LABEL in labels:
                return True
            labels.append(SMART_QUEUE_LABEL)
            client.call('d.custom1.set', torrent_hash, _label_value(labels))
            return True
        except Exception:
            if attempt < attempts - 1:
                time.sleep(0.05)
    return False


def _mark_auto_stopped(client: Any, profile_id: int, torrent: dict[str, Any]) -> bool:
    torrent_hash = str(torrent.get('hash') or '')
    if not torrent_hash:
        return False
    previous = _read_label(client, torrent_hash, str(torrent.get('label') or ''))
    if not _has_smart_queue_label(previous):
        _remember_auto_label(profile_id, torrent_hash, previous)
    return _set_smart_queue_label(client, torrent_hash, previous)



def _record_start_grace(profile_id: int, hashes: list[str]) -> None:
    """Remember queue-started torrents so stalled detection gives them a warm-up window."""
    clean = [str(h or '').strip() for h in hashes if str(h or '').strip()]
    if not clean:
        return
    now = utcnow()
    with connect() as conn:
        for torrent_hash in clean:
            conn.execute(
                'INSERT OR REPLACE INTO smart_queue_start_grace(profile_id,torrent_hash,started_at,updated_at) VALUES(?,?,?,?)',
                (profile_id, torrent_hash, now, now),
            )


def _load_active_start_grace(profile_id: int, grace_seconds: int, now_ts: float) -> set[str]:
    """Return hashes still inside the post-start warm-up window and purge expired rows."""
    grace = max(0, int(grace_seconds or 0))
    if grace <= 0:
        with connect() as conn:
            conn.execute('DELETE FROM smart_queue_start_grace WHERE profile_id=?', (profile_id,))
        return set()
    active: set[str] = set()
    expired: list[str] = []
    with connect() as conn:
        rows = conn.execute('SELECT torrent_hash, started_at FROM smart_queue_start_grace WHERE profile_id=?', (profile_id,)).fetchall()
        for row in rows:
            torrent_hash = str(row.get('torrent_hash') or '')
            if not torrent_hash:
                continue
            if now_ts - _ts(row.get('started_at')) < grace:
                active.add(torrent_hash)
            else:
                expired.append(torrent_hash)
        for torrent_hash in expired:
            conn.execute('DELETE FROM smart_queue_start_grace WHERE profile_id=? AND torrent_hash=?', (profile_id, torrent_hash))
    return active


def _is_started_download_slot(torrent: dict[str, Any] | None) -> bool:
    """Return True for incomplete torrents already started in rTorrent, including manual starts."""
    if not torrent or int(torrent.get('complete') or 0):
        return False
    status = str(torrent.get('status') or '').lower()
    if status == 'checking':
        return False
    # Note: Manual Start changes d.state first; d.is_active may stay 0 while rTorrent is queued or idle.
    return bool(int(torrent.get('state') or 0) or int(torrent.get('active') or 0))


def _is_smart_queue_hold(torrent: dict[str, Any] | None, manage_stopped: bool = True) -> bool:
    if not torrent or int(torrent.get('complete') or 0):
        return False
    if _is_started_download_slot(torrent):
        # Note: A manual start can leave the Smart Queue label behind; started items are active slots, not holds.
        return False
    if _has_stalled_label(str(torrent.get('label') or '')):
        return False
    if _is_user_paused(torrent):
        # Note: Paused torrents are always treated as user-controlled and are not Smart Queue holds.
        return False
    if _has_smart_queue_label(str(torrent.get('label') or '')):
        return True
    # Note: Smart Queue manages stopped torrents by default; the old manage_stopped flag is ignored for compatibility.
    return not int(torrent.get('state') or 0)


def _clear_untracked_smart_queue_label(client: Any, torrent_hash: str, current_label: str) -> bool:
    if not _has_smart_queue_label(current_label):
        return False
    try:
        # Note: Clear only the orphaned Smart Queue marker and keep unrelated labels intact.
        client.call('d.custom1.set', torrent_hash, _smart_queue_label_cleanup_value(current_label))
        return True
    except Exception:
        return False


def _cleanup_auto_labels(client: Any, profile_id: int, torrents: list[dict[str, Any]], keep_hashes: set[str], manage_stopped: bool = True) -> list[str]:
    by_hash = {str(t.get('hash') or ''): t for t in torrents}
    restored: list[str] = []
    with connect() as conn:
        rows = conn.execute('SELECT torrent_hash FROM smart_queue_auto_labels WHERE profile_id=?', (profile_id,)).fetchall()
    tracked_hashes = {str(row.get('torrent_hash') or '') for row in rows if row.get('torrent_hash')}

    for row in rows:
        h = str(row.get('torrent_hash') or '')
        t = by_hash.get(h)
        if not h or h in keep_hashes:
            continue
        current_label = '' if t is None else str(t.get('label') or '')
        if not _is_smart_queue_hold(t, manage_stopped):
            if _restore_auto_label(client, profile_id, h, None if t is None else current_label):
                restored.append(h)
            continue
        if not _has_smart_queue_label(current_label):
            _set_smart_queue_label(client, h, current_label)

    for h, t in by_hash.items():
        if not h or h in keep_hashes or h in tracked_hashes or _is_smart_queue_hold(t, manage_stopped):
            continue
        if _clear_untracked_smart_queue_label(client, h, str(t.get('label') or '')):
            restored.append(h)
    return restored


def _is_running_download_slot(t: dict[str, Any]) -> bool:
    """Return True for incomplete torrents that already occupy a Smart Queue slot."""
    # Note: Do not exclude Smart Queue/Stalled labels here. Manual Start can leave old labels,
    # and those torrents still must count toward the global Smart Queue limit.
    return _is_started_download_slot(t) and not _is_user_paused(t)


def _has_recent_transfer_activity(t: dict[str, Any], stalled_seconds: int) -> bool:
    """Return True when a torrent is currently transferring or was active within the stalled window."""
    # Note: Live transfer rates always protect a torrent from being marked as stalled.
    if int(t.get('down_rate') or 0) > 0 or int(t.get('up_rate') or 0) > 0:
        return True
    last_activity = int(t.get('last_activity') or 0)
    if last_activity <= 0:
        return False
    return time.time() - last_activity < max(1, int(stalled_seconds or 0))


def _is_stalled_download(t: dict[str, Any], min_speed: int, min_seeds: int, min_peers: int, stalled_seconds: int, ignore_seed_peer: bool, ignore_speed: bool) -> bool:
    """Return True when a started torrent should begin or continue the stalled timer."""
    # Note: Recent transfer activity wins over ignored source/speed criteria, preventing active torrents from being stopped as stalled.
    if _has_recent_transfer_activity(t, stalled_seconds):
        return False
    speed_ok = True if ignore_speed else int(t.get('down_rate') or 0) <= max(0, int(min_speed or 0))
    source_ok = True if ignore_seed_peer else int(t.get('seeds') or 0) <= max(0, int(min_seeds or 0)) and (min_peers <= 0 or int(t.get('peers') or 0) <= min_peers)
    return speed_ok and source_ok


def _stalled_timer_key(min_speed: int, min_seeds: int, min_peers: int, stalled_seconds: int, ignore_seed_peer: bool, ignore_speed: bool) -> str:
    """Return a stable key for the stalled rules that started the current timer."""
    # Note: Version bump clears old timers created by the previous ignore-speed/source behavior.
    return f"v5|speed={int(min_speed or 0)}|seeds={int(min_seeds or 0)}|peers={int(min_peers or 0)}|seconds={int(stalled_seconds or 0)}|ignore_sources={int(bool(ignore_seed_peer))}|ignore_speed={int(bool(ignore_speed))}"


def _is_low_activity_download(t: dict[str, Any], min_speed: int, min_seeds: int, min_peers: int, stalled_seconds: int, ignore_seed_peer: bool = False, ignore_speed: bool = False) -> bool:
    """Return True when a started torrent is weak and should be stopped first."""
    # Note: Active transfers are never preferred for cleanup while non-transferring rows are available.
    if _has_recent_transfer_activity(t, stalled_seconds):
        return False
    low_speed = False if ignore_speed else int(t.get('down_rate') or 0) <= max(0, int(min_speed or 0))
    low_seeds = False if ignore_seed_peer else int(t.get('seeds') or 0) <= max(0, int(min_seeds or 0))
    low_peers = False if ignore_seed_peer or min_peers <= 0 else int(t.get('peers') or 0) <= max(0, int(min_peers or 0))
    return low_speed or low_seeds or low_peers


def _is_waiting_download_candidate(t: dict[str, Any], manage_stopped: bool) -> bool:
    """Return True for stopped torrents Smart Queue may start later."""
    if int(t.get('complete') or 0):
        return False
    if str(t.get('status') or '').lower() == 'checking':
        # Note: Torrents still being checked must finish post-check handling before Smart Queue may start them.
        return False
    if _has_stalled_label(str(t.get('label') or '')):
        return False
    if _is_user_paused(t):
        # Note: User-paused torrents are never candidates, even when they have no Smart Queue label.
        return False
    if _has_smart_queue_label(str(t.get('label') or '')):
        return True
    # Note: Enabled Smart Queue manages all stopped torrents; no separate stopped-torrent switch is needed.
    return not int(t.get('state') or 0)



def _progress_value(torrent: dict[str, Any]) -> float:
    """Return a safe 0-100 progress value for queue ranking."""
    try:
        value = float(torrent.get('progress') or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(100.0, value))


def _start_candidate_sort_key(torrent: dict[str, Any], prefer_partial_progress: bool) -> tuple[float, float, int, int, int]:
    """Rank stopped downloads for starting; partial progress can win so work is finished first."""
    progress = _progress_value(torrent)
    # Note: Existing partial downloads are preferred by default, then higher progress, then better source counts.
    partial_rank = 1.0 if prefer_partial_progress and 0.0 < progress < 100.0 else 0.0
    return (
        partial_rank,
        progress if prefer_partial_progress else 0.0,
        int(torrent.get('seeds') or 0),
        int(torrent.get('peers') or 0),
        int(torrent.get('down_rate') or 0),
    )

def _split_start_candidates(torrents: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return all stopped torrents as start candidates without relying on stale source counts."""
    # Note: rTorrent/tracker source counts can be missing before announce, so start decisions are not filtered by seeds or peers.
    return list(torrents), []


def cooldown_remaining(settings: dict[str, Any]) -> int:
    # Note: Returns seconds remaining until the next automatic Smart Queue run is allowed.
    last = _ts(settings.get('last_run_at'))
    minutes = max(1, int(settings.get('cooldown_minutes') or 10))
    if not last:
        return 0
    return max(0, int((last + minutes * 60) - time.time()))



def refill_remaining(settings: dict[str, Any]) -> int:
    # Note: Custom refill interval is separate from the full Smart Queue cooldown.
    if not int(settings.get('refill_enabled') or 0):
        return 0
    minutes = int(settings.get('refill_interval_minutes') or 0)
    if minutes <= 0:
        return 0
    last = _ts(settings.get('last_refill_at'))
    if not last:
        return 0
    return max(0, int((last + minutes * 60) - time.time()))


def _refill_mode(settings: dict[str, Any]) -> str:
    # Note: Expose one stable frontend mode while storing compact database fields.
    if not int(settings.get('refill_enabled') or 0):
        return 'off'
    return 'custom' if int(settings.get('refill_interval_minutes') or 0) > 0 else 'auto'


def _mark_refill_run(profile_id: int, user_id: int) -> None:
    # Note: Custom refill interval is measured from the last lightweight refill attempt.
    with connect() as conn:
        conn.execute('UPDATE smart_queue_settings SET last_refill_at=?, updated_at=? WHERE profile_id=?', (utcnow(), utcnow(), profile_id))


def _refill_underfilled_queue(profile: dict, settings: dict[str, Any], profile_id: int, user_id: int) -> dict[str, Any]:
    """Fill free Smart Queue slots during cooldown without running full stalled/stop logic."""
    # Note: This lightweight pass fixes queue starvation after downloads finish or new stopped torrents are added.
    torrents = rtorrent.list_torrents(profile)
    user_excluded = _excluded_hashes(profile_id, user_id)
    max_active = max(1, int(settings.get('max_active_downloads') or 5))
    min_seeds = int(settings.get('min_seeds') or 0)
    min_peers = int(settings.get('min_peers') or 0)
    stalled_label_hashes = {str(t.get('hash') or '') for t in torrents if _has_stalled_label(str(t.get('label') or '')) and t.get('hash')}
    downloading = [
        t for t in torrents
        if _is_running_download_slot(t)
        and str(t.get('hash') or '') not in user_excluded
    ]
    stopped = [
        t for t in torrents
        if str(t.get('hash') or '') not in user_excluded
        and str(t.get('hash') or '') not in stalled_label_hashes
        and _is_waiting_download_candidate(t, True)
        and not _is_running_download_slot(t)
    ]
    if int(settings.get('auto_stop_idle') or 0) and not downloading and not stopped:
        idle_details = {
            'decision': 'Smart Queue auto-stopped during cooldown refill: no active or waiting downloads',
            'enabled': False,
            'auto_stop_idle': True,
            'cooldown_refill': True,
            'checked': len(torrents),
            'active_before': 0,
            'active_after_stop': 0,
            'active_after_expected': 0,
            'max_active_downloads': max_active,
            'over_limit': 0,
            'stopped': [],
            'started': [],
            'start_requested': [],
            'active_verified_count': 0,
            'pending_confirmation_count': 0,
            'stalled_detected': 0,
            'stalled_stopped': 0,
            'protected_stalled': 0,
            'excluded': len(user_excluded),
            'excluded_stalled': len(stalled_label_hashes),
        }
        _diagnostics_write('smart_queue.auto_stopped_idle', {'profile_id': profile_id, 'checked': len(torrents), 'cooldown_refill': True}, idle_details)
        return _disable_when_idle(profile_id, user_id, torrents, idle_details)
    available_slots = max(0, max_active - len(downloading))
    startable_stopped, source_skipped = _split_start_candidates(stopped)
    prefer_partial_progress = bool(int(settings.get('prefer_partial_progress', 1) or 0))
    candidates = sorted(
        startable_stopped,
        key=lambda t: _start_candidate_sort_key(t, prefer_partial_progress),
        reverse=True,
    )
    c = rtorrent.client_for(profile)
    started_by_queue: list[str] = []
    label_failed: list[str] = []
    start_failed: list[dict[str, str]] = []
    start_no_effect: list[dict[str, Any]] = []
    start_requested: list[str] = []
    start_results: list[dict[str, Any]] = []
    to_start = candidates[:available_slots]
    to_label_waiting = candidates[available_slots:]

    for t in to_label_waiting:
        h = str(t.get('hash') or '')
        if not h:
            continue
        try:
            if not _mark_auto_stopped(c, profile_id, t):
                label_failed.append(h)
        except Exception:
            label_failed.append(h)

    start_summary = _start_and_verify_downloads(c, profile_id, to_start)
    active_verified = start_summary['active_verified']
    start_no_effect = start_summary['start_no_effect']
    start_pending_confirmation = start_summary.get('start_pending_confirmation', [])
    start_failed = start_summary['start_failed']
    start_requested = start_summary['start_requested']
    start_results = start_summary['start_results']
    _record_start_grace(profile_id, start_requested)
    for h in start_requested:
        _restore_auto_label(c, profile_id, h, None)
        try:
            rtorrent.clear_post_check_download_label(c, h, None)
        except Exception:
            label_failed.append(h)
    started_by_queue = list(start_requested)
    keep_labels = (
        {str(t.get('hash') or '') for t in to_label_waiting}
        | {str(t.get('hash') or '') for t in stopped if _has_smart_queue_label(str(t.get('label') or '')) and str(t.get('hash') or '') not in set(started_by_queue)}
    )
    restored = _cleanup_auto_labels(c, profile_id, torrents, keep_labels, True)
    # Note: Cooldown refill uses started incomplete torrents as queue slots. This diagnostic
    # explains why a refill may legitimately start nothing even when only a few torrents transfer data.
    active_transferring = sum(1 for t in downloading if int(t.get('down_rate') or 0) > 0 or int(t.get('up_rate') or 0) > 0)
    active_rtorrent = sum(1 for t in downloading if int(t.get('active') or 0))
    active_state = sum(1 for t in downloading if int(t.get('state') or 0))
    active_after_expected = len(downloading) + len(start_requested)
    if available_slots <= 0:
        refill_decision = f'Cooldown refill skipped: active slots at limit ({len(downloading)}/{max_active})'
        refill_blocked_reason = 'active_slots_at_limit'
    elif not candidates:
        refill_decision = 'Cooldown refill skipped: no stopped candidates available'
        refill_blocked_reason = 'no_candidates'
    elif start_requested:
        refill_decision = f'Cooldown refill requested {len(start_requested)} start(s)'
        refill_blocked_reason = ''
    else:
        refill_decision = 'Cooldown refill ran but rTorrent did not confirm new starts yet'
        refill_blocked_reason = 'start_not_confirmed'
    details = {
        'decision': refill_decision,
        'blocked_reason': refill_blocked_reason,
        'enabled': bool(settings.get('enabled')),
        'cooldown_refill': True,
        'cooldown_respected': True,
        'refill_mode': _refill_mode(settings),
        'refill_interval_minutes': int(settings.get('refill_interval_minutes') or 0),
        'active_before': len(downloading),
        'active_after_expected': active_after_expected,
        'active_transferring_count': active_transferring,
        'active_rtorrent_count': active_rtorrent,
        'active_state_count': active_state,
        'available_slots': available_slots,
        'candidates': len(candidates),
        'start_source_skipped': len(source_skipped),
        'waiting_labeled': len(to_label_waiting),
        'started_planned': len(to_start),
        'start_requested': start_requested,
        'start_results': start_results,
        'start_batch_size': start_summary['start_batch_size'],
        'start_batch_pause_seconds': start_summary['start_batch_pause_seconds'],
        'start_verify_attempts': start_summary['start_verify_attempts'],
        'start_verify_delay_seconds': start_summary['start_verify_delay_seconds'],
        'start_no_effect': start_no_effect,
        'start_pending_confirmation': start_pending_confirmation,
        'start_failed': start_failed,
        'active_verified': active_verified,
        'labels_failed': label_failed,
        'labels_restored': restored,
        'max_active_downloads': max_active,
        'prefer_partial_progress': prefer_partial_progress,
        'excluded': len(user_excluded),
        'excluded_stalled': len(stalled_label_hashes),
    }
    _diagnostics_write(
        'smart_queue.cooldown_refill',
        {
            'profile_id': profile_id,
            'checked': len(torrents),
            'active_before': len(downloading),
            'max_active_downloads': max_active,
            'available_slots': available_slots,
            'candidates': len(candidates),
            'active_transferring': active_transferring,
            'active_rtorrent': active_rtorrent,
            'active_state': active_state,
            'blocked_reason': refill_blocked_reason,
            'start_source_skipped': len(source_skipped),
            'requested': len(start_requested),
            'verified': len(active_verified),
            'pending': len(start_pending_confirmation),
            'pending_reasons': _pending_reason_counts(start_pending_confirmation),
            'start_failed': len(start_failed),
            'no_effect': len(start_no_effect),
            'waiting_labeled': len(to_label_waiting),
            'labels_failed': len(label_failed),
        },
        {
            'settings': {
                'refill_mode': _refill_mode(settings),
                'refill_interval_minutes': int(settings.get('refill_interval_minutes') or 0),
                'min_seeds': min_seeds,
                'min_peers': min_peers,
                'prefer_partial_progress': prefer_partial_progress,
            },
            'to_start': _diagnostics_torrents(to_start),
            'to_label_waiting': _diagnostics_torrents(to_label_waiting),
            'source_skipped': _diagnostics_torrents(source_skipped),
            'pending_confirmation': _diagnostics_sample(start_pending_confirmation),
            'start_failed': _diagnostics_sample(start_failed),
            'start_results': _diagnostics_sample(start_results),
            'labels_failed': _diagnostics_sample(label_failed),
        },
    )
    _mark_refill_run(profile_id, user_id)
    if started_by_queue or to_label_waiting or start_failed or label_failed or restored:
        add_history(profile_id, 'cooldown_refill', [], started_by_queue, len(torrents), details, user_id)
    settings = get_settings(profile_id, user_id)
    return {
        'ok': True,
        'enabled': bool(settings.get('enabled')),
        'cooldown_skipped': True,
        'cooldown_refill': True,
        'cooldown_respected': True,
        'refill_mode': _refill_mode(settings),
        'refill_interval_minutes': int(settings.get('refill_interval_minutes') or 0),
        'refill_remaining_seconds': refill_remaining(settings),
        'paused': [],
        'resumed': started_by_queue,
        'stopped': [],
        'started': started_by_queue,
        'start_requested': start_requested,
        'start_batch_size': start_summary['start_batch_size'],
        'start_verify_attempts': start_summary['start_verify_attempts'],
        'start_verify_delay_seconds': start_summary['start_verify_delay_seconds'],
        'waiting_labeled': len(to_label_waiting),
        'labels_restored': restored,
        'labels_failed': label_failed,
        'start_failed': start_failed,
        'start_no_effect': start_no_effect,
        'start_pending_confirmation': start_pending_confirmation,
        'active_verified': active_verified,
        'active_before': len(downloading),
        'active_after_expected': active_after_expected,
        'active_transferring_count': active_transferring,
        'active_rtorrent_count': active_rtorrent,
        'active_state_count': active_state,
        'blocked_reason': refill_blocked_reason,
        'available_slots': available_slots,
        'start_source_skipped': len(source_skipped),
        'checked': len(torrents),
        'excluded': len(user_excluded),
        'rtorrent_cap': rtorrent_cap,
        'settings': settings,
    }



def _simulation_settings(current: dict[str, Any], data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return Smart Queue settings merged with unsaved UI values for read-only dry-run planning."""
    # Note: The simulator accepts draft UI values and never persists them, so users can test settings safely.
    data = data or {}
    merged = dict(current)
    for key, default, minimum in (
        ('max_active_downloads', 5, 1),
        ('stalled_seconds', 300, 30),
        ('min_speed_bytes', 0, 0),
        ('min_seeds', 0, 0),
        ('min_peers', 0, 0),
        ('cooldown_minutes', 10, 1),
        ('stop_batch_size', 50, 1),
        ('start_grace_seconds', 900, 0),
        ('refill_interval_minutes', 0, 0),
        ('surge_refill_interval_minutes', 1440, 1),
        ('surge_refill_batch_size', 2000, 1),
    ):
        if key in data:
            merged[key] = _int_setting(data, current, key, default, minimum)
    for key in (
        'enabled',
        'ignore_seed_peer',
        'ignore_speed',
        'protect_active_below_cap',
        'prefer_partial_progress',
        'auto_stop_idle',
        'refill_enabled',
        'surge_refill_enabled',
    ):
        if key in data:
            merged[key] = 1 if data.get(key) else 0
    refill_mode = str(data.get('refill_mode') or '').strip().lower()
    if refill_mode in {'auto', 'custom', 'off'}:
        merged['refill_enabled'] = 0 if refill_mode == 'off' else 1
        merged['refill_interval_minutes'] = _int_setting(data, current, 'refill_interval_minutes', 5, 1) if refill_mode == 'custom' else 0
    return merged


def _simulation_torrent_item(t: dict[str, Any], reason: str = '', effective_down_rate: int | None = None) -> dict[str, Any]:
    """Build a compact torrent row for the Smart Queue simulator UI."""
    # Note: Hashes are kept because they are the only stable link between the dry-run and the live torrent list.
    down_rate = int(t.get('down_rate') or 0)
    return {
        'hash': str(t.get('hash') or ''),
        'name': str(t.get('name') or t.get('hash') or ''),
        'status': str(t.get('status') or ''),
        'progress': _progress_value(t),
        'down_rate': down_rate,
        'effective_down_rate': down_rate if effective_down_rate is None else max(0, int(effective_down_rate or 0)),
        'up_rate': int(t.get('up_rate') or 0),
        'seeds': int(t.get('seeds') or 0),
        'peers': int(t.get('peers') or 0),
        'label': str(t.get('label') or ''),
        'reason': reason,
    }



def _simulation_int(data: dict[str, Any] | None, key: str, default: int, minimum: int = 0, maximum: int | None = None) -> int:
    """Read a bounded integer option used only by the Smart Queue simulator."""
    # Note: Simulation-only inputs are clamped so a browser typo cannot create huge timeline payloads.
    try:
        value = int(float(str((data or {}).get(key, default) or default)))
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _simulation_remaining_bytes(t: dict[str, Any]) -> int:
    """Return remaining download bytes for forecast rows without changing torrent state."""
    # Note: rTorrent snapshots expose both to_download and size/completed; the fallback keeps old snapshots usable.
    if int(t.get('complete') or 0):
        return 0
    if t.get('to_download') not in (None, ''):
        try:
            return max(0, int(t.get('to_download') or 0))
        except (TypeError, ValueError):
            pass
    try:
        return max(0, int(t.get('size') or 0) - int(t.get('completed_bytes') or 0))
    except (TypeError, ValueError):
        return 0



def _simulation_live_forecast_speed(t: dict[str, Any], fallback_speed: int = 0) -> int:
    """Return a per-torrent forecast speed when no total simulated speed is provided."""
    # Note: Live mode uses current per-torrent speed; queued starts can fall back to the live average.
    return max(0, int(t.get('down_rate') or 0), int(fallback_speed or 0))


def _shared_simulation_schedule(rows: list[dict[str, Any]], total_speed: int) -> dict[str, Any]:
    """Calculate finish times when one total bandwidth value is shared by active torrents."""
    # Note: The simulated speed field represents total download bandwidth, so every active unfinished torrent gets an equal share.
    total_speed = max(0, int(total_speed or 0))
    initial = [float(max(0, int(row.get('remaining_bytes') or 0))) for row in rows]
    if total_speed <= 0 or not rows or not any(value > 0 for value in initial):
        return {'etas': {}, 'initial_rates': {}, 'average_rates': {}}
    remaining = {idx: value for idx, value in enumerate(initial) if value > 0}
    elapsed = 0.0
    etas: dict[int, int] = {}
    guard = 0
    while remaining and guard < len(rows) + 5:
        guard += 1
        active_count = len(remaining)
        share = total_speed / active_count
        if share <= 0:
            break
        step_bytes = min(remaining.values())
        elapsed += step_bytes / share
        completed = [idx for idx, value in remaining.items() if value <= step_bytes + 0.000001]
        for idx in completed:
            etas[idx] = max(0, int(round(elapsed)))
            remaining.pop(idx, None)
        for idx in list(remaining):
            remaining[idx] = max(0.0, remaining[idx] - step_bytes)
    initial_share = int(total_speed / max(1, len([value for value in initial if value > 0]))) if any(value > 0 for value in initial) else 0
    average_rates = {
        idx: int(initial[idx] / max(1, eta))
        for idx, eta in etas.items()
        if idx < len(initial)
    }
    return {
        'etas': etas,
        'initial_rates': {idx: initial_share for idx, value in enumerate(initial) if value > 0},
        'average_rates': average_rates,
    }


def _shared_simulation_point(rows: list[dict[str, Any]], total_speed: int, seconds: int) -> dict[str, int]:
    """Return aggregate progress at a point in time for shared-bandwidth simulation."""
    # Note: The same fair-share model is used for timeline points and completion dates to avoid contradictory UI numbers.
    total_speed = max(0, int(total_speed or 0))
    seconds = max(0, int(seconds or 0))
    remaining = {idx: float(max(0, int(row.get('remaining_bytes') or 0))) for idx, row in enumerate(rows) if int(row.get('remaining_bytes') or 0) > 0}
    if total_speed <= 0 or not remaining:
        total_remaining = sum(int(row.get('remaining_bytes') or 0) for row in rows)
        return {'downloaded': 0, 'remaining': total_remaining, 'completed': 0, 'active': len(remaining)}
    elapsed = 0.0
    guard = 0
    while remaining and elapsed < seconds and guard < len(rows) + 5:
        guard += 1
        active_count = len(remaining)
        share = total_speed / active_count
        if share <= 0:
            break
        step_bytes = min(remaining.values())
        step_seconds = step_bytes / share
        available = seconds - elapsed
        if available < step_seconds:
            downloaded_each = share * available
            for idx in list(remaining):
                remaining[idx] = max(0.0, remaining[idx] - downloaded_each)
            elapsed = seconds
            break
        elapsed += step_seconds
        completed = [idx for idx, value in remaining.items() if value <= step_bytes + 0.000001]
        for idx in completed:
            remaining.pop(idx, None)
        for idx in list(remaining):
            remaining[idx] = max(0.0, remaining[idx] - step_bytes)
    total_remaining = int(round(sum(remaining.values())))
    total_initial = sum(int(row.get('remaining_bytes') or 0) for row in rows)
    completed = len(rows) - len(remaining)
    return {
        'downloaded': max(0, total_initial - total_remaining),
        'remaining': total_remaining,
        'completed': completed,
        'active': len(remaining),
    }


def _build_simulation_forecast(
    active_rows: list[dict[str, Any]],
    data: dict[str, Any] | None,
    simulated_speed: int | None,
) -> dict[str, Any]:
    """Build selectable dry-run timeline points for time, data, or completion horizons."""
    # Note: Forecasts are read-only; simulated speed is total bandwidth shared by current/planned active downloads.
    data = data or {}
    mode = str(data.get('timeline_mode') or 'events').strip().lower()
    if mode not in {'events', 'complete', 'data', 'time'}:
        mode = 'events'
    point_count = _simulation_int(data, 'timeline_points', 8, 2, 10)
    data_limit_bytes = _simulation_int(data, 'timeline_data_bytes', 0, 0, 10 * 1024 * 1024 * 1024 * 1024)
    time_limit_seconds = _simulation_int(data, 'timeline_time_seconds', 3600, 60, 366 * 24 * 3600)
    fallback_speed = 0
    live_speeds = [int(t.get('down_rate') or 0) for t in active_rows if int(t.get('down_rate') or 0) > 0]
    if live_speeds:
        fallback_speed = max(0, int(sum(live_speeds) / len(live_speeds)))

    rows: list[dict[str, Any]] = []
    for t in active_rows:
        remaining = _simulation_remaining_bytes(t)
        if remaining <= 0:
            continue
        speed = 0 if simulated_speed is not None else _simulation_live_forecast_speed(t, fallback_speed if int(t.get('down_rate') or 0) <= 0 else 0)
        rows.append({
            'hash': str(t.get('hash') or ''),
            'name': str(t.get('name') or t.get('hash') or ''),
            'remaining_bytes': remaining,
            'speed_bytes': speed,
            'average_down_rate': speed,
            'eta_seconds': int(remaining / speed) if speed > 0 else None,
            'forecastable': speed > 0,
            'progress': round(float(t.get('progress') or 0), 2),
        })

    total_remaining = sum(int(r['remaining_bytes']) for r in rows)
    shared_total_speed = max(0, int(simulated_speed or 0)) if simulated_speed is not None else None
    if shared_total_speed is not None:
        schedule = _shared_simulation_schedule(rows, shared_total_speed)
        for idx, row in enumerate(rows):
            row['speed_bytes'] = int(schedule['initial_rates'].get(idx, 0))
            row['average_down_rate'] = int(schedule['average_rates'].get(idx, row['speed_bytes']))
            row['eta_seconds'] = schedule['etas'].get(idx)
            row['forecastable'] = row['eta_seconds'] is not None and shared_total_speed > 0
        total_speed = shared_total_speed if rows else 0
    else:
        total_speed = sum(int(r['speed_bytes']) for r in rows)

    forecastable_rows = [r for r in rows if r.get('forecastable')]
    unforecastable = len(rows) - len(forecastable_rows)
    completion_seconds = max([int(r['eta_seconds']) for r in forecastable_rows if r.get('eta_seconds') is not None] or [0])
    if mode == 'complete':
        horizon_seconds = completion_seconds
    elif mode == 'data':
        capped_data = min(data_limit_bytes, total_remaining) if total_remaining > 0 else data_limit_bytes
        horizon_seconds = int(capped_data / total_speed) if total_speed > 0 and capped_data > 0 else 0
    elif mode == 'time':
        horizon_seconds = time_limit_seconds
    else:
        horizon_seconds = 0
    if mode == 'complete' and unforecastable:
        horizon_seconds = completion_seconds
    horizon_seconds = max(0, int(horizon_seconds or 0))

    points: list[dict[str, Any]] = []
    if mode != 'events' and horizon_seconds > 0 and total_speed > 0:
        for idx in range(point_count):
            seconds = int(round(horizon_seconds * idx / max(1, point_count - 1)))
            if shared_total_speed is not None:
                point = _shared_simulation_point(rows, shared_total_speed, seconds)
                downloaded = point['downloaded']
                remaining = point['remaining']
                completed = point['completed']
                active = point['active']
            else:
                downloaded = 0
                remaining = 0
                completed = 0
                active = 0
                for row in rows:
                    row_remaining = int(row['remaining_bytes'])
                    row_speed = int(row['speed_bytes'])
                    row_downloaded = min(row_remaining, seconds * row_speed) if row_speed > 0 else 0
                    downloaded += row_downloaded
                    left = max(0, row_remaining - row_downloaded)
                    remaining += left
                    if left <= 0:
                        completed += 1
                    else:
                        active += 1
            points.append({
                'label': 'Forecast point',
                'seconds_from_now': seconds,
                'downloaded_bytes': downloaded,
                'remaining_bytes': remaining,
                'completed_count': completed,
                'active_count': active,
                'progress_percent': round((downloaded / total_remaining) * 100, 2) if total_remaining > 0 else 100,
                'description': f'Forecast: downloaded {downloaded} bytes, remaining {remaining} bytes.',
                'kind': 'forecast',
            })

    return {
        'mode': mode,
        'point_count': point_count,
        'data_limit_bytes': data_limit_bytes,
        'time_limit_seconds': time_limit_seconds,
        'horizon_seconds': horizon_seconds,
        'total_remaining_bytes': total_remaining,
        'total_speed_bytes': total_speed,
        'completion_seconds': completion_seconds if completion_seconds else None,
        'forecastable_count': len(forecastable_rows),
        'unforecastable_count': unforecastable,
        'shared_speed': shared_total_speed is not None,
        'points': points,
        'rows': rows,
    }


def _simulation_completed_rows(forecast: dict[str, Any]) -> list[dict[str, Any]]:
    """Return forecastable torrents with calculated finish times for the completed table."""
    # Note: Average speed is derived from remaining bytes and finish time, so shared bandwidth is reflected correctly.
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for row in forecast.get('rows') or []:
        eta = row.get('eta_seconds')
        speed = max(0, int(row.get('average_down_rate') or row.get('speed_bytes') or 0))
        if eta is None or speed <= 0:
            continue
        eta_seconds = max(0, int(eta or 0))
        rows.append({
            'hash': str(row.get('hash') or ''),
            'name': str(row.get('name') or row.get('hash') or ''),
            'progress': round(float(row.get('progress') or 0), 1),
            'remaining_bytes': max(0, int(row.get('remaining_bytes') or 0)),
            'average_down_rate': speed,
            'current_share_down_rate': max(0, int(row.get('speed_bytes') or 0)),
            'completion_seconds': eta_seconds,
            'completion_at': (now + timedelta(seconds=eta_seconds)).isoformat(),
        })
    return sorted(rows, key=lambda item: int(item.get('completion_seconds') or 0))[:100]

def _limit_simulation_timeline(timeline: list[dict[str, Any]], maximum: int = 10) -> list[dict[str, Any]]:
    """Return a stable, readable timeline limited to the UI maximum."""
    # Note: The simulator keeps the first and last moments when sampling, so long forecasts remain analyzable.
    maximum = max(1, int(maximum or 10))
    if len(timeline) <= maximum:
        return timeline
    if maximum == 1:
        return timeline[:1]
    sampled = [timeline[0]]
    span = len(timeline) - 1
    for idx in range(1, maximum - 1):
        sampled.append(timeline[round((span * idx) / (maximum - 1))])
    sampled.append(timeline[-1])
    unique: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in sampled:
        ident = id(item)
        if ident not in seen:
            unique.append(item)
            seen.add(ident)
    cursor = 0
    while len(unique) < maximum and cursor < len(timeline):
        item = timeline[cursor]
        ident = id(item)
        if ident not in seen:
            unique.append(item)
            seen.add(ident)
        cursor += 1
    return sorted(unique[:maximum], key=lambda item: int(item.get('seconds_from_now') or 0))


def dry_run(profile: dict | None = None, user_id: int | None = None, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a read-only Smart Queue execution plan for current torrents and draft settings."""
    profile = profile or active_profile()
    if not profile:
        return {'ok': False, 'error': 'No active rTorrent profile'}
    user_id = user_id or default_user_id()
    profile_id = int(profile['id'])
    current_settings = get_settings(profile_id, user_id)
    settings = _simulation_settings(current_settings, data or {})
    torrents = rtorrent.list_torrents(profile)
    user_excluded = _excluded_hashes(profile_id, user_id)
    stalled_label_hashes = {str(t.get('hash') or '') for t in torrents if _has_stalled_label(str(t.get('label') or '')) and t.get('hash')}
    manage_stopped = True
    downloading = [
        t for t in torrents
        if _is_running_download_slot(t)
        and str(t.get('hash') or '') not in user_excluded
    ]
    stopped = [
        t for t in torrents
        if str(t.get('hash') or '') not in user_excluded
        and str(t.get('hash') or '') not in stalled_label_hashes
        and _is_waiting_download_candidate(t, manage_stopped)
        and not _is_running_download_slot(t)
    ]
    max_active = max(1, int(settings.get('max_active_downloads') or 5))
    min_speed = int(settings.get('min_speed_bytes') or 0)
    min_seeds = int(settings.get('min_seeds') or 0)
    min_peers = int(settings.get('min_peers') or 0)
    stalled_seconds = int(settings.get('stalled_seconds') or 300)
    stop_batch_size = max(1, int(settings.get('stop_batch_size') or 50))
    start_grace_seconds = max(0, int(settings.get('start_grace_seconds') or 0))
    ignore_seed_peer = bool(int(settings.get('ignore_seed_peer') or 0))
    ignore_speed = bool(int(settings.get('ignore_speed') or 0))
    protect_active_below_cap = bool(int(settings.get('protect_active_below_cap', 1) or 0))
    prefer_partial_progress = bool(int(settings.get('prefer_partial_progress', 1) or 0))
    timer_key = _stalled_timer_key(min_speed, min_seeds, min_peers, stalled_seconds, ignore_seed_peer, ignore_speed)
    now_ts = datetime.now(timezone.utc).timestamp()
    start_grace_hashes = _load_active_start_grace(profile_id, start_grace_seconds, now_ts)
    simulated_speed = None
    if data and str(data.get('download_speed_bytes') or '').strip():
        try:
            simulated_speed = max(0, int(data.get('download_speed_bytes') or 0))
        except (TypeError, ValueError):
            simulated_speed = None
    simulated_current_share = int(simulated_speed / max(1, len(downloading))) if simulated_speed is not None and downloading else None

    stalled: list[dict[str, Any]] = []
    stop_eligible: list[dict[str, Any]] = []
    protected_grace: list[dict[str, Any]] = []
    warming: list[dict[str, Any]] = []
    with connect() as conn:
        for t in downloading:
            h = str(t.get('hash') or '')
            simulated = dict(t)
            if simulated_current_share is not None:
                simulated['down_rate'] = simulated_current_share
            if _is_low_activity_download(simulated, min_speed, min_seeds, min_peers, stalled_seconds, ignore_seed_peer, ignore_speed):
                stop_eligible.append(t)
            if h in start_grace_hashes:
                protected_grace.append(t)
                continue
            if _is_stalled_download(simulated, min_speed, min_seeds, min_peers, stalled_seconds, ignore_seed_peer, ignore_speed):
                row = conn.execute('SELECT first_stalled_at, timer_key FROM smart_queue_stalled WHERE profile_id=? AND torrent_hash=?', (profile_id, h)).fetchone()
                first_ts = _ts(row.get('first_stalled_at')) if row and str(row.get('timer_key') or '') == timer_key else now_ts
                remaining = max(0, int((first_ts + stalled_seconds) - now_ts))
                if remaining <= 0:
                    stalled.append(t)
                else:
                    item = _simulation_torrent_item(t, f'stalled timer: {remaining}s left', simulated_current_share)
                    item['seconds_until_stalled'] = remaining
                    warming.append(item)

    startable_stopped, source_skipped = _split_start_candidates(stopped)
    candidates = sorted(startable_stopped, key=lambda t: _start_candidate_sort_key(t, prefer_partial_progress), reverse=True)
    stalled_hashes = {str(t.get('hash') or '') for t in stalled}
    over_limit = max(0, len(downloading) - max_active)
    def dry_run_sort_speed(t: dict[str, Any]) -> int:
        return int(simulated_current_share if simulated_current_share is not None else int(t.get('down_rate') or 0))

    stop_rank = sorted(
        stop_eligible,
        key=lambda t: (
            0 if str(t.get('hash') or '') in stalled_hashes else 1,
            dry_run_sort_speed(t),
            int(t.get('seeds') or 0),
            int(t.get('peers') or 0),
        ),
    )
    capped_over_limit = min(over_limit, len(stop_rank))
    to_stop = stop_rank[:min(capped_over_limit, stop_batch_size)]
    stop_hashes = {str(t.get('hash') or '') for t in to_stop}
    remaining_stop_budget = max(0, stop_batch_size - len(to_stop))
    free_slots_before_stop = max(0, max_active - len(downloading))
    replacement_capacity = max(0, len(candidates) - free_slots_before_stop)
    stalled_replacement_allowed = not (protect_active_below_cap and len(downloading) < max_active and over_limit == 0)
    stalled_replacement_limit = min(remaining_stop_budget, replacement_capacity) if stalled_replacement_allowed else 0
    for t in stalled:
        if stalled_replacement_limit <= 0:
            break
        h = str(t.get('hash') or '')
        if h and h not in stop_hashes:
            to_stop.append(t)
            stop_hashes.add(h)
            stalled_replacement_limit -= 1
    active_after_stop = max(0, len(downloading) - len(to_stop))
    available_slots = max(0, max_active - active_after_stop)
    to_start = candidates[:available_slots]
    to_label_waiting = candidates[available_slots:]
    protected_stalled = max(0, len(stalled) - len([h for h in stop_hashes if h in stalled_hashes]))

    active_after_expected = active_after_stop + len(to_start)
    active_after_rows = [t for t in downloading if str(t.get('hash') or '') not in stop_hashes] + list(to_start)
    forecast = _build_simulation_forecast(active_after_rows, data or {}, simulated_speed)
    forecast_rates = {str(row.get('hash') or ''): int(row.get('speed_bytes') or 0) for row in forecast.get('rows') or []}
    current_rates = {str(t.get('hash') or ''): (simulated_current_share if simulated_current_share is not None else int(t.get('down_rate') or 0)) for t in downloading}

    def forecast_rate_for(t: dict[str, Any], default: int = 0) -> int:
        return int(forecast_rates.get(str(t.get('hash') or ''), default) or 0)

    def current_rate_for(t: dict[str, Any]) -> int:
        return int(current_rates.get(str(t.get('hash') or ''), int(t.get('down_rate') or 0)) or 0)

    timeline = [
        {'label': 'Now', 'seconds_from_now': 0, 'description': f'Dry run would stop {len(to_stop)} and start {len(to_start)} torrent(s).', 'kind': 'event'},
    ]
    next_stalled = min([int(item.get('seconds_until_stalled') or 0) for item in warming if int(item.get('seconds_until_stalled') or 0) > 0] or [0])
    if next_stalled:
        timeline.append({'label': 'Next stalled timer', 'seconds_from_now': next_stalled, 'description': 'At least one weak active torrent reaches the stalled threshold.', 'kind': 'event'})
    if int(settings.get('enabled') or 0):
        timeline.append({'label': 'Next full check', 'seconds_from_now': cooldown_remaining(settings), 'description': 'Automatic Smart Queue pass based on cooldown.', 'kind': 'event'})
        if int(settings.get('refill_enabled') or 0):
            timeline.append({'label': 'Next refill', 'seconds_from_now': refill_remaining(settings), 'description': 'Lightweight refill during cooldown.', 'kind': 'event'})
        if int(settings.get('surge_refill_enabled') or 0):
            timeline.append({'label': 'Next surge refill', 'seconds_from_now': surge_refill_remaining(settings), 'description': 'Optional over-cap batch refill.', 'kind': 'event'})
    timeline.extend(forecast.get('points') or [])
    timeline = _limit_simulation_timeline(sorted(timeline, key=lambda item: int(item.get('seconds_from_now') or 0)), 10)
    return {
        'ok': True,
        'dry_run': True,
        'enabled': bool(settings.get('enabled')),
        'checked': len(torrents),
        'settings': settings,
        'simulated_download_speed_bytes': simulated_speed,
        'summary': {
            'active_before': len(downloading),
            'active_after_stop': active_after_stop,
            'active_after_expected': active_after_expected,
            'max_active_downloads': max_active,
            'available_slots': available_slots,
            'over_limit': over_limit,
            'stoppable_over_limit': capped_over_limit,
            'stalled_detected': len(stalled),
            'stalled_protected': protected_stalled,
            'waiting_candidates': len(candidates),
            'waiting_labeled': len(to_label_waiting),
            'excluded': len(user_excluded),
            'excluded_stalled': len(stalled_label_hashes),
            'start_grace_protected': len(protected_grace),
            'source_skipped': len(source_skipped),
            'forecastable_downloads': forecast.get('forecastable_count', 0),
            'unforecastable_downloads': forecast.get('unforecastable_count', 0),
        },
        'planned': {
            'stop': [_simulation_torrent_item(t, 'would stop: stalled or over active limit', current_rate_for(t)) for t in to_stop],
            'start': [_simulation_torrent_item(t, 'would start: best queued candidate for an available slot', forecast_rate_for(t)) for t in to_start],
            'waiting': [_simulation_torrent_item(t, 'would remain queued and labeled for Smart Queue', 0) for t in to_label_waiting[:100]],
            'protected': [_simulation_torrent_item(t, 'protected by start grace', current_rate_for(t)) for t in protected_grace[:100]],
            'warming': warming[:100],
            'completed': _simulation_completed_rows(forecast),
            'source_skipped': [_simulation_torrent_item(t, 'not selected by source split', 0) for t in source_skipped[:100]],
        },
        'timeline': timeline,
        'forecast': forecast,
        'notes': [
            'Dry run is read-only: no settings, labels, start, stop, or history entries are changed.',
            'The plan uses current torrent data plus draft values from the Smart Queue form.',
            'Optional simulated download speed is treated as total bandwidth and divided between active downloads.',
            'Timeline forecasts use fair shared speed for simulated mode and do not mutate the live queue.',
        ],
    }

def surge_refill_remaining(settings: dict[str, Any]) -> int:
    """Return seconds until the next over-cap Surge refill may run."""
    # Note: Surge refill has its own timer because it intentionally starts more torrents than the normal cap.
    if not int(settings.get('surge_refill_enabled') or 0):
        return 0
    minutes = int(settings.get('surge_refill_interval_minutes') or 0)
    if minutes <= 0:
        return 0
    last = _ts(settings.get('last_surge_refill_at'))
    if not last:
        return 0
    return max(0, int((last + minutes * 60) - time.time()))


def _mark_surge_refill_run(profile_id: int, user_id: int) -> None:
    # Note: The over-cap refill timer is updated even when no candidates are found, preventing tight retry loops.
    with connect() as conn:
        conn.execute('UPDATE smart_queue_settings SET last_surge_refill_at=?, updated_at=? WHERE profile_id=?', (utcnow(), utcnow(), profile_id))


def _surge_refill_over_limit(profile: dict, settings: dict[str, Any], profile_id: int, user_id: int) -> dict[str, Any]:
    """Start a large user-defined batch above the Smart Queue cap, then let normal checks drain it."""
    # Note: Surge refill never raises max_active_downloads; it only overfills once per configured interval.
    torrents = rtorrent.list_torrents(profile)
    user_excluded = _excluded_hashes(profile_id, user_id)
    max_active = max(1, int(settings.get('max_active_downloads') or 5))
    batch_size = max(1, int(settings.get('surge_refill_batch_size') or 2000))
    stalled_label_hashes = {str(t.get('hash') or '') for t in torrents if _has_stalled_label(str(t.get('label') or '')) and t.get('hash')}
    downloading = [
        t for t in torrents
        if _is_running_download_slot(t)
        and str(t.get('hash') or '') not in user_excluded
    ]
    stopped = [
        t for t in torrents
        if str(t.get('hash') or '') not in user_excluded
        and str(t.get('hash') or '') not in stalled_label_hashes
        and _is_waiting_download_candidate(t, True)
        and not _is_running_download_slot(t)
    ]
    if int(settings.get('auto_stop_idle') or 0) and not downloading and not stopped:
        idle_details = {
            'decision': 'Smart Queue auto-stopped during Surge refill: no active or waiting downloads',
            'enabled': False,
            'auto_stop_idle': True,
            'surge_refill': True,
            'checked': len(torrents),
            'active_before': 0,
            'active_after_stop': 0,
            'active_after_expected': 0,
            'max_active_downloads': max_active,
            'surge_refill_batch_size': batch_size,
            'over_limit': 0,
            'stopped': [],
            'started': [],
            'start_requested': [],
            'stalled_detected': 0,
            'stalled_stopped': 0,
            'protected_stalled': 0,
            'excluded': len(user_excluded),
            'excluded_stalled': len(stalled_label_hashes),
        }
        _mark_surge_refill_run(profile_id, user_id)
        _diagnostics_write('smart_queue.surge_refill_idle', {'profile_id': profile_id, 'checked': len(torrents)}, idle_details)
        return _disable_when_idle(profile_id, user_id, torrents, idle_details)

    startable_stopped, source_skipped = _split_start_candidates(stopped)
    prefer_partial_progress = bool(int(settings.get('prefer_partial_progress', 1) or 0))
    candidates = sorted(
        startable_stopped,
        key=lambda t: _start_candidate_sort_key(t, prefer_partial_progress),
        reverse=True,
    )
    c = rtorrent.client_for(profile)
    rtorrent_cap = _ensure_rtorrent_download_cap(c, max(max_active, len(downloading) + batch_size))
    label_failed: list[str] = []
    to_start = candidates[:batch_size]
    to_label_waiting = candidates[batch_size:]

    for t in to_label_waiting:
        h = str(t.get('hash') or '')
        if not h:
            continue
        try:
            if not _mark_auto_stopped(c, profile_id, t):
                label_failed.append(h)
        except Exception:
            label_failed.append(h)

    start_summary = _start_and_verify_downloads(c, profile_id, to_start)
    active_verified = start_summary['active_verified']
    start_pending_confirmation = start_summary.get('start_pending_confirmation', [])
    start_failed = start_summary['start_failed']
    start_requested = start_summary['start_requested']
    start_results = start_summary['start_results']
    _record_start_grace(profile_id, start_requested)
    for h in start_requested:
        _restore_auto_label(c, profile_id, h, None)
        try:
            rtorrent.clear_post_check_download_label(c, h, None)
        except Exception:
            label_failed.append(h)

    keep_labels = (
        {str(t.get('hash') or '') for t in to_label_waiting}
        | {str(t.get('hash') or '') for t in stopped if _has_smart_queue_label(str(t.get('label') or '')) and str(t.get('hash') or '') not in set(start_requested)}
    )
    restored = _cleanup_auto_labels(c, profile_id, torrents, keep_labels, True)
    active_transferring = sum(1 for t in downloading if int(t.get('down_rate') or 0) > 0 or int(t.get('up_rate') or 0) > 0)
    active_rtorrent = sum(1 for t in downloading if int(t.get('active') or 0))
    active_state = sum(1 for t in downloading if int(t.get('state') or 0))
    active_after_expected = len(downloading) + len(start_requested)
    over_limit_expected = max(0, active_after_expected - max_active)
    if start_requested:
        decision = f'Surge refill requested {len(start_requested)} over-cap start(s); normal checks will drain overflow'
        blocked_reason = ''
    elif not candidates:
        decision = 'Surge refill skipped: no stopped candidates available'
        blocked_reason = 'no_candidates'
    else:
        decision = 'Surge refill ran but rTorrent did not confirm new starts yet'
        blocked_reason = 'start_not_confirmed'
    details = {
        'decision': decision,
        'blocked_reason': blocked_reason,
        'enabled': bool(settings.get('enabled')),
        'surge_refill': True,
        'surge_refill_interval_minutes': int(settings.get('surge_refill_interval_minutes') or 0),
        'surge_refill_batch_size': batch_size,
        'active_before': len(downloading),
        'active_after_expected': active_after_expected,
        'active_transferring_count': active_transferring,
        'active_rtorrent_count': active_rtorrent,
        'active_state_count': active_state,
        'max_active_downloads': max_active,
        'over_limit': over_limit_expected,
        'candidates': len(candidates),
        'started_planned': len(to_start),
        'waiting_labeled': len(to_label_waiting),
        'start_requested': start_requested,
        'start_results': start_results,
        'active_verified_count': len(active_verified),
        'pending_confirmation_count': len(start_pending_confirmation),
        'start_pending_confirmation': start_pending_confirmation,
        'start_failed': start_failed,
        'labels_failed': label_failed,
        'labels_restored': restored,
        'start_source_skipped': len(source_skipped),
        'rtorrent_cap_updated': bool(rtorrent_cap.get('updated')),
        'rtorrent_cap': rtorrent_cap,
        'excluded': len(user_excluded),
        'excluded_stalled': len(stalled_label_hashes),
    }
    _diagnostics_write(
        'smart_queue.surge_refill',
        {
            'profile_id': profile_id,
            'checked': len(torrents),
            'active_before': len(downloading),
            'active_after_expected': active_after_expected,
            'max_active_downloads': max_active,
            'over_limit': over_limit_expected,
            'batch_size': batch_size,
            'candidates': len(candidates),
            'requested': len(start_requested),
            'verified': len(active_verified),
            'pending': len(start_pending_confirmation),
            'start_failed': len(start_failed),
            'waiting_labeled': len(to_label_waiting),
            'blocked_reason': blocked_reason,
            'rtorrent_cap_updated': bool(rtorrent_cap.get('updated')),
        },
        {
            'rtorrent_cap': rtorrent_cap,
            'settings': {
                'surge_refill_interval_minutes': int(settings.get('surge_refill_interval_minutes') or 0),
                'surge_refill_batch_size': batch_size,
                'prefer_partial_progress': prefer_partial_progress,
            },
            'to_start': _diagnostics_torrents(to_start),
            'to_label_waiting': _diagnostics_torrents(to_label_waiting),
            'source_skipped': _diagnostics_torrents(source_skipped),
            'pending_confirmation': _diagnostics_sample(start_pending_confirmation),
            'start_failed': _diagnostics_sample(start_failed),
            'labels_failed': _diagnostics_sample(label_failed),
        },
    )
    _mark_surge_refill_run(profile_id, user_id)
    add_history(profile_id, 'surge_refill', [], start_requested, len(torrents), details, user_id)
    settings = get_settings(profile_id, user_id)
    return {
        'ok': True,
        'enabled': bool(settings.get('enabled')),
        'surge_refill': True,
        'cooldown_skipped': True,
        'refill_mode': _refill_mode(settings),
        'refill_remaining_seconds': refill_remaining(settings),
        'surge_refill_remaining_seconds': surge_refill_remaining(settings),
        'paused': [],
        'resumed': start_requested,
        'stopped': [],
        'started': start_requested,
        'start_requested': start_requested,
        'start_batch_size': start_summary['start_batch_size'],
        'start_verify_attempts': start_summary['start_verify_attempts'],
        'start_verify_delay_seconds': start_summary['start_verify_delay_seconds'],
        'waiting_labeled': len(to_label_waiting),
        'labels_restored': restored,
        'labels_failed': label_failed,
        'start_failed': start_failed,
        'start_no_effect': start_summary['start_no_effect'],
        'start_pending_confirmation': start_pending_confirmation,
        'active_verified': active_verified,
        'active_before': len(downloading),
        'active_after_expected': active_after_expected,
        'over_limit': over_limit_expected,
        'active_transferring_count': active_transferring,
        'active_rtorrent_count': active_rtorrent,
        'active_state_count': active_state,
        'blocked_reason': blocked_reason,
        'start_source_skipped': len(source_skipped),
        'checked': len(torrents),
        'excluded': len(user_excluded),
        'settings': settings,
    }

def mark_run(profile_id: int, user_id: int | None = None) -> None:
    user_id = user_id or default_user_id()
    with connect() as conn:
        conn.execute('UPDATE smart_queue_settings SET last_run_at=?, updated_at=? WHERE profile_id=?', (utcnow(), utcnow(), profile_id))

def _disable_when_idle(profile_id: int, user_id: int, torrents: list[dict[str, Any]], details: dict[str, Any]) -> dict[str, Any]:
    # Note: Auto-stop is intentionally profile-scoped and only flips the Smart Queue enabled flag; saved thresholds remain intact.
    now = utcnow()
    with connect() as conn:
        conn.execute('UPDATE smart_queue_settings SET enabled=0, last_run_at=?, updated_at=? WHERE profile_id=?', (now, now, profile_id))
    add_history(profile_id, 'auto_stopped_idle', [], [], len(torrents), details, user_id)
    settings = get_settings(profile_id, user_id)
    return {'ok': True, 'enabled': False, 'auto_stopped_idle': True, 'paused': [], 'resumed': [], 'stopped': [], 'started': [], 'checked': len(torrents), 'settings': settings, 'message': 'Smart Queue stopped because there is no active or waiting work.'}

def check(profile: dict | None = None, user_id: int | None = None, force: bool = False) -> dict[str, Any]:
    profile = profile or active_profile()
    if not profile:
        return {'ok': False, 'error': 'No active rTorrent profile'}
    user_id = user_id or default_user_id()
    profile_id = int(profile['id'])
    settings = get_settings(profile_id, user_id)
    remaining = cooldown_remaining(settings)
    if not force and int(settings.get('enabled') or 0) and int(settings.get('surge_refill_enabled') or 0) and not surge_refill_remaining(settings):
        try:
            return _surge_refill_over_limit(profile, settings, profile_id, user_id)
        except Exception as exc:
            return {'ok': True, 'enabled': True, 'surge_refill': False, 'settings': settings, 'error': str(exc)}
    if remaining and not force:
        if int(settings.get('enabled') or 0):
            refill_wait = refill_remaining(settings)
            if not int(settings.get('refill_enabled') or 0):
                return {'ok': True, 'enabled': True, 'cooldown_skipped': True, 'cooldown_refill': False, 'refill_disabled': True, 'cooldown_remaining_seconds': remaining, 'surge_refill_remaining_seconds': surge_refill_remaining(settings), 'settings': settings}
            if refill_wait:
                return {'ok': True, 'enabled': True, 'cooldown_skipped': True, 'cooldown_refill': False, 'refill_wait_seconds': refill_wait, 'cooldown_remaining_seconds': remaining, 'surge_refill_remaining_seconds': surge_refill_remaining(settings), 'settings': settings}
            try:
                # Note: Cooldown still blocks the full Smart Queue pass, but configured refill may fill free slots safely.
                refill = _refill_underfilled_queue(profile, settings, profile_id, user_id)
                refill['cooldown_remaining_seconds'] = remaining
                return refill
            except Exception as exc:
                return {'ok': True, 'enabled': True, 'cooldown_skipped': True, 'cooldown_refill': False, 'cooldown_remaining_seconds': remaining, 'settings': settings, 'error': str(exc)}
        return {'ok': True, 'enabled': bool(settings.get('enabled')), 'cooldown_skipped': True, 'cooldown_remaining_seconds': remaining, 'surge_refill_remaining_seconds': surge_refill_remaining(settings), 'settings': settings}
    if not force and not int(settings.get('enabled') or 0):
        restored: list[str] = []
        try:
            # Note: When Smart Queue is disabled, only technical labels are cleaned up, without starting or pausing torrents.
            torrents = rtorrent.list_torrents(profile)
            restored = _cleanup_auto_labels(rtorrent.client_for(profile), profile_id, torrents, set(), True)
        except Exception:
            restored = []
        # Note: Disabled checks are frequent poller passes; record only the first waiting-state row.
        disabled_log_recorded = _record_disabled_waiting_once(profile_id, user_id, {'labels_restored': restored})
        return {'ok': True, 'enabled': False, 'paused': [], 'resumed': [], 'stopped': [], 'started': [], 'labels_restored': restored, 'disabled_log_recorded': disabled_log_recorded, 'message': 'Smart Queue disabled, waiting for start'}

    torrents = rtorrent.list_torrents(profile)
    # Note: Stalled labels block automatic starting only; a manually started Stalled item still counts as a running slot.
    stalled_label_hashes = {str(t.get('hash') or '') for t in torrents if _has_stalled_label(str(t.get('label') or '')) and t.get('hash')}
    user_excluded = _excluded_hashes(profile_id, user_id)
    manage_stopped = True

    # Note: Count every started incomplete torrent, including items started manually and items with old Smart Queue labels.
    downloading = [
        t for t in torrents
        if _is_running_download_slot(t)
        and str(t.get('hash') or '') not in user_excluded
    ]
    # Note: Waiting candidates are stopped queue holds only; Stalled labels are not auto-started again.
    stopped = [
        t for t in torrents
        if str(t.get('hash') or '') not in user_excluded
        and str(t.get('hash') or '') not in stalled_label_hashes
        and _is_waiting_download_candidate(t, manage_stopped)
        and not _is_running_download_slot(t)
    ]
    manual_labeled_running = [
        str(t.get('hash') or '') for t in downloading
        if str(t.get('hash') or '') and _has_smart_queue_label(str(t.get('label') or ''))
    ]
    if int(settings.get('auto_stop_idle') or 0) and not downloading and not stopped:
        idle_details = {
            'decision': 'Smart Queue auto-stopped: no active or waiting downloads',
            'enabled': False,
            'auto_stop_idle': True,
            'checked': len(torrents),
            'active_before': 0,
            'active_after_stop': 0,
            'active_after_expected': 0,
            'max_active_downloads': max(1, int(settings.get('max_active_downloads') or 5)),
            'over_limit': 0,
            'stopped': [],
            'started': [],
            'start_requested': [],
            'active_verified_count': 0,
            'pending_confirmation_count': 0,
            'stalled_detected': 0,
            'stalled_stopped': 0,
            'protected_stalled': 0,
            'excluded': len(user_excluded),
            'excluded_stalled': len(stalled_label_hashes),
        }
        _diagnostics_write('smart_queue.auto_stopped_idle', {'profile_id': profile_id, 'checked': len(torrents)}, idle_details)
        return _disable_when_idle(profile_id, user_id, torrents, idle_details)
    min_speed = int(settings.get('min_speed_bytes') or 0)
    min_seeds = int(settings.get('min_seeds') or 0)
    min_peers = int(settings.get('min_peers') or 0)
    ignore_seed_peer = bool(int(settings.get('ignore_seed_peer') or 0))
    ignore_speed = bool(int(settings.get('ignore_speed') or 0))
    stalled_seconds = int(settings.get('stalled_seconds') or 300)
    stop_batch_size = max(1, int(settings.get('stop_batch_size') or 50))
    start_grace_seconds = max(0, int(settings.get('start_grace_seconds') or 0))
    protect_active_below_cap = bool(int(settings.get('protect_active_below_cap', 1) or 0))
    timer_key = _stalled_timer_key(min_speed, min_seeds, min_peers, stalled_seconds, ignore_seed_peer, ignore_speed)
    now = utcnow()
    now_ts = datetime.now(timezone.utc).timestamp()
    start_grace_hashes = _load_active_start_grace(profile_id, start_grace_seconds, now_ts)
    stalled: list[dict[str, Any]] = []
    stop_eligible: list[dict[str, Any]] = []
    # Note: Toast diagnostics count active torrents whose ignored criteria would otherwise match during this check.
    ignored_seed_peer_count = 0
    ignored_speed_count = 0

    snapshot_activity_protected: list[str] = []
    snapshot_activity_protected_hashes: set[str] = set()

    with connect() as conn:
        for t in downloading:
            # Note: Ignore switches keep matching criteria from advancing stalled cleanup while preserving diagnostics.
            if ignore_seed_peer and (int(t.get('seeds') or 0) <= max(0, int(min_seeds or 0)) or (min_peers > 0 and int(t.get('peers') or 0) <= max(0, int(min_peers or 0)))):
                ignored_seed_peer_count += 1
            if ignore_speed and int(t.get('down_rate') or 0) <= max(0, int(min_speed or 0)):
                ignored_speed_count += 1
            is_stalled = _is_stalled_download(t, min_speed, min_seeds, min_peers, stalled_seconds, ignore_seed_peer, ignore_speed)
            # Note: Hard-limit enforcement uses only non-ignored weak criteria before choosing weak items.
            if _is_low_activity_download(t, min_speed, min_seeds, min_peers, stalled_seconds, ignore_seed_peer, ignore_speed):
                stop_eligible.append(t)
            h = str(t.get('hash') or '')
            if not h:
                continue
            if h in start_grace_hashes:
                # Note: Fresh queue starts get time to announce/connect before stalled logic may stop them.
                conn.execute('DELETE FROM smart_queue_stalled WHERE profile_id=? AND torrent_hash=?', (profile_id, h))
                continue
            if is_stalled:
                row = conn.execute('SELECT first_stalled_at, timer_key FROM smart_queue_stalled WHERE profile_id=? AND torrent_hash=?', (profile_id, h)).fetchone()
                if row and str(row.get('timer_key') or '') == timer_key:
                    conn.execute('UPDATE smart_queue_stalled SET updated_at=? WHERE profile_id=? AND torrent_hash=?', (now, profile_id, h))
                    first = row['first_stalled_at']
                else:
                    # Note: A changed stalled rule starts a fresh timer, so old rows cannot instantly mark torrents as Stalled.
                    first = now
                    conn.execute('INSERT OR REPLACE INTO smart_queue_stalled(profile_id,torrent_hash,first_stalled_at,updated_at,timer_key) VALUES(?,?,?,?,?)', (profile_id, h, first, now, timer_key))
                if now_ts - _ts(first) >= stalled_seconds:
                    stalled.append(t)
            else:
                conn.execute('DELETE FROM smart_queue_stalled WHERE profile_id=? AND torrent_hash=?', (profile_id, h))

    # Note: Start candidates are not filtered by seeds/peers because those counts may be stale before announce.
    startable_stopped, source_skipped = _split_start_candidates(stopped)
    prefer_partial_progress = bool(int(settings.get('prefer_partial_progress', 1) or 0))
    candidates = sorted(
        startable_stopped,
        key=lambda t: _start_candidate_sort_key(t, prefer_partial_progress),
        reverse=True,
    )
    max_active = max(1, int(settings.get('max_active_downloads') or 5))
    stalled_hashes = {str(t.get('hash') or '') for t in stalled}

    # Enforce the active-download cap using only torrents that the current snapshot already proves idle/weak.
    # Note: A transferring or recently active torrent is never stopped just because the cap is exceeded.
    over_limit = max(0, len(downloading) - max_active)
    stop_eligible_hashes = {str(t.get('hash') or '') for t in stop_eligible}
    def dry_run_sort_speed(t: dict[str, Any]) -> int:
        return int(simulated_current_share if simulated_current_share is not None else int(t.get('down_rate') or 0))

    stop_rank = sorted(
        stop_eligible,
        key=lambda t: (
            0 if str(t.get('hash') or '') in stalled_hashes else 1,
            dry_run_sort_speed(t),
            int(t.get('seeds') or 0),
            int(t.get('peers') or 0),
        ),
    )
    capped_over_limit = min(over_limit, len(stop_rank))
    # Note: The user-defined batch limit caps all automatic stops in one pass.
    # Hard cap overflow is handled first, then stalled replacement uses only proven spare candidate capacity.
    to_stop: list[dict[str, Any]] = stop_rank[:min(capped_over_limit, stop_batch_size)]
    stop_hashes = {str(t.get('hash') or '') for t in to_stop}
    remaining_stop_budget = max(0, stop_batch_size - len(to_stop))
    free_slots_before_stop = max(0, max_active - len(downloading))
    replacement_capacity = max(0, len(candidates) - free_slots_before_stop)
    stalled_replacement_allowed = not (protect_active_below_cap and len(downloading) < max_active and over_limit == 0)
    stalled_replacement_limit = min(remaining_stop_budget, replacement_capacity) if stalled_replacement_allowed else 0

    # Note: Stalled downloads are replaced gradually. With protection enabled, below-cap checks refill first
    # and postpone stalled cleanup until the active count reaches the configured cap or overflows it.
    for t in stalled:
        if stalled_replacement_limit <= 0:
            break
        h = str(t.get('hash') or '')
        if h and h not in stop_hashes:
            to_stop.append(t)
            stop_hashes.add(h)
            stalled_replacement_limit -= 1

    protected_stalled = max(0, len(stalled) - len([h for h in stop_hashes if h in stalled_hashes]))

    c = rtorrent.client_for(profile)
    rtorrent_cap = _ensure_rtorrent_download_cap(c, max_active)
    for t in downloading:
        h = str(t.get('hash') or '')
        if not h or not _has_stalled_label(str(t.get('label') or '')):
            continue
        if _has_recent_transfer_activity(t, stalled_seconds):
            # Note: Snapshot activity is enough to remove Stalled; no per-torrent live RPC guard is needed.
            snapshot_activity_protected.append(h)
            snapshot_activity_protected_hashes.add(h)
            _clear_stalled_label(c, h, str(t.get('label') or ''))
            with connect() as conn:
                conn.execute('DELETE FROM smart_queue_stalled WHERE profile_id=? AND torrent_hash=?', (profile_id, h))
    stopped_by_queue: list[str] = []
    started_by_queue: list[str] = []
    label_failed: list[str] = []
    stalled_labeled: list[str] = []
    stop_failed: list[dict[str, str]] = []
    start_failed: list[dict[str, str]] = []
    start_no_effect: list[dict[str, Any]] = []
    start_requested: list[str] = []
    start_results: list[dict[str, Any]] = []

    for t in to_stop:
        h = str(t.get('hash') or '')
        try:
            if not h or h in snapshot_activity_protected_hashes:
                continue
            if _has_recent_transfer_activity(t, stalled_seconds):
                # Note: Snapshot activity wins; active torrents are protected without slow per-item live checks.
                snapshot_activity_protected.append(h)
                snapshot_activity_protected_hashes.add(h)
                _clear_stalled_label(c, h, str(t.get('label') or ''))
                with connect() as conn:
                    conn.execute('DELETE FROM smart_queue_stalled WHERE profile_id=? AND torrent_hash=?', (profile_id, h))
                continue
            # Note: Smart Queue stops with the same low-level d.stop command used by the manual Stop action.
            # This avoids extra pre-check RPCs and keeps large queues fast even with many candidates.
            c.call('d.stop', h)
            if h in stalled_hashes:
                if _ensure_stalled_label(c, h, _read_label(c, h, str(t.get('label') or ''))):
                    stalled_labeled.append(h)
                else:
                    label_failed.append(h)
            elif not _mark_auto_stopped(c, profile_id, t):
                label_failed.append(h)
            stopped_by_queue.append(h)
        except Exception as exc:
            # Note: Stop failures are stored in history instead of being swallowed, so queue drift is visible.
            stop_failed.append({'hash': h, 'error': str(exc)})

    active_after_stop = max(0, len(downloading) - len(stopped_by_queue))
    # Note: Starts are planned only after confirmed stops, so failed stops cannot push the queue above the cap.
    available_slots = max(0, max_active - active_after_stop)
    to_start = candidates[:available_slots]
    # Note: Items outside the current start batch are explicitly marked as pending Smart Queue items.
    to_label_waiting = candidates[available_slots:]

    for t in to_label_waiting:
        h = str(t.get('hash') or '')
        if not h or h in stop_hashes:
            continue
        try:
            if not _mark_auto_stopped(c, profile_id, t):
                label_failed.append(h)
        except Exception:
            label_failed.append(h)

    # Note: Start the whole candidate batch in one round. Remove the label after an accepted RPC,
    # because rTorrent may keep some items in its own queue with active=0 despite a valid d.start/d.resume.
    start_summary = _start_and_verify_downloads(c, profile_id, to_start)
    active_verified = start_summary['active_verified']
    start_no_effect = start_summary['start_no_effect']
    start_pending_confirmation = start_summary.get('start_pending_confirmation', [])
    start_failed = start_summary['start_failed']
    start_requested = start_summary['start_requested']
    start_results = start_summary['start_results']
    _record_start_grace(profile_id, start_requested)
    for h in start_requested:
        _restore_auto_label(c, profile_id, h, None)
        try:
            # Note: Once Smart Queue starts a post-check torrent, its temporary download-after-check label is no longer needed.
            rtorrent.clear_post_check_download_label(c, h, None)
        except Exception:
            label_failed.append(h)
    # Note: History shows accepted Smart Queue starts; active_verified shows items already visible as started in rTorrent.
    started_by_queue = list(start_requested)
    keep_labels = (
        set(stopped_by_queue)
        | {str(t.get('hash') or '') for t in to_label_waiting}
        | {str(t.get('hash') or '') for t in stopped if _has_smart_queue_label(str(t.get('label') or '')) and str(t.get('hash') or '') not in set(started_by_queue)}
    )
    restored = _cleanup_auto_labels(c, profile_id, torrents, keep_labels, manage_stopped)
    stalled_stopped_hashes = [h for h in stopped_by_queue if h in stalled_hashes]
    # Note: Smart Queue history now stores a compact decision summary while keeping enough hashes to audit Stalled actions.
    details = {
        'decision': _decision_text(len(stopped_by_queue), len(started_by_queue), len(stalled), len(stalled_stopped_hashes), protected_stalled),
        'enabled': bool(settings.get('enabled')),
        'checked': len(torrents),
        'max_active_downloads': max_active,
        'prefer_partial_progress': prefer_partial_progress,
        'active_before': len(downloading),
        'active_after_stop': active_after_stop,
        'active_after_expected': active_after_stop + len(started_by_queue),
        'over_limit': over_limit,
        'stoppable_over_limit': capped_over_limit,
        'stopped': stopped_by_queue,
        'started': started_by_queue,
        'start_requested': start_requested,
        'active_verified_count': len(active_verified),
        'pending_confirmation_count': len(start_pending_confirmation),
        'start_failed_count': len(start_failed),
        'stop_failed_count': len(stop_failed),
        'label_failed_count': len(label_failed),
        'waiting_labeled': len(to_label_waiting),
        'stalled_detected': len(stalled),
        'stalled_hashes': _hash_sample(stalled_hashes),
        'stalled_stopped': len(stalled_stopped_hashes),
        'stalled_stopped_hashes': _hash_sample(stalled_stopped_hashes),
        'stalled_labeled': stalled_labeled,
        'protected_stalled': protected_stalled,
        'snapshot_activity_protected': len(snapshot_activity_protected),
        'snapshot_activity_protected_hashes': _hash_sample(snapshot_activity_protected),
        'stalled_replacement_allowed': stalled_replacement_allowed,
        'excluded': len(user_excluded),
        'excluded_stalled': len(stalled_label_hashes),
        'manual_labeled_running': len(manual_labeled_running),
        'labels_restored_count': len(restored),
        'start_source_skipped': len(source_skipped),
        'ignore_seed_peer': ignore_seed_peer,
        'ignore_speed': ignore_speed,
        'ignored_seed_peer_count': ignored_seed_peer_count if ignore_seed_peer else 0,
        'ignored_speed_count': ignored_speed_count if ignore_speed else 0,
        'stalled_seconds': stalled_seconds,
        'stop_batch_size': stop_batch_size,
        'start_grace_seconds': start_grace_seconds,
        'start_grace_protected': len(start_grace_hashes),
        'replacement_capacity': replacement_capacity,
        'rtorrent_cap_updated': bool(rtorrent_cap.get('updated')),
        'rtorrent_cap': rtorrent_cap,
        'stop_failed': stop_failed,
        'start_failed': start_failed,
        'labels_failed': label_failed,
    }
    _diagnostics_write(
        'smart_queue.force_check' if force else 'smart_queue.auto_check',
        {
            'profile_id': profile_id,
            'force': bool(force),
            'checked': len(torrents),
            'active_before': len(downloading),
            'active_after_stop': active_after_stop,
            'active_after_expected': active_after_stop + len(started_by_queue),
            'max_active_downloads': max_active,
            'over_limit': over_limit,
            'stoppable_over_limit': capped_over_limit,
            'stopped': len(stopped_by_queue),
            'stalled': len(stalled),
            'protected_stalled': protected_stalled,
            'snapshot_activity_protected': len(snapshot_activity_protected),
            'stalled_stopped': len(stalled_stopped_hashes),
            'stalled_stopped_hashes': _hash_sample(stalled_stopped_hashes, 20),
            'stop_eligible': len(stop_eligible),
            'candidates': len(candidates),
            'available_slots': available_slots,
            'requested': len(start_requested),
            'verified': len(active_verified),
            'pending': len(start_pending_confirmation),
            'pending_reasons': _pending_reason_counts(start_pending_confirmation),
            'start_failed': len(start_failed),
            'no_effect': len(start_no_effect),
            'waiting_labeled': len(to_label_waiting),
            'start_source_skipped': len(source_skipped),
            'labels_failed': len(label_failed),
            'stop_failed': len(stop_failed),
        },
        {
            'settings': {
                'min_speed_bytes': min_speed,
                'min_seeds': min_seeds,
                'min_peers': min_peers,
                'ignore_seed_peer': ignore_seed_peer,
                'ignore_speed': ignore_speed,
                'stalled_seconds': stalled_seconds,
                'stop_batch_size': stop_batch_size,
                'start_grace_seconds': start_grace_seconds,
                'protect_active_below_cap': protect_active_below_cap,
                'auto_stop_idle': bool(int(settings.get('auto_stop_idle') or 0)),
                'prefer_partial_progress': prefer_partial_progress,
            },
            'rtorrent_cap': rtorrent_cap,
            'to_stop': _diagnostics_torrents(to_stop),
            'snapshot_activity_protected': _diagnostics_sample(snapshot_activity_protected),
            'stalled': _diagnostics_torrents(stalled),
            'stop_eligible': _diagnostics_torrents(stop_eligible),
            'to_start': _diagnostics_torrents(to_start),
            'to_label_waiting': _diagnostics_torrents(to_label_waiting),
            'source_skipped': _diagnostics_torrents(source_skipped),
            'pending_confirmation': _diagnostics_sample(start_pending_confirmation),
            'start_failed': _diagnostics_sample(start_failed),
            'stop_failed': _diagnostics_sample(stop_failed),
            'start_results': _diagnostics_sample(start_results),
            'manual_labeled_running': _diagnostics_sample(manual_labeled_running),
            'labels_failed': _diagnostics_sample(label_failed),
        },
    )
    add_history(profile_id, 'force_check' if force else 'auto_check', stopped_by_queue, started_by_queue, len(torrents), {**details, 'stopped': stopped_by_queue, 'started': started_by_queue}, user_id)
    mark_run(profile_id, user_id)
    settings = get_settings(profile_id, user_id)
    remaining = cooldown_remaining(settings)
    return {'ok': True, 'enabled': bool(settings.get('enabled')), 'paused': stopped_by_queue, 'resumed': started_by_queue, 'stopped': stopped_by_queue, 'started': started_by_queue, 'start_requested': start_requested, 'start_batch_size': start_summary['start_batch_size'], 'start_verify_attempts': start_summary['start_verify_attempts'], 'start_verify_delay_seconds': start_summary['start_verify_delay_seconds'], 'waiting_labeled': len(to_label_waiting), 'stalled_labeled': stalled_labeled, 'excluded_stalled': len(stalled_label_hashes), 'manual_labeled_running': len(manual_labeled_running), 'labels_restored': restored, 'labels_failed': label_failed, 'stop_failed': stop_failed, 'start_failed': start_failed, 'start_no_effect': start_no_effect, 'start_pending_confirmation': start_pending_confirmation, 'active_verified': active_verified, 'active_before': len(downloading), 'active_after_stop': active_after_stop, 'over_limit': over_limit, 'stoppable_over_limit': capped_over_limit, 'stop_eligible': len(stop_eligible), 'start_source_skipped': len(source_skipped), 'ignore_seed_peer': ignore_seed_peer, 'ignore_speed': ignore_speed, 'ignored_seed_peer_count': ignored_seed_peer_count if ignore_seed_peer else 0, 'ignored_speed_count': ignored_speed_count if ignore_speed else 0, 'stalled_seconds': stalled_seconds, 'stalled_timer_key': timer_key, 'stop_batch_size': stop_batch_size, 'start_grace_seconds': start_grace_seconds, 'protect_active_below_cap': protect_active_below_cap, 'prefer_partial_progress': prefer_partial_progress, 'auto_stop_idle': bool(int(settings.get('auto_stop_idle') or 0)), 'stalled_replacement_allowed': stalled_replacement_allowed, 'start_grace_protected': len(start_grace_hashes), 'replacement_capacity': replacement_capacity, 'protected_stalled': protected_stalled, 'healthy_active_protected': len(snapshot_activity_protected), 'snapshot_activity_protected': snapshot_activity_protected, 'rtorrent_cap': rtorrent_cap, 'checked': len(torrents), 'excluded': len(user_excluded), 'settings': settings, 'cooldown_remaining_seconds': remaining, 'surge_refill_remaining_seconds': surge_refill_remaining(settings)}
