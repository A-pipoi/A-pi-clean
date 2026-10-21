"""Local account administration. Passwords never enter command-line arguments."""

from __future__ import annotations

import argparse
import getpass
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant.api.auth_store import AuthStore
from quant.api.two_factor import TwoFactor


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(
            os.getenv(
                "A_PI_DATA_ROOT", str(Path(__file__).resolve().parents[1] / "data")
            )
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for command in (
        "adduser",
        "passwd",
        "disable",
        "enable",
        "reset-sessions",
        "reset-2fa",
    ):
        action = sub.add_parser(command)
        action.add_argument("username")
        if command == "adduser":
            action.add_argument("--role", choices=("admin", "researcher"))
    sub.add_parser("list")
    args = parser.parse_args()
    store = AuthStore(args.data_root / "auth" / "users.sqlite3")
    try:
        if args.command in {"adduser", "passwd"}:
            password = getpass.getpass("Password (10–1024 characters): ")
            if password != getpass.getpass("Confirm password: "):
                raise ValueError("passwords do not match")
            if args.command == "adduser":
                store.create_user(
                    args.username,
                    password,
                    args.role,
                    must_change_password=bool(store.list_users()),
                )
            else:
                store.set_password(args.username, password, must_change_password=True)
        elif args.command in {"disable", "enable"}:
            store.set_disabled(args.username, args.command == "disable")
        elif args.command == "reset-sessions":
            store.reset_sessions(args.username)
        elif args.command == "reset-2fa":
            user = store.get_by_username(args.username)
            if not user:
                raise ValueError("user not found")
            confirmation = input("Type the username to confirm local 2FA recovery: ")
            if confirmation != user.username:
                raise ValueError("confirmation did not match")
            TwoFactor(store.path).disable(user.id)
            store.reset_sessions(user.username)
            store.audit(None, "console.reset_2fa", user.username, "local-console")
        else:
            for user in store.list_users():
                print(
                    f"{user.username}\t{user.role}\t{'disabled' if user.disabled else 'enabled'}"
                )
            return
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, f"Error: {error}\n")
    print(f"{args.command}: {args.username}")


if __name__ == "__main__":
    main()
