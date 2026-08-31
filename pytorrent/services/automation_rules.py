from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
import json
import threading
import uuid
from ..db import connect, default_user_id, utcnow
from . import rtorrent, auth
from .preferences import active_profile, get_profile, get_profile_for_system, get_disk_monitor_preferences, get_profile_disk_monitor_preferences
from .workers import enqueue

AUTOMATION_JOB_CHUNK_SIZE = 100
AUTOMATION_LIGHT_ACTIONS = {'start', 'stop', 'pause', 'resume', 'set_label'}
_CHECK_LOCKS: dict[tuple[int, int | None], threading.Lock] = {}
_CHECK_LOCKS_GUARD = threading.Lock()


def _check_lock(profile_id: int, rule_id: int | None = None) -> threading.Lock:
    """Prevent overlapping automation runs for the same profile or rule."""
    key = (int(profile_id), int(rule_id) if rule_id is not None else None)
    with _CHECK_LOCKS_GUARD:
        if key not in _CHECK_LOCKS:
            _CHECK_LOCKS[key] = threading.Lock()
        return _CHECK_LOCKS[key]


def _resolve_user_id(profile: dict[str, Any] | None = None, user_id: int | None = None) -> int:
    """Return the user acting now; background execution falls back to the profile owner."""
    if user_id:
        return int(user_id)
    request_user_id = auth.current_user_id()
    if request_user_id:
        return int(request_user_id)
    if profile and profile.get('user_id'):
        return int(profile.get('user_id') or 0)
    return int(default_user_id())


def _loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value or '')
    except Exception:
        return default


def _ts(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except Exception:
        return 0.0


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _label_names(value: str | None) -> list[str]:
    seen = []
    for part in str(value or '').replace(';', ',').replace('|', ',').split(','):
        item = part.strip()
        if item and item not in seen:
            seen.append(item)
    return seen


def _label_value(labels: list[str]) -> str:
    out = []
    for label in labels:
        label = str(label or '').strip()
        if label and label not in out:
            out.append(label)
    return ', '.join(out)


def _rule_row(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item['conditions'] = _loads(item.pop('conditions_json', '[]'), [])
    item['effects'] = _loads(item.pop('effects_json', '[]'), [])
    item['created_by_user_id'] = int(item.get('created_by_user_id') or 0)
    item['created_by_username'] = str(item.get('created_by_username') or '').strip()
    item['created_by_display_name'] = str(item.get('created_by_display_name') or '').strip()
    item['created_by_label'] = item['created_by_display_name'] or item['created_by_username'] or (f"user #{item['created_by_user_id']}" if item['created_by_user_id'] else '')
    # Backward-compatible aliases. These identify the creator, not an owner/authorization principal.
    item['owner_user_id'] = item['created_by_user_id']
    item['owner_username'] = item['created_by_username']
    item['owner_display_name'] = item['created_by_display_name']
    item['owner_label'] = item['created_by_label']
    return item


def _require_profile_read(profile_id: int, user_id: int | None = None) -> int:
    viewer_id = _resolve_user_id(user_id=user_id)
    if not auth.can_access_profile(profile_id, viewer_id):
        raise PermissionError('No access to profile')
    return viewer_id


def _require_profile_write(profile_id: int, user_id: int | None = None) -> int:
    viewer_id = _resolve_user_id(user_id=user_id)
    if not auth.can_write_profile(profile_id, viewer_id):
        raise PermissionError('No write access to profile')
    return viewer_id


def _select_rules_sql(where_sql: str) -> str:
    return f'''
        SELECT
            r.*,
            u.username AS created_by_username,
            COALESCE(u.display_name, '') AS created_by_display_name
        FROM automation_rules r
        LEFT JOIN users u ON u.id = r.created_by_user_id
        WHERE {where_sql}
        ORDER BY r.enabled DESC, r.name COLLATE NOCASE
    '''


def _decorate_rule_state(rules: list[dict[str, Any]], profile_id: int | None) -> None:
    if profile_id is None:
        return
    with connect() as conn:
        for rule in rules:
            row = conn.execute(
                'SELECT last_applied_at FROM automation_rule_state WHERE rule_id=? AND profile_id=? AND torrent_hash=?',
                (rule['id'], profile_id, '__rule__'),
            ).fetchone()
            last = row.get('last_applied_at') if row else None
            cooldown = int(rule.get('cooldown_minutes') or 0)
            remaining = max(0, int((_ts(last) + cooldown * 60) - _now_ts())) if last and cooldown > 0 else 0
            rule['last_applied_at'] = last
            rule['cooldown_remaining_seconds'] = remaining


def list_rules(profile_id: int | None = None, user_id: int | None = None) -> list[dict[str, Any]]:
    if profile_id is None:
        profile = active_profile(user_id=user_id)
        profile_id = int(profile['id']) if profile else None
    if profile_id is None:
        return []
    _require_profile_read(profile_id, user_id)
    with connect() as conn:
        rows = conn.execute(_select_rules_sql('r.profile_id=?'), (profile_id,)).fetchall()
    rules = [_rule_row(r) for r in rows]
    _decorate_rule_state(rules, profile_id)
    return rules


def _list_enabled_rules_for_profile(profile_id: int, rule_id: int | None = None, force: bool = False) -> list[dict[str, Any]]:
    params: list[Any] = [profile_id]
    clauses = ['r.profile_id=?']
    if rule_id is not None:
        clauses.append('r.id=?')
        params.append(int(rule_id))
    if not force:
        clauses.append('r.enabled=1')
    with connect() as conn:
        rows = conn.execute(_select_rules_sql(' AND '.join(clauses)), tuple(params)).fetchall()
    rules = [_rule_row(r) for r in rows]
    _decorate_rule_state(rules, profile_id)
    return rules


def get_rule(rule_id: int, profile_id: int, user_id: int | None = None) -> dict[str, Any]:
    _require_profile_read(profile_id, user_id)
    with connect() as conn:
        row = conn.execute(_select_rules_sql('r.id=? AND r.profile_id=?'), (rule_id, profile_id)).fetchone()
    if not row:
        raise ValueError('Rule not found')
    rule = _rule_row(row)
    _decorate_rule_state([rule], profile_id)
    return rule


def _portable_rule(rule: dict[str, Any]) -> dict[str, Any]:
    conditions = rule.get('conditions') or []
    effects = rule.get('effects') or []
    return {
        'name': str(rule.get('name') or 'Automation rule'),
        'enabled': bool(rule.get('enabled', True)),
        'cooldown_minutes': max(0, int(rule.get('cooldown_minutes') or 0)),
        'conditions': list(conditions) if isinstance(conditions, list) else [],
        'effects': list(effects) if isinstance(effects, list) else [],
    }


def _validate_management_effects(profile_id: int, effects: list[dict[str, Any]], actor_id: int, *, enabled: bool = True) -> None:
    """Validate cross-profile effects while an authorized user creates or changes a rule."""
    # Note: Disabled rules may always be saved so access loss cannot prevent emergency disable; target RW is rechecked before enabling.
    for effect in effects:
        if not isinstance(effect, dict) or str(effect.get('type') or '') != 'profile_transfer':
            continue
        target_id = int(effect.get('target_profile_id') or 0)
        if not target_id or target_id == int(profile_id):
            raise ValueError('Automation target profile is invalid')
        if enabled and (not auth.can_write_profile(target_id, actor_id) or not get_profile(target_id, actor_id)):
            raise PermissionError('Write access to automation target profile is required')


def export_rules(profile_id: int, user_id: int | None = None) -> dict[str, Any]:
    rules = [_portable_rule(rule) for rule in list_rules(profile_id, user_id)]
    return {'version': 1, 'app': 'pyTorrent', 'exported_at': utcnow(), 'scope': 'profile', 'rules': rules}


def import_rules(profile_id: int, payload: dict[str, Any] | list[Any], user_id: int | None = None, replace: bool = False) -> list[dict[str, Any]]:
    actor_id = _require_profile_write(profile_id, user_id)
    raw_rules = payload if isinstance(payload, list) else payload.get('rules', []) if isinstance(payload, dict) else []
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ValueError('Import file does not contain automation rules')

    # Normalize and validate every rule before the first DELETE/INSERT. This keeps replace imports all-or-nothing.
    normalized: list[dict[str, Any]] = []
    for raw in raw_rules:
        if not isinstance(raw, dict):
            continue
        rule = _portable_rule(raw)
        if not isinstance(rule.get('conditions'), list) or not rule.get('conditions'):
            raise ValueError('Rule needs at least one condition')
        if not isinstance(rule.get('effects'), list) or not rule.get('effects'):
            raise ValueError('Rule needs at least one effect')
        _validate_management_effects(profile_id, rule['effects'], actor_id, enabled=bool(rule.get('enabled', True)))
        normalized.append(rule)
    if not normalized:
        raise ValueError('No valid automation rules found')

    now = utcnow()
    imported_ids: list[int] = []
    with connect() as conn:
        if replace:
            conn.execute('DELETE FROM automation_rule_state WHERE profile_id=?', (profile_id,))
            conn.execute('DELETE FROM automation_rules WHERE profile_id=?', (profile_id,))
        for rule in normalized:
            cur = conn.execute(
                'INSERT INTO automation_rules(created_by_user_id,updated_by_user_id,profile_id,name,enabled,conditions_json,effects_json,cooldown_minutes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (
                    actor_id, actor_id, profile_id, str(rule.get('name') or 'Automation rule').strip() or 'Automation rule',
                    1 if rule.get('enabled', True) else 0, json.dumps(rule.get('conditions') or []),
                    json.dumps(rule.get('effects') or []), max(0, int(rule.get('cooldown_minutes') or 0)), now, now,
                ),
            )
            imported_ids.append(int(cur.lastrowid))

    return [get_rule(rule_id, profile_id, actor_id) for rule_id in imported_ids]


def normalize_rules_for_restore(profile_id: int, raw_rules: list[dict[str, Any]], user_id: int) -> list[dict[str, Any]]:
    """Normalize profile-backup automation rows through current management authorization rules."""
    # Note: Backup restore never writes raw profile_transfer rules; enabled cross-profile rules must pass target RW again.
    normalized: list[dict[str, Any]] = []
    for raw in raw_rules or []:
        if not isinstance(raw, dict):
            continue
        source = dict(raw)
        if 'conditions' not in source:
            try: source['conditions'] = json.loads(source.get('conditions_json') or '[]')
            except Exception: source['conditions'] = []
        if 'effects' not in source:
            try: source['effects'] = json.loads(source.get('effects_json') or '[]')
            except Exception: source['effects'] = []
        rule = _portable_rule(source)
        if not rule['conditions'] or not rule['effects']:
            continue
        _validate_management_effects(profile_id, rule['effects'], int(user_id), enabled=bool(rule.get('enabled', True)))
        normalized.append(rule)
    return normalized


def save_rule(profile_id: int, data: dict[str, Any], user_id: int | None = None) -> dict[str, Any]:
    actor_id = _require_profile_write(profile_id, user_id)
    name = str(data.get('name') or 'Automation rule').strip() or 'Automation rule'
    conditions = data.get('conditions') or []
    effects = data.get('effects') or []
    if not isinstance(conditions, list) or not conditions:
        raise ValueError('Rule needs at least one condition')
    if not isinstance(effects, list) or not effects:
        raise ValueError('Rule needs at least one effect')
    enabled = 1 if data.get('enabled', True) else 0
    _validate_management_effects(profile_id, effects, actor_id, enabled=bool(enabled))
    cooldown = max(0, int(data.get('cooldown_minutes') or 0))
    now = utcnow()
    rule_id = int(data.get('id') or 0)
    with connect() as conn:
        if rule_id:
            cur = conn.execute(
                'UPDATE automation_rules SET name=?, enabled=?, conditions_json=?, effects_json=?, cooldown_minutes=?, updated_by_user_id=?, updated_at=? WHERE id=? AND profile_id=?',
                (name, enabled, json.dumps(conditions), json.dumps(effects), cooldown, actor_id, now, rule_id, profile_id),
            )
            if not cur.rowcount:
                raise ValueError('Rule not found')
        else:
            cur = conn.execute(
                'INSERT INTO automation_rules(created_by_user_id,updated_by_user_id,profile_id,name,enabled,conditions_json,effects_json,cooldown_minutes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (actor_id, actor_id, profile_id, name, enabled, json.dumps(conditions), json.dumps(effects), cooldown, now, now),
            )
            rule_id = int(cur.lastrowid)
    return get_rule(rule_id, profile_id, actor_id)


def delete_rule(rule_id: int, profile_id: int, user_id: int | None = None) -> None:
    _require_profile_write(profile_id, user_id)
    with connect() as conn:
        existing = conn.execute('SELECT id FROM automation_rules WHERE id=? AND profile_id=?', (rule_id, profile_id)).fetchone()
        if not existing:
            raise ValueError('Rule not found')
        # Child/runtime state is removed first; audit history keeps the rule name but not a dangling id.
        conn.execute('DELETE FROM automation_rule_state WHERE rule_id=? AND profile_id=?', (rule_id, profile_id))
        conn.execute('UPDATE automation_history SET rule_id=NULL WHERE rule_id=? AND profile_id=?', (rule_id, profile_id))
        conn.execute('DELETE FROM automation_rules WHERE id=? AND profile_id=?', (rule_id, profile_id))


def list_history(profile_id: int, user_id: int | None = None, limit: int = 30) -> list[dict[str, Any]]:
    """Return automation history with an explicit human/system execution identity."""
    # Note: Scheduler rows remain attributable to System even when a legacy FK stores a profile-owner user id.
    _require_profile_read(profile_id, user_id)
    with connect() as conn:
        rows = conn.execute('''
            SELECT
                h.*,
                u.username AS owner_username,
                COALESCE(u.display_name, '') AS owner_display_name
            FROM automation_history h
            LEFT JOIN users u ON u.id = h.user_id
            WHERE h.profile_id=?
            ORDER BY h.created_at DESC
            LIMIT ?
        ''', (profile_id, max(1, min(int(limit or 30), 100)))).fetchall()
    public_rows = []
    for raw in rows:
        row = dict(raw)
        if str(row.get('actor_type') or 'user') == 'system':
            row['executed_by_user_id'] = None
            row['owner_username'] = 'system'
            row['owner_display_name'] = 'System'
        else:
            row['executed_by_user_id'] = int(row.get('user_id') or 0) or None
        public_rows.append(row)
    return public_rows


def clear_history(profile_id: int, user_id: int | None = None) -> int:
    _require_profile_write(profile_id, user_id)
    with connect() as conn:
        cur = conn.execute('DELETE FROM automation_history WHERE profile_id=?', (profile_id,))
        return int(cur.rowcount or 0)


def _condition_true(t: dict[str, Any], cond: dict[str, Any]) -> bool:
    typ = str(cond.get('type') or '')
    if typ == 'completed': return bool(int(t.get('complete') or 0))
    if typ == 'no_seeds': return int(t.get('seeds') or 0) <= int(cond.get('seeds') or 0)
    if typ == 'ratio_gte': return float(t.get('ratio') or 0) >= float(cond.get('ratio') or 0)
    if typ == 'progress_gte': return float(t.get('progress') or 0) >= float(cond.get('progress') or 0)
    if typ == 'progress_lte': return float(t.get('progress') or 0) <= float(cond.get('progress') or 0)
    if typ == 'label_missing': return str(cond.get('label') or '').strip() not in _label_names(t.get('label'))
    if typ == 'label_has': return str(cond.get('label') or '').strip() in _label_names(t.get('label'))
    if typ == 'status': return str(t.get('status') or '').lower() == str(cond.get('status') or '').lower()
    if typ == 'path_contains': return str(cond.get('text') or '').lower() in str(t.get('path') or '').lower()
    return False


def _conditions_match(conn, rule: dict[str, Any], profile_id: int, t: dict[str, Any]) -> bool:
    h = str(t.get('hash') or '')
    if not h: return False
    immediate_ok = True; delayed_ok = True; now = utcnow(); now_ts = _now_ts()
    for cond in rule.get('conditions') or []:
        raw_ok = _condition_true(t, cond)
        negated = bool(cond.get('negate'))
        ok = (not raw_ok) if negated else raw_ok
        if cond.get('type') == 'no_seeds' and int(cond.get('minutes') or 0) > 0 and not negated:
            row = conn.execute('SELECT condition_since_at FROM automation_rule_state WHERE rule_id=? AND profile_id=? AND torrent_hash=?', (rule['id'], profile_id, h)).fetchone()
            since = row.get('condition_since_at') if row else None
            if raw_ok:
                if not since:
                    conn.execute('INSERT INTO automation_rule_state(rule_id,profile_id,torrent_hash,condition_since_at,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(rule_id,profile_id,torrent_hash) DO UPDATE SET condition_since_at=excluded.condition_since_at, updated_at=excluded.updated_at', (rule['id'], profile_id, h, now, now))
                    since = now
                delayed_ok = delayed_ok and (_ts(since) + int(cond.get('minutes') or 0) * 60 <= now_ts)
            else:
                conn.execute('INSERT INTO automation_rule_state(rule_id,profile_id,torrent_hash,condition_since_at,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(rule_id,profile_id,torrent_hash) DO UPDATE SET condition_since_at=NULL, updated_at=excluded.updated_at', (rule['id'], profile_id, h, None, now))
                delayed_ok = False
        else:
            immediate_ok = immediate_ok and ok
    return immediate_ok and delayed_ok


def _cooldown_ok(conn, rule: dict[str, Any], profile_id: int) -> bool:
    cooldown = int(rule.get('cooldown_minutes') or 0)
    if cooldown <= 0: return True
    row = conn.execute('SELECT last_applied_at FROM automation_rule_state WHERE rule_id=? AND profile_id=? AND torrent_hash=?', (rule['id'], profile_id, '__rule__')).fetchone()
    last = row.get('last_applied_at') if row else None
    return not last or (_ts(last) + cooldown * 60 <= _now_ts())


def _mark_rule_cooldown(conn, rule: dict[str, Any], profile_id: int, now: str) -> None:
    conn.execute('INSERT INTO automation_rule_state(rule_id,profile_id,torrent_hash,last_applied_at,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(rule_id,profile_id,torrent_hash) DO UPDATE SET last_applied_at=excluded.last_applied_at, updated_at=excluded.updated_at', (rule['id'], profile_id, '__rule__', now, now))


def _chunk_hashes(hashes: list[str], size: int = AUTOMATION_JOB_CHUNK_SIZE) -> list[list[str]]:
    safe_size = max(1, int(size or AUTOMATION_JOB_CHUNK_SIZE))
    return [hashes[index:index + safe_size] for index in range(0, len(hashes), safe_size)]


def _job_context(rule: dict[str, Any], eff_type: str, hashes: list[str], torrents_by_hash: dict[str, dict[str, Any]], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    ctx = {
        'source': 'automation',
        'rule_id': rule.get('id'),
        'rule_name': str(rule.get('name') or ''),
        'rule_created_by_user_id': int(rule.get('created_by_user_id') or rule.get('owner_user_id') or 0),
        'rule_created_by': str(rule.get('created_by_label') or rule.get('owner_label') or ''),
        'effect': eff_type,
        'bulk': len(hashes) > 1,
        'hash_count': len(hashes),
        'requested_at': utcnow(),
        'items': [
            {
                'hash': h,
                'name': str((torrents_by_hash.get(h) or {}).get('name') or ''),
                'path': str((torrents_by_hash.get(h) or {}).get('path') or ''),
            }
            for h in hashes
        ],
    }
    if extra:
        ctx.update(extra)
    return ctx


def _enqueue_automation_job(profile: dict[str, Any], rule: dict[str, Any], action_name: str, hashes: list[str], payload: dict[str, Any], torrents_by_hash: dict[str, dict[str, Any]], user_id: int | None = None, context_extra: dict[str, Any] | None = None, system_execution: bool = False, execution_id: str = '') -> list[str]:
    """Queue automation effects while preserving the run's authorization scope."""
    # Note: Scheduler authority is stored in the job row, never in user-controlled payload JSON.
    job_ids: list[str] = []
    chunks = [hashes] if action_name in AUTOMATION_LIGHT_ACTIONS else _chunk_hashes(hashes)
    for index, chunk in enumerate(chunks, start=1):
        part_payload = dict(payload or {})
        part_payload['hashes'] = chunk
        part_payload['source'] = 'automation'
        if action_name not in AUTOMATION_LIGHT_ACTIONS:
            part_payload['requires_order'] = True
        extra = dict(context_extra or {})
        if execution_id:
            extra['automation_execution_id'] = str(execution_id)
            # Note: Execution state is sealed only after every effect job has been queued successfully, preventing an early fast job from committing cooldown before sibling jobs exist.
            extra['automation_execution_scheduled'] = False
        if len(chunks) > 1:
            extra.update({'bulk_label': f'automation-{index}', 'bulk_part': index, 'bulk_parts': len(chunks), 'parent_hash_count': len(hashes)})
        if action_name == 'move':
            extra.update({'target_path': str(part_payload.get('path') or ''), 'move_data': bool(part_payload.get('move_data'))})
        if action_name == 'profile_transfer':
            extra.update({'target_profile_id': int(part_payload.get('target_profile_id') or 0), 'target_path': str(part_payload.get('target_path') or ''), 'move_data': bool(part_payload.get('move_data')), 'post_action': str(part_payload.get('post_action') or 'current')})
        if action_name == 'remove':
            extra.update({'remove_data': bool(part_payload.get('remove_data'))})
        effect_type = str(context_extra.get('effect_type') if context_extra else action_name)
        part_payload['job_context'] = _job_context(rule, effect_type, chunk, torrents_by_hash, extra)
        job_ids.append(enqueue(action_name, int(profile['id']), part_payload, user_id=user_id, system_managed=system_execution))
    return job_ids




def _safe_remote_path(value: str) -> str:
    path = str(value or '').strip().replace('\\', '/')
    while '//' in path:
        path = path.replace('//', '/')
    if path.endswith('/') and path != '/':
        path = path.rstrip('/')
    return path

def _path_inside_root(path: str, root: str) -> bool:
    path = _safe_remote_path(path)
    root = _safe_remote_path(root)
    return bool(path and root and (path == root or path.startswith(root.rstrip('/') + '/')))

def _automation_profile_transfer_payload(profile: dict[str, Any], eff: dict[str, Any], user_id: int, system_execution: bool = False) -> dict[str, Any]:
    """Validate automation profile-transfer settings with the same path semantics as the interactive transfer."""
    # Note: Interactive runs recheck user access; trusted scheduler runs use the previously authorized profile-bound rule.
    source_id = int(profile.get('id') or 0)
    if not system_execution and not auth.can_write_profile(source_id, user_id):
        raise PermissionError('Automation executor has no write access to source profile')
    target_id = int(eff.get('target_profile_id') or 0)
    if not target_id or target_id == source_id:
        raise ValueError('Automation target profile is invalid')
    if not system_execution and not auth.can_write_profile(target_id, user_id):
        raise PermissionError('Automation executor has no write access to target profile')
    target_profile = get_profile_for_system(target_id) if system_execution else get_profile(target_id, user_id)
    if not target_profile:
        raise ValueError('Automation target profile does not exist')

    requested_move_data = bool(eff.get('move_data'))
    requested_target_path = _safe_remote_path(str(eff.get('target_path') or eff.get('path') or ''))
    default_path = _safe_remote_path(rtorrent.default_download_path(target_profile)) if requested_move_data else ''
    target_path = (requested_target_path or default_path) if requested_move_data else ''
    roots = [default_path] if default_path else []
    if requested_move_data:
        try:
            prefs = get_profile_disk_monitor_preferences(target_id) if system_execution else get_disk_monitor_preferences(target_id, user_id=user_id)
            for item in json.loads((prefs or {}).get('disk_monitor_paths_json') or '[]'):
                clean = _safe_remote_path(str(item or ''))
                if clean and clean not in roots:
                    roots.append(clean)
            selected = _safe_remote_path(str((prefs or {}).get('disk_monitor_selected_path') or ''))
            if selected and selected not in roots:
                roots.append(selected)
        except Exception:
            pass
        target_roots = [r for r in roots if r]
        if not any(_path_inside_root(target_path, root) for root in target_roots):
            if requested_target_path:
                raise ValueError('Automation target path is outside the target profile download roots')
            target_path = default_path

    move_data = False
    downgrade_reason = ''
    if requested_move_data:
        check = rtorrent.remote_can_write_directory(profile, target_path)
        move_data = bool(check.get('ok'))
        if not move_data:
            downgrade_reason = str(check.get('message') or check.get('error') or 'target path is not writable by source rTorrent user')
    post_action = str(eff.get('post_action') or 'current').strip().lower()
    if post_action not in {'none', 'current', 'start', 'stop', 'pause', 'check', 'recheck'}:
        post_action = 'current'
    label_mode = str(eff.get('label_mode') or 'none').strip().lower()
    if label_mode not in {'none', 'custom', 'moved_from', 'moved_to'}:
        label_mode = 'none'
    return {
        'target_profile_id': target_id,
        'target_path': target_path,
        'path': target_path,
        'move_data': move_data,
        'move_data_requested': requested_move_data,
        'move_data_downgraded': bool(requested_move_data and not move_data),
        'move_data_downgrade_reason': downgrade_reason,
        'preserve_source_path': not move_data,
        'post_action': post_action,
        'label_mode': label_mode,
        'label_value': str(eff.get('label_value') or '').strip(),
    }

def _apply_effects_bulk(c: Any, profile: dict[str, Any], torrents: list[dict[str, Any]], effects: list[dict[str, Any]], rule: dict[str, Any], user_id: int | None = None, system_execution: bool = False, execution_id: str = '') -> list[dict[str, Any]]:
    """Translate matching rule effects into durable user or scheduler jobs."""
    # Note: Every effect from one check shares one execution id so cooldown is committed only after the whole rule execution succeeds.
    hashes = [str(t.get('hash') or '') for t in torrents if str(t.get('hash') or '')]
    torrents_by_hash = {str(t.get('hash') or ''): t for t in torrents if str(t.get('hash') or '')}
    labels_by_hash = {str(t.get('hash') or ''): _label_names(t.get('label')) for t in torrents}
    applied: list[dict[str, Any]] = []
    if not hashes: return applied
    for eff in effects:
        typ = str(eff.get('type') or '')
        if typ == 'move':
            path = str(eff.get('path') or '').strip() or rtorrent.default_download_path(profile)
            payload = {
                'path': path,
                'move_data': bool(eff.get('move_data')),
                'recheck': bool(eff.get('recheck', eff.get('move_data'))),
                'keep_seeding': bool(eff.get('keep_seeding')),
            }
            job_ids = _enqueue_automation_job(profile, rule, 'move', hashes, payload, torrents_by_hash, user_id, {'effect_type': 'move'}, system_execution, execution_id)
            applied.append({'type': 'move', 'path': path, 'count': len(hashes), 'target_hashes': hashes, 'move_data': payload['move_data'], 'recheck': payload['recheck'], 'keep_seeding': payload['keep_seeding'], 'job_ids': job_ids})
        elif typ == 'profile_transfer':
            executor_id = int(user_id or _resolve_user_id(profile))
            payload = _automation_profile_transfer_payload(profile, eff, executor_id, system_execution)
            job_ids = _enqueue_automation_job(profile, rule, 'profile_transfer', hashes, payload, torrents_by_hash, executor_id, {'effect_type': 'profile_transfer'}, system_execution, execution_id)
            applied.append({'type': 'profile_transfer', 'target_profile_id': payload['target_profile_id'], 'target_path': payload['target_path'], 'count': len(hashes), 'target_hashes': hashes, 'move_data': payload['move_data'], 'move_data_requested': payload['move_data_requested'], 'move_data_downgraded': payload['move_data_downgraded'], 'post_action': payload['post_action'], 'label_mode': payload['label_mode'], 'label': payload['label_value'], 'job_ids': job_ids})
        elif typ == 'add_label':
            label = str(eff.get('label') or '').strip()
            if label:
                grouped: dict[str, list[str]] = {}
                for h in hashes:
                    labels = labels_by_hash.get(h, [])
                    if label in labels:
                        continue
                    new_labels = list(labels) + [label]
                    value = _label_value(new_labels)
                    labels_by_hash[h] = _label_names(value)
                    grouped.setdefault(value, []).append(h)
                target_hashes = [h for group in grouped.values() for h in group]
                job_ids: list[str] = []
                for value, group_hashes in grouped.items():
                    job_ids.extend(_enqueue_automation_job(profile, rule, 'set_label', group_hashes, {'label': value}, torrents_by_hash, user_id, {'effect_type': 'add_label', 'label': label}, system_execution, execution_id))
                if target_hashes:
                    applied.append({'type': 'add_label', 'label': label, 'count': len(target_hashes), 'target_hashes': target_hashes, 'job_ids': job_ids})
        elif typ == 'remove_label':
            label = str(eff.get('label') or '').strip()
            if label:
                grouped: dict[str, list[str]] = {}
                for h in hashes:
                    labels = labels_by_hash.get(h, [])
                    if label not in labels:
                        continue
                    value = _label_value([x for x in labels if x != label])
                    labels_by_hash[h] = _label_names(value)
                    grouped.setdefault(value, []).append(h)
                target_hashes = [h for group in grouped.values() for h in group]
                job_ids: list[str] = []
                for value, group_hashes in grouped.items():
                    job_ids.extend(_enqueue_automation_job(profile, rule, 'set_label', group_hashes, {'label': value}, torrents_by_hash, user_id, {'effect_type': 'remove_label', 'label': label}, system_execution, execution_id))
                if target_hashes:
                    applied.append({'type': 'remove_label', 'label': label, 'count': len(target_hashes), 'target_hashes': target_hashes, 'job_ids': job_ids})
        elif typ == 'set_labels':
            value = _label_value(_label_names(eff.get('labels')))
            target_labels = _label_names(value)
            target_hashes = [h for h in hashes if labels_by_hash.get(h, []) != target_labels]
            for h in target_hashes:
                labels_by_hash[h] = list(target_labels)
            if target_hashes:
                job_ids = _enqueue_automation_job(profile, rule, 'set_label', target_hashes, {'label': value}, torrents_by_hash, user_id, {'effect_type': 'set_labels', 'labels': value}, system_execution, execution_id)
                applied.append({'type': 'set_labels', 'labels': value, 'count': len(target_hashes), 'target_hashes': target_hashes, 'job_ids': job_ids})
        elif typ in {'pause', 'stop', 'start', 'resume', 'recheck', 'reannounce'}:
            job_ids = _enqueue_automation_job(profile, rule, typ, hashes, {}, torrents_by_hash, user_id, {'effect_type': typ}, system_execution, execution_id)
            applied.append({'type': typ, 'count': len(hashes), 'target_hashes': hashes, 'job_ids': job_ids})
        elif typ == 'remove':
            payload = {'remove_data': bool(eff.get('remove_data'))}
            job_ids = _enqueue_automation_job(profile, rule, 'remove', hashes, payload, torrents_by_hash, user_id, {'effect_type': 'remove'}, system_execution, execution_id)
            applied.append({'type': 'remove', 'count': len(hashes), 'target_hashes': hashes, 'remove_data': payload['remove_data'], 'job_ids': job_ids})
    return applied


def _record_skipped_rule(profile_id: int, rule: dict[str, Any], hashes: list[str], reason: str, now: str, user_id: int) -> dict[str, Any]:
    action = {'type': 'skipped', 'error': reason, 'count': len(hashes)}
    torrent_hash = hashes[0] if len(hashes) == 1 else f'batch:{rule["id"]}:{now}:skipped'
    torrent_name = '1 torrent' if len(hashes) == 1 else f'{len(hashes)} torrents'
    with connect() as conn:
        conn.execute(
            'INSERT INTO automation_history(user_id,profile_id,rule_id,torrent_hash,torrent_name,rule_name,actions_json,actor_type,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
            (int(user_id), profile_id, rule['id'], torrent_hash, torrent_name, str(rule.get('name') or ''), json.dumps([action]), 'user', now),
        )
    return {'rule_id': rule['id'], 'rule_name': rule.get('name'), 'count': len(hashes), 'actions': [action], 'skipped': True}


def _pending_rule_hashes(profile_id: int) -> set[tuple[int, str]]:
    """Return automation rule/hash pairs already owned by pending or running jobs."""
    # Note: Queue state prevents duplicate scheduling while last_applied_at waits for real worker success.
    pending: set[tuple[int, str]] = set()
    with connect() as conn:
        rows = conn.execute("SELECT payload_json FROM jobs WHERE profile_id=? AND status IN ('pending','running')", (int(profile_id),)).fetchall()
    for row in rows:
        try:
            payload = json.loads(row.get('payload_json') or '{}')
            ctx = payload.get('job_context') or {}
            if str(ctx.get('source') or '') != 'automation':
                continue
            rid = int(ctx.get('rule_id') or 0)
            for h in payload.get('hashes') or []:
                if rid and str(h or ''):
                    pending.add((rid, str(h)))
        except Exception:
            continue
    return pending


def _automation_execution_jobs(profile_id: int, execution_id: str) -> list[dict[str, Any]]:
    """Load jobs belonging to one exact automation execution id."""
    # Note: Exact JSON verification avoids treating an unrelated payload containing the id text as part of the execution.
    if not execution_id:
        return []
    with connect() as conn:
        rows = conn.execute(
            'SELECT id,status,payload_json FROM jobs WHERE profile_id=? AND payload_json LIKE ?',
            (int(profile_id), f'%{execution_id}%'),
        ).fetchall()
    matched: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        try:
            job_payload = json.loads(row.get('payload_json') or '{}')
            ctx = job_payload.get('job_context') or {}
            if str(ctx.get('source') or '') != 'automation' or str(ctx.get('automation_execution_id') or '') != execution_id:
                continue
            row['payload'] = job_payload
            matched.append(row)
        except Exception:
            continue
    return matched


def _seal_automation_execution(profile_id: int, execution_id: str) -> None:
    """Mark one fully queued automation execution as eligible for terminal-state commit."""
    # Note: Jobs may finish very quickly; sealing persisted payloads and re-evaluating immediately closes the enqueue/worker race without granting cooldown to partial executions.
    jobs = _automation_execution_jobs(profile_id, execution_id)
    if not jobs:
        return
    with connect() as conn:
        for job in jobs:
            payload = dict(job.get('payload') or {})
            ctx = dict(payload.get('job_context') or {})
            ctx['automation_execution_scheduled'] = True
            payload['job_context'] = ctx
            conn.execute('UPDATE jobs SET payload_json=?, updated_at=? WHERE id=?', (json.dumps(payload), utcnow(), job['id']))
    refreshed = _automation_execution_jobs(profile_id, execution_id)
    if refreshed:
        first = refreshed[0]
        _commit_automation_terminal_state({'profile_id': int(profile_id)}, first.get('payload') or {}, True)


def _commit_automation_terminal_state(job: dict, payload: dict, succeeded: bool) -> None:
    """Finalize automation state when a legacy job or complete multi-effect execution reaches terminal state."""
    # Note: A rule-level cooldown is written only after every job generated by the same evaluation succeeds.
    ctx = (payload or {}).get('job_context') or {}
    if str(ctx.get('source') or '') != 'automation':
        return
    rule_id = int(ctx.get('rule_id') or 0)
    profile_id = int((job or {}).get('profile_id') or 0)
    hashes = {str(h) for h in (payload or {}).get('hashes') or [] if str(h or '')}
    execution_id = str(ctx.get('automation_execution_id') or '')
    if not rule_id or not profile_id:
        return

    if execution_id:
        execution_jobs = _automation_execution_jobs(profile_id, execution_id)
        if not execution_jobs:
            return
        # Note: A worker terminal callback cannot finalize an execution until the producer confirms that all sibling jobs were queued.
        if any((item.get('payload') or {}).get('job_context', {}).get('automation_execution_scheduled') is not True for item in execution_jobs):
            return
        statuses = {str(item.get('status') or '') for item in execution_jobs}
        if statuses & {'pending', 'running'}:
            return
        succeeded = bool(execution_jobs) and all(str(item.get('status') or '') == 'done' for item in execution_jobs)
        hashes = {
            str(h)
            for item in execution_jobs
            for h in ((item.get('payload') or {}).get('hashes') or [])
            if str(h or '')
        }

    if not hashes:
        return
    now = utcnow()
    with connect() as conn:
        rule = conn.execute('SELECT id,cooldown_minutes FROM automation_rules WHERE id=? AND profile_id=?', (rule_id, profile_id)).fetchone()
        if not rule:
            return
        if succeeded:
            for h in hashes:
                conn.execute(
                    'INSERT INTO automation_rule_state(rule_id,profile_id,torrent_hash,last_matched_at,last_applied_at,updated_at) VALUES(?,?,?,?,?,?) '
                    'ON CONFLICT(rule_id,profile_id,torrent_hash) DO UPDATE SET last_matched_at=excluded.last_matched_at,last_applied_at=excluded.last_applied_at,updated_at=excluded.updated_at',
                    (rule_id, profile_id, h, now, now, now),
                )
            _mark_rule_cooldown(conn, rule, profile_id, now)
            return
        conn.execute("DELETE FROM automation_rule_state WHERE rule_id=? AND profile_id=? AND torrent_hash='__rule__'", (rule_id, profile_id))
        for h in hashes:
            conn.execute(
                'UPDATE automation_rule_state SET last_applied_at=NULL, updated_at=? WHERE rule_id=? AND profile_id=? AND torrent_hash=?',
                (now, rule_id, profile_id, h),
            )


def record_job_success(job: dict, payload: dict) -> None:
    """Advance automation cooldown/state only after a queued mutation succeeds."""
    # Note: Multi-effect executions remain pending until their final sibling job reaches a terminal state.
    _commit_automation_terminal_state(job, payload, True)


def record_job_failure(job: dict, payload: dict) -> None:
    """Keep failed automation effects immediately eligible for a later retry."""
    # Note: Any failed or cancelled sibling keeps the full rule execution eligible for a later retry.
    _commit_automation_terminal_state(job, payload, False)


def check(profile: dict | None = None, user_id: int | None = None, force: bool = False, rule_id: int | None = None, system_execution: bool = False) -> dict[str, Any]:
    """Evaluate profile-bound rules as an authorized user or trusted scheduler."""
    # Note: User-triggered runs require current RW access; background runs rely on persisted rule authority, not a creator account.
    profile = profile or active_profile(user_id=user_id)
    if not profile:
        return {'ok': False, 'error': 'No active rTorrent profile'}
    profile_id = int(profile['id'])
    executor_id = _resolve_user_id(profile, user_id)
    if not system_execution:
        _require_profile_write(profile_id, executor_id)
    lock = _check_lock(profile_id, rule_id)
    if not lock.acquire(blocking=False):
        return {'ok': True, 'checked': 0, 'applied': [], 'batches': [], 'rules': 0, 'skipped': True, 'reason': 'Automation check already running'}
    try:
        rules = _list_enabled_rules_for_profile(profile_id, rule_id=rule_id, force=force)
        if not rules:
            return {'ok': True, 'checked': 0, 'applied': [], 'batches': [], 'rules': 0}
        torrents = rtorrent.list_torrents(profile)
        applied = []
        batches = []
        now = utcnow()
        planned: list[dict[str, Any]] = []
        pending_hashes = _pending_rule_hashes(profile_id)
        with connect() as conn:
            for rule in rules:
                if not force and not _cooldown_ok(conn, rule, profile_id):
                    continue
                matched = [t for t in torrents if _conditions_match(conn, rule, profile_id, t) and (int(rule['id']), str(t.get('hash') or '')) not in pending_hashes]
                if not matched:
                    continue
                hashes = [str(t.get('hash') or '') for t in matched if str(t.get('hash') or '')]
                if hashes:
                    planned.append({'rule': rule, 'matched': matched, 'hashes': hashes})
        for item in planned:
            rule = item['rule']
            matched = item['matched']
            hashes = item['hashes']
            try:
                # Note: One id binds all jobs from this rule evaluation into a single success/failure boundary.
                execution_id = uuid.uuid4().hex
                actions = _apply_effects_bulk(None, profile, matched, rule.get('effects') or [], rule, executor_id, system_execution, execution_id)
                _seal_automation_execution(profile_id, execution_id)
            except Exception as exc:
                actions = [{'error': str(exc), 'count': len(hashes), 'target_hashes': hashes}]
            changed_hashes = sorted({h for a in actions for h in (a.get('target_hashes') or [])})
            if not actions or not changed_hashes:
                continue
            history_actions = [{k: v for k, v in a.items() if k != 'target_hashes'} for a in actions]
            matched_by_hash = {str(t.get('hash') or ''): t for t in matched}
            creator_id = int(rule.get('created_by_user_id') or 0)
            creator_label = str(rule.get('created_by_label') or '')
            with connect() as conn:
                for h in changed_hashes:
                    t = matched_by_hash.get(h, {})
                    conn.execute('INSERT INTO automation_rule_state(rule_id,profile_id,torrent_hash,last_matched_at,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(rule_id,profile_id,torrent_hash) DO UPDATE SET last_matched_at=excluded.last_matched_at, updated_at=excluded.updated_at', (rule['id'], profile_id, h, now, now))
                    applied.append({'rule_id': rule['id'], 'rule_name': rule.get('name'), 'created_by_user_id': creator_id, 'created_by_label': creator_label, 'executed_by_user_id': None if system_execution else executor_id, 'executed_by': 'system' if system_execution else 'user', 'hash': h, 'name': t.get('name'), 'actions': [{'type': a.get('type', 'error'), 'count': a.get('count', len(changed_hashes))} for a in actions]})
                torrent_name = str(matched_by_hash.get(changed_hashes[0], {}).get('name') or '') if len(changed_hashes) == 1 else f'{len(changed_hashes)} torrents'
                torrent_hash = changed_hashes[0] if len(changed_hashes) == 1 else f'batch:{rule["id"]}:{now}'
                conn.execute('INSERT INTO automation_history(user_id,profile_id,rule_id,torrent_hash,torrent_name,rule_name,actions_json,actor_type,created_at) VALUES(?,?,?,?,?,?,?,?,?)', (executor_id, profile_id, rule['id'], torrent_hash, torrent_name, str(rule.get('name') or ''), json.dumps(history_actions), 'system' if system_execution else 'user', now))
            batches.append({'rule_id': rule['id'], 'rule_name': rule.get('name'), 'created_by_user_id': creator_id, 'created_by_label': creator_label, 'executed_by_user_id': None if system_execution else executor_id, 'executed_by': 'system' if system_execution else 'user', 'count': len(changed_hashes), 'actions': history_actions})
        return {'ok': True, 'checked': len(torrents), 'rules': len(rules), 'applied': applied, 'batches': batches}
    finally:
        lock.release()
