from __future__ import annotations
from ._shared import *


@bp.get('/smart-queue')
def smart_queue_get():
    from ..services import smart_queue
    profile = request_profile()
    if not profile:
        return ok({'settings': {}, 'exclusions': [], 'error': 'No profile'})
    try:
        history_limit = max(1, min(int(request.args.get('history_limit', 10) or 10), 100))
        settings = smart_queue.get_settings(profile['id'])
        exclusions = smart_queue.list_exclusions(profile['id'])
        history = smart_queue.list_history(profile['id'], limit=history_limit)
        history_total = smart_queue.count_history(profile['id'])
        return ok({'settings': settings, 'exclusions': exclusions, 'history': history, 'history_total': history_total, 'cooldown_remaining_seconds': smart_queue.cooldown_remaining(settings), 'refill_remaining_seconds': smart_queue.refill_remaining(settings), 'surge_refill_remaining_seconds': smart_queue.surge_refill_remaining(settings)})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc), 'settings': {}, 'exclusions': []})


@bp.post('/smart-queue')
def smart_queue_save():
    from ..services import smart_queue
    profile = request_profile()
    if not profile:
        return ok({'settings': {}, 'error': 'No profile'})
    try:
        payload = request.get_json(silent=True) or {}
        settings = smart_queue.save_settings(profile['id'], payload)
        return ok({'settings': settings, 'cooldown_remaining_seconds': smart_queue.cooldown_remaining(settings), 'refill_remaining_seconds': smart_queue.refill_remaining(settings), 'surge_refill_remaining_seconds': smart_queue.surge_refill_remaining(settings)})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)})



@bp.post('/smart-queue/check')
def smart_queue_check():
    profile = request_profile()
    if not profile:
        return ok({'result': {'ok': False, 'error': 'No profile'}})
    if str(request.args.get('sync') or '').lower() in {'1', 'true', 'yes'}:
        from ..services import smart_queue
        try:
            result = smart_queue.check(profile, force=True)
            diff = torrent_cache.refresh(profile)
            rows = torrent_cache.snapshot(profile['id'])
            return ok({'result': result, 'torrent_patch': {**diff, 'summary': cached_summary(profile['id'], rows, force=True)}})
        except Exception as exc:
            return jsonify({'ok': False, 'error': str(exc)}), 500
    try:
        job_id = enqueue(
            'smart_queue_check',
            int(profile['id']),
            {'job_context': {'source': 'user', 'bulk_label': 'Smart Queue manual check'}},
            force=True,
            max_attempts=1,
        )
        return ok({'queued': True, 'job_id': job_id, 'result': {'ok': True, 'queued': True, 'job_id': job_id}})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500



@bp.post('/smart-queue/exclusion')
def smart_queue_exclusion():
    from ..services import smart_queue
    profile = request_profile()
    if not profile:
        return jsonify({'ok': False, 'error': 'No profile'}), 400
    data = request.get_json(silent=True) or {}
    torrent_hash = str(data.get('hash') or '').strip()
    if not torrent_hash:
        return jsonify({'ok': False, 'error': 'Missing torrent hash'}), 400
    smart_queue.set_exclusion(profile['id'], torrent_hash, bool(data.get('excluded', True)), str(data.get('reason') or 'manual'))
    return ok({'exclusions': smart_queue.list_exclusions(profile['id'])})

@bp.delete('/smart-queue/history')
def smart_queue_history_clear():
    from ..services import smart_queue
    profile = request_profile()
    if not profile:
        return jsonify({'ok': False, 'error': 'No profile'}), 400
    try:
        removed = smart_queue.clear_history(profile['id'])
        return ok({'removed': removed, 'history': [], 'history_total': 0})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500

