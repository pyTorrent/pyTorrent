from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
import json
import threading
from ..db import connect, default_user_id, utcnow
from . import rtorrent, auth
from .preferences import active_profile, get_profile, get_disk_monitor_preferences
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
    """Return a safe user id for rule ownership or background execution."""
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
    item['owner_user_id'] = int(item.get('user_id') or 0)
    item['owner_username'] = str(item.get('owner_username') or '').strip()
    item['owner_display_name'] = str(item.get('owner_display_name') or '').strip()
    item['owner_label'] = item['owner_display_name'] or item['owner_username'] or f"user #{item['owner_user_id']}"
    return item


def _require_profile_read(profile_id: int, user_id: int | None = None) -> int:
    viewer_id = _resolve_user_id(user_id=user_id)
    if not auth.can_access_profile(profile_id, viewer_id):
        raise ValueError('No access to profile')
    return viewer_id


def _require_profile_write(profile_id: int, user_id: int | None = None) -> int:
    viewer_id = _resolve_user_id(user_id=user_id)
    if not auth.can_write_profile(profile_id, viewer_id):
        raise ValueError('No write access to profile')
    return viewer_id


def _can_manage_rule(profile_id: int, rule: dict[str, Any], user_id: int) -> bool:
    return int(rule.get('user_id') or 0) == int(user_id) or auth.can_write_profile(profile_id, user_id)


def _select_rules_sql(where_sql: str) -> str:
    return f'''
        SELECT
            r.*,
            u.username AS owner_username,
            COALESCE(u.display_name, '') AS owner_display_name
        FROM automation_rules r
        LEFT JOIN users u ON u.id = r.user_id
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
    return {
        'name': str(rule.get('name') or 'Automation rule'),
        'enabled': bool(rule.get('enabled', True)),
        'cooldown_minutes': max(0, int(rule.get('cooldown_minutes') or 0)),
        'conditions': list(rule.get('conditions') or []),
        'effects': list(rule.get('effects') or []),
    }


def export_rules(profile_id: int, user_id: int | None = None) -> dict[str, Any]:
    rules = [_portable_rule(rule) for rule in list_rules(profile_id, user_id)]
    return {'version': 1, 'app': 'pyTorrent', 'exported_at': utcnow(), 'scope': 'profile', 'rules': rules}


def import_rules(profile_id: int, payload: dict[str, Any] | list[Any], user_id: int | None = None, replace: bool = False) -> list[dict[str, Any]]:
    owner_id = _require_profile_write(profile_id, user_id)
    raw_rules = payload if isinstance(payload, list) else payload.get('rules', []) if isinstance(payload, dict) else []
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ValueError('Import file does not contain automation rules')
    if replace:
        with connect() as conn:
            conn.execute('DELETE FROM automation_rules WHERE profile_id=?', (profile_id,))
            conn.execute('DELETE FROM automation_rule_state WHERE profile_id=?', (profile_id,))
    imported = []
    for raw in raw_rules:
        if not isinstance(raw, dict):
            continue
        rule = _portable_rule(raw)
        imported.append(save_rule(profile_id, rule, owner_id))
    if not imported:
        raise ValueError('No valid automation rules found')
    return imported


def save_rule(profile_id: int, data: dict[str, Any], user_id: int | None = None) -> dict[str, Any]:
    actor_id = _resolve_user_id(user_id=user_id)
    name = str(data.get('name') or 'Automation rule').strip() or 'Automation rule'
    conditions = data.get('conditions') or []
    effects = data.get('effects') or []
    if not isinstance(conditions, list) or not conditions:
        raise ValueError('Rule needs at least one condition')
    if not isinstance(effects, list) or not effects:
        raise ValueError('Rule needs at least one effect')
    cooldown = max(0, int(data.get('cooldown_minutes') or 0))
    enabled = 1 if data.get('enabled', True) else 0
    now = utcnow()
    rule_id = int(data.get('id') or 0)
    if rule_id:
        existing = get_rule(rule_id, profile_id, actor_id)
        if not _can_manage_rule(profile_id, existing, actor_id):
            raise ValueError('No permission to edit this automation rule')
        owner_id = int(existing.get('user_id') or existing.get('owner_user_id') or actor_id)
        with connect() as conn:
            cur = conn.execute(
                'UPDATE automation_rules SET name=?, enabled=?, conditions_json=?, effects_json=?, cooldown_minutes=?, updated_at=? WHERE id=? AND profile_id=?',
                (name, enabled, json.dumps(conditions), json.dumps(effects), cooldown, now, rule_id, profile_id),
            )
            if not cur.rowcount:
                raise ValueError('Rule not found')
    else:
        owner_id = _require_profile_write(profile_id, actor_id)
        with connect() as conn:
            cur = conn.execute(
                'INSERT INTO automation_rules(user_id,profile_id,name,enabled,conditions_json,effects_json,cooldown_minutes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                (owner_id, profile_id, name, enabled, json.dumps(conditions), json.dumps(effects), cooldown, now, now),
            )
            rule_id = int(cur.lastrowid)
    return get_rule(rule_id, profile_id, actor_id)


def delete_rule(rule_id: int, profile_id: int, user_id: int | None = None) -> None:
    actor_id = _resolve_user_id(user_id=user_id)
    rule = get_rule(rule_id, profile_id, actor_id)
    if not _can_manage_rule(profile_id, rule, actor_id):
        raise ValueError('No permission to delete this automation rule')
    with connect() as conn:
        conn.execute('DELETE FROM automation_rules WHERE id=? AND profile_id=?', (rule_id, profile_id))
        conn.execute('DELETE FROM automation_rule_state WHERE rule_id=? AND profile_id=?', (rule_id, profile_id))


def list_history(profile_id: int, user_id: int | None = None, limit: int = 30) -> list[dict[str, Any]]:
    _require_profile_read(profile_id, user_id)
    with connect() as conn:
        return conn.execute('''
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
        'rule_owner_user_id': int(rule.get('user_id') or rule.get('owner_user_id') or 0),
        'rule_owner': str(rule.get('owner_label') or ''),
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


def _enqueue_automation_job(profile: dict[str, Any], rule: dict[str, Any], action_name: str, hashes: list[str], payload: dict[str, Any], torrents_by_hash: dict[str, dict[str, Any]], user_id: int | None = None, context_extra: dict[str, Any] | None = None) -> list[str]:
    job_ids: list[str] = []
    chunks = [hashes] if action_name in AUTOMATION_LIGHT_ACTIONS else _chunk_hashes(hashes)
    for index, chunk in enumerate(chunks, start=1):
        part_payload = dict(payload or {})
        part_payload['hashes'] = chunk
        part_payload['source'] = 'automation'
        if action_name not in AUTOMATION_LIGHT_ACTIONS:
            part_payload['requires_order'] = True
        extra = dict(context_extra or {})
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
        job_ids.append(enqueue(action_name, int(profile['id']), part_payload, user_id=user_id))
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

def _automation_profile_transfer_payload(profile: dict[str, Any], eff: dict[str, Any], user_id: int) -> dict[str, Any]:
    # Note: Automation profile transfers reuse server-side permission checks; UI values are not trusted.
    source_id = int(profile.get('id') or 0)
    if not auth.can_write_profile(source_id, user_id):
        raise ValueError('Rule owner has no write access to source profile')
    target_id = int(eff.get('target_profile_id') or 0)
    if not target_id or target_id == source_id:
        raise ValueError('Automation target profile is invalid')
    if not auth.can_write_profile(target_id, user_id):
        raise ValueError('Rule owner has no write access to target profile')
    target_profile = get_profile(target_id, user_id)
    if not target_profile:
        raise ValueError('Automation target profile does not exist')
    default_path = _safe_remote_path(rtorrent.default_download_path(target_profile))
    requested_target_path = _safe_remote_path(str(eff.get('target_path') or eff.get('path') or ''))
    target_path = requested_target_path or default_path
    roots = [default_path]
    try:
        prefs = get_disk_monitor_preferences(target_id, user_id=user_id)
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
    requested_move_data = bool(eff.get('move_data'))
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
        'post_action': post_action,
        'label_mode': label_mode,
        'label_value': str(eff.get('label_value') or '').strip(),
    }

def _apply_effects_bulk(c: Any, profile: dict[str, Any], torrents: list[dict[str, Any]], effects: list[dict[str, Any]], rule: dict[str, Any], user_id: int | None = None) -> list[dict[str, Any]]:
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
            job_ids = _enqueue_automation_job(profile, rule, 'move', hashes, payload, torrents_by_hash, user_id, {'effect_type': 'move'})
            applied.append({'type': 'move', 'path': path, 'count': len(hashes), 'target_hashes': hashes, 'move_data': payload['move_data'], 'recheck': payload['recheck'], 'keep_seeding': payload['keep_seeding'], 'job_ids': job_ids})
        elif typ == 'profile_transfer':
            owner_id = int(user_id or rule.get('user_id') or rule.get('owner_user_id') or default_user_id())
            payload = _automation_profile_transfer_payload(profile, eff, owner_id)
            job_ids = _enqueue_automation_job(profile, rule, 'profile_transfer', hashes, payload, torrents_by_hash, owner_id, {'effect_type': 'profile_transfer'})
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
                    job_ids.extend(_enqueue_automation_job(profile, rule, 'set_label', group_hashes, {'label': value}, torrents_by_hash, user_id, {'effect_type': 'add_label', 'label': label}))
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
                    job_ids.extend(_enqueue_automation_job(profile, rule, 'set_label', group_hashes, {'label': value}, torrents_by_hash, user_id, {'effect_type': 'remove_label', 'label': label}))
                if target_hashes:
                    applied.append({'type': 'remove_label', 'label': label, 'count': len(target_hashes), 'target_hashes': target_hashes, 'job_ids': job_ids})
        elif typ == 'set_labels':
            value = _label_value(_label_names(eff.get('labels')))
            target_labels = _label_names(value)
            target_hashes = [h for h in hashes if labels_by_hash.get(h, []) != target_labels]
            for h in target_hashes:
                labels_by_hash[h] = list(target_labels)
            if target_hashes:
                job_ids = _enqueue_automation_job(profile, rule, 'set_label', target_hashes, {'label': value}, torrents_by_hash, user_id, {'effect_type': 'set_labels', 'labels': value})
                applied.append({'type': 'set_labels', 'labels': value, 'count': len(target_hashes), 'target_hashes': target_hashes, 'job_ids': job_ids})
        elif typ in {'pause', 'stop', 'start', 'resume', 'recheck', 'reannounce'}:
            job_ids = _enqueue_automation_job(profile, rule, typ, hashes, {}, torrents_by_hash, user_id, {'effect_type': typ})
            applied.append({'type': typ, 'count': len(hashes), 'target_hashes': hashes, 'job_ids': job_ids})
        elif typ == 'remove':
            payload = {'remove_data': bool(eff.get('remove_data'))}
            job_ids = _enqueue_automation_job(profile, rule, 'remove', hashes, payload, torrents_by_hash, user_id, {'effect_type': 'remove'})
            applied.append({'type': 'remove', 'count': len(hashes), 'target_hashes': hashes, 'remove_data': payload['remove_data'], 'job_ids': job_ids})
    return applied


def _record_skipped_rule(profile_id: int, rule: dict[str, Any], hashes: list[str], reason: str, now: str) -> dict[str, Any]:
    action = {'type': 'skipped', 'error': reason, 'count': len(hashes)}
    owner_id = int(rule.get('user_id') or rule.get('owner_user_id') or default_user_id())
    torrent_hash = hashes[0] if len(hashes) == 1 else f'batch:{rule["id"]}:{now}:skipped'
    torrent_name = '1 torrent' if len(hashes) == 1 else f'{len(hashes)} torrents'
    with connect() as conn:
        conn.execute(
            'INSERT INTO automation_history(user_id,profile_id,rule_id,torrent_hash,torrent_name,rule_name,actions_json,created_at) VALUES(?,?,?,?,?,?,?,?)',
            (owner_id, profile_id, rule['id'], torrent_hash, torrent_name, str(rule.get('name') or ''), json.dumps([action]), now),
        )
    return {'rule_id': rule['id'], 'rule_name': rule.get('name'), 'count': len(hashes), 'actions': [action], 'skipped': True}


def check(profile: dict | None = None, user_id: int | None = None, force: bool = False, rule_id: int | None = None) -> dict[str, Any]:
    profile = profile or active_profile(user_id=user_id)
    if not profile:
        return {'ok': False, 'error': 'No active rTorrent profile'}
    profile_id = int(profile['id'])
    if rule_id is not None:
        _require_profile_read(profile_id, user_id)
    lock = _check_lock(profile_id, rule_id)
    if not lock.acquire(blocking=False):
        # Note: Browser, manual and background checks can now coexist without duplicate rule application.
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
        with connect() as conn:
            for rule in rules:
                if not force and not _cooldown_ok(conn, rule, profile_id):
                    continue
                matched = [t for t in torrents if _conditions_match(conn, rule, profile_id, t)]
                if not matched:
                    continue
                hashes = [str(t.get('hash') or '') for t in matched if str(t.get('hash') or '')]
                if hashes:
                    planned.append({'rule': rule, 'matched': matched, 'hashes': hashes})
        for item in planned:
            rule = item['rule']
            matched = item['matched']
            hashes = item['hashes']
            owner_id = int(rule.get('user_id') or rule.get('owner_user_id') or default_user_id())
            if not auth.can_write_profile(profile_id, owner_id):
                batch = _record_skipped_rule(profile_id, rule, hashes, 'Rule owner no longer has write access to profile', now)
                batches.append(batch)
                continue
            try:
                actions = _apply_effects_bulk(None, profile, matched, rule.get('effects') or [], rule, owner_id)
            except Exception as exc:
                actions = [{'error': str(exc), 'count': len(hashes), 'target_hashes': hashes}]
            changed_hashes = sorted({h for a in actions for h in (a.get('target_hashes') or [])})
            if not actions or not changed_hashes:
                continue
            history_actions = [{k: v for k, v in a.items() if k != 'target_hashes'} for a in actions]
            matched_by_hash = {str(t.get('hash') or ''): t for t in matched}
            with connect() as conn:
                for h in changed_hashes:
                    t = matched_by_hash.get(h, {})
                    conn.execute('INSERT INTO automation_rule_state(rule_id,profile_id,torrent_hash,last_matched_at,last_applied_at,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(rule_id,profile_id,torrent_hash) DO UPDATE SET last_matched_at=excluded.last_matched_at, last_applied_at=excluded.last_applied_at, updated_at=excluded.updated_at', (rule['id'], profile_id, h, now, now, now))
                    applied.append({'rule_id': rule['id'], 'rule_name': rule.get('name'), 'owner_user_id': owner_id, 'owner_label': rule.get('owner_label'), 'hash': h, 'name': t.get('name'), 'actions': [{'type': a.get('type', 'error'), 'count': a.get('count', len(changed_hashes))} for a in actions]})
                _mark_rule_cooldown(conn, rule, profile_id, now)
                torrent_name = str(matched_by_hash.get(changed_hashes[0], {}).get('name') or '') if len(changed_hashes) == 1 else f'{len(changed_hashes)} torrents'
                torrent_hash = changed_hashes[0] if len(changed_hashes) == 1 else f'batch:{rule["id"]}:{now}'
                conn.execute('INSERT INTO automation_history(user_id,profile_id,rule_id,torrent_hash,torrent_name,rule_name,actions_json,created_at) VALUES(?,?,?,?,?,?,?,?)', (owner_id, profile_id, rule['id'], torrent_hash, torrent_name, str(rule.get('name') or ''), json.dumps(history_actions), now))
            batches.append({'rule_id': rule['id'], 'rule_name': rule.get('name'), 'owner_user_id': owner_id, 'owner_label': rule.get('owner_label'), 'count': len(changed_hashes), 'actions': history_actions})
        return {'ok': True, 'checked': len(torrents), 'rules': len(rules), 'applied': applied, 'batches': batches}
    finally:
        lock.release()
