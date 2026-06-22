from __future__ import annotations
from flask import abort, jsonify, request
from ..services.auth import current_user, list_users, save_user, delete_user, login_user, logout_user, enabled as auth_enabled, provider as auth_provider, uses_external_provider, external_auth_summary, list_api_tokens, create_api_token, revoke_api_token


def _ok(payload=None):
    data = {"ok": True}
    if payload:
        data.update(payload)
    return jsonify(data)


def register_auth_routes(bp):
    @bp.post("/auth/login")
    def auth_login():
        if not auth_enabled():
            abort(404)
        data = request.get_json(silent=True) or {}
        user = login_user(str(data.get("username") or ""), str(data.get("password") or ""))
        if not user:
            return jsonify({"ok": False, "error": "Invalid username or password"}), 401
        return _ok({"user": user, "auth_enabled": auth_enabled(), "auth_provider": auth_provider()})

    @bp.get("/auth/me")
    def auth_me():
        if not auth_enabled():
            abort(404)
        return _ok({"user": current_user(), "auth_enabled": auth_enabled(), "auth_provider": auth_provider()})

    @bp.post("/auth/logout")
    def auth_logout():
        if not auth_enabled():
            abort(404)
        if uses_external_provider():
            return _ok({"logout_managed_by_provider": True, "auth_provider": auth_provider()})
        logout_user()
        return _ok()

    @bp.get("/auth/users")
    def auth_users_list():
        if not auth_enabled():
            abort(404)
        return _ok({"users": list_users(), "auth": external_auth_summary()})

    @bp.post("/auth/users")
    def auth_users_create():
        if not auth_enabled():
            abort(404)
        try:
            return _ok({"user": save_user(request.get_json(silent=True) or {})})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @bp.put("/auth/users/<int:user_id>")
    def auth_users_update(user_id: int):
        if not auth_enabled():
            abort(404)
        try:
            return _ok({"user": save_user(request.get_json(silent=True) or {}, user_id)})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @bp.delete("/auth/users/<int:user_id>")
    def auth_users_delete(user_id: int):
        if not auth_enabled():
            abort(404)
        try:
            delete_user(user_id)
            return _ok()
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
    @bp.get("/auth/users/<int:user_id>/tokens")
    def auth_user_tokens_list(user_id: int):
        if not auth_enabled():
            abort(404)
        return _ok({"tokens": list_api_tokens(user_id)})

    @bp.post("/auth/users/<int:user_id>/tokens")
    def auth_user_tokens_create(user_id: int):
        if not auth_enabled():
            abort(404)
        try:
            data = request.get_json(silent=True) or {}
            return _ok({"token": create_api_token(user_id, str(data.get("name") or "API token"))})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @bp.delete("/auth/users/<int:user_id>/tokens/<int:token_id>")
    def auth_user_tokens_delete(user_id: int, token_id: int):
        if not auth_enabled():
            abort(404)
        try:
            revoke_api_token(user_id, token_id)
            return _ok({"tokens": list_api_tokens(user_id)})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

