from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from . import smart_queue as queue


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
            merged[key] = queue._int_setting(data, current, key, default, minimum)
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
        merged['refill_interval_minutes'] = queue._int_setting(data, current, 'refill_interval_minutes', 5, 1) if refill_mode == 'custom' else 0
    return merged


def _simulation_torrent_item(t: dict[str, Any], reason: str = '', effective_down_rate: int | None = None) -> dict[str, Any]:
    """Build a compact torrent row for the Smart Queue simulator UI."""
    # Note: Hashes are kept because they are the only stable link between the dry-run and the live torrent list.
    down_rate = int(t.get('down_rate') or 0)
    return {
        'hash': str(t.get('hash') or ''),
        'name': str(t.get('name') or t.get('hash') or ''),
        'status': str(t.get('status') or ''),
        'progress': queue._progress_value(t),
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
    profile = profile or queue.active_profile()
    if not profile:
        return {'ok': False, 'error': 'No active rTorrent profile'}
    user_id = user_id or queue.default_user_id()
    profile_id = int(profile['id'])
    current_settings = queue.get_settings(profile_id, user_id)
    settings = _simulation_settings(current_settings, data or {})
    torrents = queue.rtorrent.list_torrents(profile)
    user_excluded = queue._excluded_hashes(profile_id, user_id)
    stalled_label_hashes = {str(t.get('hash') or '') for t in torrents if queue._has_stalled_label(str(t.get('label') or '')) and t.get('hash')}
    manage_stopped = True
    downloading = [
        t for t in torrents
        if queue._is_running_download_slot(t)
        and str(t.get('hash') or '') not in user_excluded
    ]
    stopped = [
        t for t in torrents
        if str(t.get('hash') or '') not in user_excluded
        and str(t.get('hash') or '') not in stalled_label_hashes
        and queue._is_waiting_download_candidate(t, manage_stopped)
        and not queue._is_running_download_slot(t)
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
    timer_key = queue._stalled_timer_key(min_speed, min_seeds, min_peers, stalled_seconds, ignore_seed_peer, ignore_speed)
    now_ts = datetime.now(timezone.utc).timestamp()
    start_grace_hashes = queue._load_active_start_grace(profile_id, start_grace_seconds, now_ts)
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
    with queue.connect() as conn:
        for t in downloading:
            h = str(t.get('hash') or '')
            simulated = dict(t)
            if simulated_current_share is not None:
                simulated['down_rate'] = simulated_current_share
            if queue._is_low_activity_download(simulated, min_speed, min_seeds, min_peers, stalled_seconds, ignore_seed_peer, ignore_speed):
                stop_eligible.append(t)
            if h in start_grace_hashes:
                protected_grace.append(t)
                continue
            if queue._is_stalled_download(simulated, min_speed, min_seeds, min_peers, stalled_seconds, ignore_seed_peer, ignore_speed):
                row = conn.execute('SELECT first_stalled_at, timer_key FROM smart_queue_stalled WHERE profile_id=? AND torrent_hash=?', (profile_id, h)).fetchone()
                first_ts = queue._ts(row.get('first_stalled_at')) if row and str(row.get('timer_key') or '') == timer_key else now_ts
                remaining = max(0, int((first_ts + stalled_seconds) - now_ts))
                if remaining <= 0:
                    stalled.append(t)
                else:
                    item = _simulation_torrent_item(t, f'stalled timer: {remaining}s left', simulated_current_share)
                    item['seconds_until_stalled'] = remaining
                    warming.append(item)

    startable_stopped, source_skipped = queue._split_start_candidates(stopped)
    candidates = sorted(startable_stopped, key=lambda t: queue._start_candidate_sort_key(t, prefer_partial_progress), reverse=True)
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
        timeline.append({'label': 'Next full check', 'seconds_from_now': queue.cooldown_remaining(settings), 'description': 'Automatic Smart Queue pass based on cooldown.', 'kind': 'event'})
        if int(settings.get('refill_enabled') or 0):
            timeline.append({'label': 'Next refill', 'seconds_from_now': queue.refill_remaining(settings), 'description': 'Lightweight refill during cooldown.', 'kind': 'event'})
        if int(settings.get('surge_refill_enabled') or 0):
            timeline.append({'label': 'Next surge refill', 'seconds_from_now': queue.surge_refill_remaining(settings), 'description': 'Optional over-cap batch refill.', 'kind': 'event'})
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
