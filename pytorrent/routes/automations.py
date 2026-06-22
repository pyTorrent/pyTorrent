from __future__ import annotations
from ._shared import *


def _automation_user_id() -> int:
    return int(default_user_id() or 0)


@bp.get('/automations')
def automations_get():
    from ..services import automation_rules
    profile = request_profile()
    if not profile:
        return ok({'rules': [], 'history': [], 'error': 'No profile'})
    try:
        user_id = _automation_user_id()
        return ok({
            'rules': automation_rules.list_rules(profile['id'], user_id=user_id),
            'history': automation_rules.list_history(profile['id'], user_id=user_id),
        })
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc), 'rules': [], 'history': []}), 500


@bp.get('/automations/export')
def automations_export():
    from ..services import automation_rules
    profile = request_profile()
    if not profile:
        return jsonify({'ok': False, 'error': 'No profile'}), 400
    try:
        data = automation_rules.export_rules(profile['id'], user_id=_automation_user_id())
        return ok({'export': data, 'count': len(data.get('rules') or [])})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400


@bp.post('/automations/import')
def automations_import():
    from ..services import automation_rules
    profile = request_profile()
    if not profile:
        return jsonify({'ok': False, 'error': 'No profile'}), 400
    try:
        payload = request.get_json(silent=True) or {}
        replace = str(request.args.get('replace') or '').lower() in {'1', 'true', 'yes'} or bool(payload.get('replace')) if isinstance(payload, dict) else False
        user_id = _automation_user_id()
        imported = automation_rules.import_rules(profile['id'], payload, user_id=user_id, replace=replace)
        return ok({'imported': len(imported), 'rules': automation_rules.list_rules(profile['id'], user_id=user_id)})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400


@bp.post('/automations')
def automations_save():
    from ..services import automation_rules
    profile = request_profile()
    if not profile:
        return jsonify({'ok': False, 'error': 'No profile'}), 400
    try:
        user_id = _automation_user_id()
        rule = automation_rules.save_rule(profile['id'], request.get_json(silent=True) or {}, user_id=user_id)
        return ok({'rule': rule, 'rules': automation_rules.list_rules(profile['id'], user_id=user_id)})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400


@bp.delete('/automations/<int:rule_id>')
def automations_delete(rule_id: int):
    from ..services import automation_rules
    profile = request_profile()
    if not profile:
        return jsonify({'ok': False, 'error': 'No profile'}), 400
    try:
        user_id = _automation_user_id()
        automation_rules.delete_rule(rule_id, profile['id'], user_id=user_id)
        return ok({'rules': automation_rules.list_rules(profile['id'], user_id=user_id)})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400


@bp.post('/automations/<int:rule_id>/run')
def automations_run_rule(rule_id: int):
    from ..services import automation_rules
    profile = request_profile()
    if not profile:
        return jsonify({'ok': False, 'error': 'No profile'}), 400
    try:
        user_id = _automation_user_id()
        return ok({
            'result': automation_rules.check(profile, user_id=user_id, force=True, rule_id=rule_id),
            'rules': automation_rules.list_rules(profile['id'], user_id=user_id),
            'history': automation_rules.list_history(profile['id'], user_id=user_id),
        })
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500


@bp.post('/automations/check')
def automations_check():
    from ..services import automation_rules
    profile = request_profile()
    if not profile:
        return jsonify({'ok': False, 'error': 'No profile'}), 400
    try:
        user_id = _automation_user_id()
        return ok({
            'result': automation_rules.check(profile, user_id=user_id, force=True),
            'rules': automation_rules.list_rules(profile['id'], user_id=user_id),
            'history': automation_rules.list_history(profile['id'], user_id=user_id),
        })
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500


@bp.delete('/automations/history')
def automations_history_clear():
    from ..services import automation_rules
    profile = request_profile()
    if not profile:
        return jsonify({'ok': False, 'error': 'No profile'}), 400
    try:
        user_id = _automation_user_id()
        deleted = automation_rules.clear_history(profile['id'], user_id=user_id)
        return ok({'deleted': deleted, 'history': automation_rules.list_history(profile['id'], user_id=user_id), 'cleanup': cleanup_summary()})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500
