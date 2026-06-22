from __future__ import annotations
import argparse
import getpass
import sys
import json
from .db import connect, init_db, utcnow
from .services.auth import password_hash
from .services import tracker_cache


def reset_password(username: str, password: str) -> bool:
    """Note: Reset the selected user password hash without changing role or permissions."""
    username = (username or "").strip()
    if not username:
        raise ValueError("Username is required")
    if password is None or password == "":
        raise ValueError("Password cannot be empty")

    init_db()
    now = utcnow()
    hashed = password_hash(password)
    with connect() as conn:
        row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE users SET password_hash=?, updated_at=? WHERE username=?",
            (hashed, now, username),
        )
    return True


def revoke_api_token_cli(identifier: str, username: str = "") -> int:
    """Note: Revoke an API token by numeric id or visible token prefix without starting the web UI."""
    token = str(identifier or "").strip()
    if not token:
        raise ValueError("Token id or prefix is required")
    init_db()
    now = utcnow()
    params: list = []
    where = ""
    if token.isdigit():
        where = "t.id=?"
        params.append(int(token))
    else:
        where = "t.token_prefix=?"
        params.append(token)
    if username:
        where += " AND u.username=?"
        params.append(str(username).strip())
    with connect() as conn:
        row = conn.execute(
            f"SELECT t.id FROM api_tokens t JOIN users u ON u.id=t.user_id WHERE {where} AND t.revoked_at IS NULL",
            tuple(params),
        ).fetchone()
        if not row:
            return 0
        conn.execute("UPDATE api_tokens SET revoked_at=?, updated_at=? WHERE id=?", (now, now, int(row["id"])))
    return 1



def fetch_tracker_favicon(domain: str, refresh: bool = True, debug: bool = False) -> str:
    """Note: Download or refresh one tracker favicon from CLI without starting the web server."""
    clean = tracker_cache.tracker_domain(domain)
    if not clean:
        raise ValueError("Tracker domain is required")
    init_db()
    path, mime = tracker_cache.favicon_path(clean, enabled=True, force=refresh)
    row = tracker_cache.favicon_cache_row(clean)
    if not path:
        detail = (row or {}).get("error") if row else "favicon not found"
        if debug and row:
            raise RuntimeError(f"{detail or 'favicon not found'}; cache={json.dumps(dict(row), default=str)}")
        raise RuntimeError(str(detail or "favicon not found"))
    if debug and row:
        return f"{path} ({mime or 'unknown'}) cache={json.dumps(dict(row), default=str)}"
    return f"{path} ({mime or 'unknown'})"

def _password_from_args(args: argparse.Namespace) -> str:
    """Note: Allow the password to be passed as an argument or entered securely in interactive mode."""
    if args.password is not None:
        return args.password
    first = getpass.getpass("New password: ")
    second = getpass.getpass("Repeat password: ")
    if first != second:
        raise ValueError("Passwords do not match")
    return first


def build_parser() -> argparse.ArgumentParser:
    """Note: Define simple administrative commands launched with python -m pytorrent.cli."""
    parser = argparse.ArgumentParser(description="pyTorrent CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    reset = sub.add_parser("reset-password", help="Reset password for an existing user")
    reset.add_argument("username", help="User login")
    reset.add_argument("password", nargs="?", help="New password; omit to type it interactively")
    reset.set_defaults(func=_cmd_reset_password)

    token = sub.add_parser("revoke-api-token", help="Revoke an API token by id or visible prefix")
    token.add_argument("identifier", help="Token id or token_prefix shown in the Users tab")
    token.add_argument("--user", default="", help="Optional username filter for safety")
    token.set_defaults(func=_cmd_revoke_api_token)

    icon = sub.add_parser("tracker-favicon", help="Download or refresh a tracker favicon cache file")
    icon.add_argument("domain", help="Tracker domain e.g tracker.example.com")
    icon.add_argument("--no-refresh", action="store_true", help="Use fresh cache when available")
    icon.add_argument("--debug", action="store_true", help="Print cache diagnostics on success or failure")
    icon.set_defaults(func=_cmd_tracker_favicon)

    return parser


def _cmd_reset_password(args: argparse.Namespace) -> int:
    """Note: Run the password reset and return a readable terminal status."""
    password = _password_from_args(args)
    if reset_password(args.username, password):
        print(f"Password reset for user: {args.username}")
        return 0
    print(f"User not found: {args.username}", file=sys.stderr)
    return 1


def _cmd_revoke_api_token(args: argparse.Namespace) -> int:
    """Note: Revoke API tokens safely from CLI when the web UI is unavailable."""
    count = revoke_api_token_cli(args.identifier, username=args.user or "")
    if count:
        print(f"API token revoked: {args.identifier}")
        return 0
    print(f"Active API token not found: {args.identifier}", file=sys.stderr)
    return 1


def _cmd_tracker_favicon(args: argparse.Namespace) -> int:
    """Note: Run favicon discovery from CLI and print the saved file path."""
    print(fetch_tracker_favicon(args.domain, refresh=not args.no_refresh, debug=bool(args.debug)))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Note: Main CLI entrypoint with error handling and without starting the web app."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
