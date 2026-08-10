from __future__ import annotations

import ast
import importlib.util
import sqlite3
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = (ROOT / "schema.sql").read_text(encoding="utf-8")
NOW = "2026-08-10T22:00:00+00:00"


def _dict_factory(cursor, row):
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = _dict_factory
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO users(id,username,role,is_active,created_at,updated_at) VALUES(1,'default','admin',1,?,?)",
        (NOW, NOW),
    )
    conn.execute("INSERT INTO user_preferences(user_id,created_at,updated_at) VALUES(1,?,?)", (NOW, NOW))
    conn.commit()
    yield conn
    conn.close()


def _connect_for(conn):
    @contextmanager
    def _connect():
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return _connect


def _package(name: str, path: Path):
    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    sys.modules[name] = module
    return module


def _module(name: str, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _base_service_root(tag: str):
    root_name = f"pttest_{tag}"
    root = _package(root_name, ROOT / "pytorrent")
    services = _package(f"{root_name}.services", ROOT / "pytorrent" / "services")
    return root_name, root, services


def _db_stub(root_name: str):
    return _module(
        f"{root_name}.db",
        connect=lambda: (_ for _ in ()).throw(RuntimeError("connect must be patched")),
        utcnow=lambda: NOW,
        default_user_id=lambda: 1,
    )


def _load_preferences():
    root_name, _root, services = _base_service_root("prefs")
    _db_stub(root_name)
    auth = _module(
        f"{root_name}.services.auth",
        current_user_id=lambda: 1,
        can_write_profile=lambda *_a, **_k: True,
        require_profile_write=lambda *_a, **_k: None,
        visible_profile_ids=lambda *_a, **_k: None,
        can_access_profile=lambda *_a, **_k: True,
    )
    setattr(services, "auth", auth)
    _module(f"{root_name}.services.frontend_assets", BOOTSTRAP_THEME_LABELS={"dark": "Dark"})
    _module(f"{root_name}.services.deletion", purge_profile=lambda *_a, **_k: None)
    return _load(f"{root_name}.services.preferences", ROOT / "pytorrent" / "services" / "preferences.py")


def _load_automation_rules():
    root_name, _root, services = _base_service_root("automation")
    _db_stub(root_name)
    auth = _module(f"{root_name}.services.auth", current_user_id=lambda: 1)
    rtorrent = _module(f"{root_name}.services.rtorrent")
    setattr(services, "auth", auth)
    setattr(services, "rtorrent", rtorrent)
    _module(
        f"{root_name}.services.preferences",
        active_profile=lambda *_a, **_k: None,
        get_profile=lambda *_a, **_k: None,
        get_disk_monitor_preferences=lambda *_a, **_k: {},
    )
    _module(f"{root_name}.services.workers", enqueue=lambda *_a, **_k: "job")
    return _load(f"{root_name}.services.automation_rules", ROOT / "pytorrent" / "services" / "automation_rules.py")


def _load_workers():
    root_name, _root, services = _base_service_root("workers")
    _db_stub(root_name)
    _module(f"{root_name}.config", WORKERS=2)
    auth = _module(f"{root_name}.services.auth", current_user_id=lambda: 1)
    rtorrent = _module(f"{root_name}.services.rtorrent")
    disk_guard = _module(f"{root_name}.services.disk_guard", assert_can_start_download=lambda *_a, **_k: None)
    operation_logs = _module(
        f"{root_name}.services.operation_logs",
        record_job_event=lambda *_a, **_k: None,
        record_worker_event=lambda *_a, **_k: None,
    )
    for name, mod in (("auth", auth), ("rtorrent", rtorrent), ("disk_guard", disk_guard), ("operation_logs", operation_logs)):
        setattr(services, name, mod)
    _module(f"{root_name}.services.preferences", get_profile=lambda *_a, **_k: None)
    cache_obj = types.SimpleNamespace(snapshot=lambda *_a, **_k: [], refresh=lambda *_a, **_k: {}, clear_profile=lambda *_a, **_k: None)
    _module(f"{root_name}.services.torrent_cache", torrent_cache=cache_obj)
    _module(f"{root_name}.services.torrent_summary", cached_summary=lambda *_a, **_k: {})
    return _load(f"{root_name}.services.workers", ROOT / "pytorrent" / "services" / "workers.py")


def _load_profile_speed_limits(workers):
    root_name, _root, services = _base_service_root("limits")
    _db_stub(root_name)
    setattr(services, "workers", workers)
    sys.modules[f"{root_name}.services.workers"] = workers
    return _load(f"{root_name}.services.profile_speed_limits", ROOT / "pytorrent" / "services" / "profile_speed_limits.py")


def _load_backup():
    root_name, _root, services = _base_service_root("backup")
    _db_stub(root_name)
    auth = _module(
        f"{root_name}.services.auth",
        enabled=lambda: False,
        current_user_id=lambda: 1,
        can_access_profile=lambda *_a, **_k: True,
        can_write_profile=lambda *_a, **_k: True,
    )
    setattr(services, "auth", auth)
    return _load(f"{root_name}.services.backup", ROOT / "pytorrent" / "services" / "backup.py")


def _load_rt_config():
    root_name, _root, _services = _base_service_root("rtconfig")
    rt_pkg = _package(f"{root_name}.services.rtorrent", ROOT / "pytorrent" / "services" / "rtorrent")
    client = _module(
        f"{root_name}.services.rtorrent.client",
        connect=lambda: (_ for _ in ()).throw(RuntimeError("connect must be patched")),
        utcnow=lambda: NOW,
        client_for=lambda _profile: None,
    )
    setattr(rt_pkg, "client", client)
    return _load(f"{root_name}.services.rtorrent.config", ROOT / "pytorrent" / "services" / "rtorrent" / "config.py")


def _load_rt_torrents():
    root_name, _root, _services = _base_service_root("rttorrents")
    rt_pkg = _package(f"{root_name}.services.rtorrent", ROOT / "pytorrent" / "services" / "rtorrent")
    client = _module(f"{root_name}.services.rtorrent.client", client_for=lambda _profile: None, Binary=bytes)
    setattr(rt_pkg, "client", client)
    _module(
        f"{root_name}.services.rtorrent.files",
        export_torrent_file=lambda *_a, **_k: None,
        iter_remote_file_chunks=lambda *_a, **_k: [],
        set_file_priorities=lambda *_a, **_k: None,
    )
    _module(f"{root_name}.services.rtorrent.system", disk_usage_for_default_path=lambda *_a, **_k: {})
    return _load(f"{root_name}.services.rtorrent.torrents", ROOT / "pytorrent" / "services" / "rtorrent" / "torrents.py")


def _load_auth():
    root_name, _root, services = _base_service_root("auth")
    flask = _module(
        "flask",
        abort=lambda *_a, **_k: None,
        g=types.SimpleNamespace(),
        has_request_context=lambda: False,
        jsonify=lambda value: value,
        redirect=lambda value: value,
        request=types.SimpleNamespace(headers={}, remote_addr="", path="", method="GET", endpoint=""),
        session={},
        url_for=lambda *_a, **_k: "",
    )
    werkzeug = _package("werkzeug", ROOT)
    security = _module(
        "werkzeug.security",
        check_password_hash=lambda *_a, **_k: False,
        generate_password_hash=lambda value: f"hash:{value}",
    )
    setattr(werkzeug, "security", security)
    _module(
        f"{root_name}.config",
        AUTH_ENABLE=True,
        AUTH_PROVIDER="local",
        AUTH_PROXY_AUTO_CREATE=False,
        AUTH_PROXY_AUTO_CREATE_PERMISSION="ro",
        AUTH_PROXY_AUTO_CREATE_ROLE="user",
        AUTH_PROXY_USER_HEADER="X-User",
        API_ALLOWED_ORIGINS=set(),
        AUTH_BYPASS_HOSTS=set(),
        AUTH_BYPASS_USER="default",
        METRICS_PATH="/metrics",
    )
    _db_stub(root_name)
    deletion = _module(f"{root_name}.services.deletion", purge_user=lambda *_a, **_k: {"ok": True})
    setattr(services, "deletion", deletion)
    return _load(f"{root_name}.services.auth", ROOT / "pytorrent" / "services" / "auth.py")


def _add_user(conn, user_id: int, username: str, role: str = "user"):
    conn.execute(
        "INSERT INTO users(id,username,role,is_active,created_at,updated_at) VALUES(?,?,?,1,?,?)",
        (user_id, username, role, NOW, NOW),
    )
    conn.execute("INSERT INTO user_preferences(user_id,created_at,updated_at) VALUES(?,?,?)", (user_id, NOW, NOW))


def _add_profile(conn, profile_id: int, owner: int = 1, name: str | None = None, default: int = 0):
    conn.execute(
        "INSERT INTO rtorrent_profiles(id,user_id,name,scgi_url,is_default,created_at,updated_at) VALUES(?,?,?,'scgi://127.0.0.1:5000',?,?,?)",
        (profile_id, owner, name or f"p{profile_id}", default, NOW, NOW),
    )


def test_profile_import_rolls_back_complete_batch_on_database_failure(db):
    preferences = _load_preferences()
    preferences.connect = _connect_for(db)
    db.execute("CREATE TRIGGER fail_bad_profile BEFORE INSERT ON rtorrent_profiles WHEN NEW.name='bad' BEGIN SELECT RAISE(ABORT,'forced'); END")
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        preferences.import_profiles({"profiles": [
            {"name": "good", "scgi_url": "scgi://127.0.0.1:5001"},
            {"name": "bad", "scgi_url": "scgi://127.0.0.1:5002"},
        ]}, user_id=1)
    assert db.execute("SELECT COUNT(*) AS n FROM rtorrent_profiles").fetchone()["n"] == 0
    assert db.execute("SELECT active_rtorrent_id FROM user_preferences WHERE user_id=1").fetchone()["active_rtorrent_id"] is None


def test_editing_foreign_profile_sets_default_for_owner_not_actor(db):
    preferences = _load_preferences()
    preferences.connect = _connect_for(db)
    preferences.auth.can_write_profile = lambda *_a, **_k: True
    _add_user(db, 2, "owner")
    _add_profile(db, 10, owner=1, default=1)
    _add_profile(db, 20, owner=2, default=1)
    _add_profile(db, 21, owner=2, default=0)
    db.commit()
    preferences.update_profile(21, {"name": "new default", "scgi_url": "scgi://127.0.0.1:5021", "is_default": True}, user_id=1)
    assert db.execute("SELECT is_default FROM rtorrent_profiles WHERE id=10").fetchone()["is_default"] == 1
    assert db.execute("SELECT is_default FROM rtorrent_profiles WHERE id=20").fetchone()["is_default"] == 0
    assert db.execute("SELECT is_default FROM rtorrent_profiles WHERE id=21").fetchone()["is_default"] == 1


def test_automation_replace_import_is_all_or_nothing(db):
    rules = _load_automation_rules()
    rules.connect = _connect_for(db)
    rules._require_profile_write = lambda *_a, **_k: 1
    _add_profile(db, 10)
    old = db.execute(
        "INSERT INTO automation_rules(user_id,profile_id,name,enabled,conditions_json,effects_json,cooldown_minutes,created_at,updated_at) VALUES(1,10,'old',1,'[]','[]',0,?,?)",
        (NOW, NOW),
    ).lastrowid
    db.execute("INSERT INTO automation_rule_state(rule_id,profile_id,torrent_hash,updated_at) VALUES(?,10,'ABC',?)", (old, NOW))
    db.execute("CREATE TRIGGER fail_bad_rule BEFORE INSERT ON automation_rules WHEN NEW.name='bad' BEGIN SELECT RAISE(ABORT,'forced'); END")
    db.commit()
    payload = {"rules": [
        {"name": "good", "conditions": [{"type": "completed"}], "effects": [{"type": "stop"}]},
        {"name": "bad", "conditions": [{"type": "completed"}], "effects": [{"type": "stop"}]},
    ]}
    with pytest.raises(sqlite3.IntegrityError):
        rules.import_rules(10, payload, user_id=1, replace=True)
    assert [r["name"] for r in db.execute("SELECT name FROM automation_rules WHERE profile_id=10 ORDER BY id").fetchall()] == ["old"]
    assert db.execute("SELECT COUNT(*) AS n FROM automation_rule_state WHERE profile_id=10").fetchone()["n"] == 1


def test_automation_import_rejects_non_list_conditions_before_write(db):
    rules = _load_automation_rules()
    rules.connect = _connect_for(db)
    rules._require_profile_write = lambda *_a, **_k: 1
    _add_profile(db, 10)
    db.execute("INSERT INTO automation_rules(user_id,profile_id,name,enabled,conditions_json,effects_json,cooldown_minutes,created_at,updated_at) VALUES(1,10,'old',1,'[]','[]',0,?,?)", (NOW, NOW))
    db.commit()
    with pytest.raises(ValueError):
        rules.import_rules(10, {"rules": [{"name": "bad", "conditions": "completed", "effects": [{"type": "stop"}]}]}, user_id=1, replace=True)
    assert db.execute("SELECT name FROM automation_rules WHERE profile_id=10").fetchone()["name"] == "old"


def test_enqueue_many_rolls_back_every_job_if_one_insert_fails(db):
    workers = _load_workers()
    workers.connect = _connect_for(db)
    workers.dispatch_prepared_jobs = lambda _prepared: None
    workers._prepare_enqueue_payload = lambda _action, _pid, payload, _force: dict(payload)
    _add_profile(db, 10)
    db.execute("CREATE TRIGGER fail_bad_job BEFORE INSERT ON jobs WHEN NEW.action='bad' BEGIN SELECT RAISE(ABORT,'forced'); END")
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        workers.enqueue_many([
            {"action_name": "start", "profile_id": 10, "payload": {}, "user_id": 1},
            {"action_name": "bad", "profile_id": 10, "payload": {}, "user_id": 1},
        ])
    assert db.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"] == 0


def test_speed_limit_save_and_job_insert_share_one_transaction(db):
    workers = _load_workers()
    limits = _load_profile_speed_limits(workers)
    limits.connect = _connect_for(db)
    workers.dispatch_prepared_jobs = lambda _prepared: None
    workers._prepare_enqueue_payload = lambda _action, _pid, payload, _force: dict(payload)
    _add_profile(db, 10)
    db.execute("INSERT INTO profile_speed_limits(profile_id,down_limit,up_limit,created_at,updated_at) VALUES(10,10,20,?,?)", (NOW, NOW))
    db.execute("CREATE TRIGGER fail_limit_job BEFORE INSERT ON jobs WHEN NEW.action='set_limits' BEGIN SELECT RAISE(ABORT,'forced'); END")
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        limits.queue_limits(10, 100, 200, user_id=1)
    row = db.execute("SELECT down_limit,up_limit FROM profile_speed_limits WHERE profile_id=10").fetchone()
    assert (row["down_limit"], row["up_limit"]) == (10, 20)
    assert db.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"] == 0


def test_failed_speed_limit_job_revert_does_not_overwrite_newer_change(db):
    workers = _load_workers()
    limits = _load_profile_speed_limits(workers)
    limits.connect = _connect_for(db)
    _add_profile(db, 10)
    db.commit()
    limits.save_limits(10, 100, 200)
    assert limits.restore_limits_if_current(10, {"down": 100, "up": 200}, {"down": 10, "up": 20, "configured": True}) is True
    assert limits.get_limits(10)["down"] == 10
    limits.save_limits(10, 300, 400)
    assert limits.restore_limits_if_current(10, {"down": 100, "up": 200}, {"down": 10, "up": 20, "configured": True}) is False
    assert limits.get_limits(10)["down"] == 300


class _ConfigClient:
    def __init__(self, fail_key: str | None = None):
        self.values = {"throttle.global_down.max_rate": 10, "throttle.global_up.max_rate": 20}
        self.fail_key = fail_key

    def call(self, method, *args):
        if method.endswith(".set"):
            key = method[:-4]
            if key == self.fail_key:
                raise RuntimeError("setter failed")
            self.values[key] = int(args[-1])
            return 0
        return self.values.get(method, 0)


def test_rtorrent_config_persists_only_runtime_successes(db):
    config = _load_rt_config()
    config.connect = _connect_for(db)
    client = _ConfigClient(fail_key="throttle.global_up.max_rate")
    config.client_for = lambda _profile: client
    _add_profile(db, 10)
    db.commit()
    result = config.set_config({"id": 10}, {"throttle.global_down.max_rate": 100, "throttle.global_up.max_rate": 200}, apply_now=True)
    assert result["ok"] is False
    assert result["updated"] == ["throttle.global_down.max_rate"]
    assert db.execute("SELECT key,value FROM rtorrent_config_overrides WHERE profile_id=10").fetchall() == [{"key": "throttle.global_down.max_rate", "value": "100"}]
    assert client.values == {"throttle.global_down.max_rate": 100, "throttle.global_up.max_rate": 20}




def test_rtorrent_config_save_only_keeps_baseline_when_daemon_is_available(db):
    config = _load_rt_config()
    config.connect = _connect_for(db)
    client = _ConfigClient()
    config.client_for = lambda _profile: client
    _add_profile(db, 10)
    db.commit()
    result = config.set_config({"id": 10}, {"throttle.global_down.max_rate": 100}, apply_now=False)
    assert result["ok"] is True
    row = db.execute(
        "SELECT value,baseline_value FROM rtorrent_config_overrides WHERE profile_id=10 AND key='throttle.global_down.max_rate'"
    ).fetchone()
    assert row == {"value": "100", "baseline_value": "10"}
    assert client.values["throttle.global_down.max_rate"] == 10


def test_worker_observability_failures_do_not_break_job_state_helpers():
    workers = _load_workers()
    workers.operation_logs.record_job_event = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("log down"))
    workers.operation_logs.record_worker_event = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("log down"))
    workers._record_job_event(1, "start", "queued", {})
    workers._record_worker_event(1, "start", "timeout", "x")

    class BadSocket:
        def emit(self, *_a, **_k):
            raise RuntimeError("socket down")

    workers.set_socketio(BadSocket())
    workers._emit("job_update", {"profile_id": 1, "status": "done"})

def test_rtorrent_config_rolls_runtime_back_if_database_store_fails():
    config = _load_rt_config()
    client = _ConfigClient()
    config.client_for = lambda _profile: client
    config.store_config_overrides = lambda *_a, **_k: (_ for _ in ()).throw(sqlite3.OperationalError("db down"))
    with pytest.raises(sqlite3.OperationalError):
        config.set_config({"id": 10}, {"throttle.global_down.max_rate": 100}, apply_now=True)
    assert client.values["throttle.global_down.max_rate"] == 10


def test_rtorrent_speed_limits_roll_back_first_runtime_setter_if_second_fails():
    source = (ROOT / "pytorrent" / "services" / "rtorrent" / "torrents.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "set_limits")
    namespace = {}
    client = _ConfigClient(fail_key="throttle.global_up.max_rate")
    namespace["client_for"] = lambda _profile: client
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "rtorrent_set_limits_test", "exec"), namespace)
    with pytest.raises(RuntimeError):
        namespace["set_limits"]({"id": 10}, 100, 200)
    assert client.values == {"throttle.global_down.max_rate": 10, "throttle.global_up.max_rate": 20}


def test_app_restore_honors_explicit_empty_tables(db):
    backup = _load_backup()
    backup.connect = _connect_for(db)
    backup._require_admin = lambda *_a, **_k: None
    backup.payload_for_backup = lambda *_a, **_k: {"backup_type": "app", "tables": {"labels": []}}
    _add_profile(db, 10)
    db.execute("INSERT INTO labels(user_id,profile_id,name,created_at,updated_at) VALUES(1,10,'old',?,?)", (NOW, NOW))
    db.commit()
    result = backup.restore_app_backup(1, user_id=1)
    assert result["restored"]["labels"] == 0
    assert db.execute("SELECT COUNT(*) AS n FROM labels").fetchone()["n"] == 0


def test_app_restore_rejects_invalid_fk_snapshot_and_rolls_back(db):
    backup = _load_backup()
    backup.connect = _connect_for(db)
    backup._require_admin = lambda *_a, **_k: None
    backup.payload_for_backup = lambda *_a, **_k: {
        "backup_type": "app",
        "tables": {"profile_preferences": [{"user_id": 999, "profile_id": 999, "created_at": NOW, "updated_at": NOW}]},
    }
    _add_profile(db, 10)
    db.execute("INSERT INTO profile_preferences(user_id,profile_id,created_at,updated_at) VALUES(1,10,?,?)", (NOW, NOW))
    db.commit()
    with pytest.raises(ValueError, match="invalid database references"):
        backup.restore_app_backup(1, user_id=1)
    db.execute("PRAGMA foreign_keys=ON")
    assert db.execute("SELECT user_id,profile_id FROM profile_preferences").fetchone() == {"user_id": 1, "profile_id": 10}


def test_built_in_and_last_admin_cannot_be_demoted_or_deleted(db):
    auth = _load_auth()
    auth.connect = _connect_for(db)
    auth.require_admin = lambda: None
    auth.uses_external_provider = lambda: False
    auth.current_user_id = lambda: 99
    auth.default_user_id = lambda: 999
    with pytest.raises(ValueError, match="built-in"):
        auth.save_user({"username": "renamed", "role": "admin", "is_active": True}, user_id=1)
    with pytest.raises(ValueError, match="built-in"):
        auth.save_user({"username": "default", "role": "user", "is_active": True}, user_id=1)
    db.execute("DELETE FROM user_preferences WHERE user_id=1")
    db.execute("DELETE FROM users WHERE id=1")
    _add_user(db, 2, "sole-admin", role="admin")
    db.commit()
    with pytest.raises(ValueError, match="last active administrator"):
        auth.save_user({"username": "sole-admin", "role": "user", "is_active": True}, user_id=2)
    with pytest.raises(ValueError, match="last active administrator"):
        auth.delete_user(2)


def test_api_value_errors_have_global_400_handler_and_profile_selector_raises_valueerror():
    app_source = (ROOT / "pytorrent" / "__init__.py").read_text(encoding="utf-8")
    shared_source = (ROOT / "pytorrent" / "routes" / "_shared.py").read_text(encoding="utf-8")
    assert "@app.errorhandler(ValueError)" in app_source
    assert 'return jsonify({"ok": False, "error": message}), 400' in app_source
    assert 'raise ValueError("profile_id must be an integer")' in shared_source


def test_torrent_add_and_bulk_queue_only_after_complete_batch_validation():
    torrent_path = ROOT / "pytorrent" / "routes" / "torrents.py"
    shared_path = ROOT / "pytorrent" / "routes" / "_shared.py"
    torrent_tree = ast.parse(torrent_path.read_text(encoding="utf-8"))
    add_fn = next(node for node in torrent_tree.body if isinstance(node, ast.FunctionDef) and node.name == "torrent_add")
    # No enqueue call may live inside the uploaded-file validation loops.
    for loop in [n for n in ast.walk(add_fn) if isinstance(n, ast.For)]:
        names = {n.func.id for n in ast.walk(loop) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "enqueue" not in names
        assert "enqueue_many" not in names
    add_calls = [n for n in ast.walk(add_fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "enqueue_many"]
    assert len(add_calls) == 2  # multipart and JSON paths both queue as one batch

    shared_tree = ast.parse(shared_path.read_text(encoding="utf-8"))
    bulk_fn = next(node for node in shared_tree.body if isinstance(node, ast.FunctionDef) and node.name == "enqueue_bulk_parts")
    names = [n.func.id for n in ast.walk(bulk_fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert "enqueue_many" in names
