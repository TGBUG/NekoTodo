from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select

from nekotodo import db, security, storage as storage_mod, tools
from nekotodo.config import load_settings
from nekotodo.models import Account, User


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nekotodo", description="NekoTodo admin CLI")
    parser.add_argument("--config", default=None, help="path to config file")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("register", help="manually register an account")
    p.add_argument("username")
    p.add_argument("--password", default=None, help="password (prompted if omitted)")
    p.add_argument("--timezone", default="UTC", help="user timezone, e.g. Asia/Shanghai")

    sub.add_parser("list-accounts", help="list registered accounts")

    p = sub.add_parser("revoke-all", help="invalidate all tokens of an account")
    p.add_argument("username")

    p = sub.add_parser("reset-password", help="reset a password and revoke all tokens")
    p.add_argument("username")
    p.add_argument("--password", default=None, help="new password (prompted if omitted)")

    p = sub.add_parser(
        "delete-account", help="delete an account and all of its data (tasks, source infos, files)"
    )
    p.add_argument("username")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    db.init_db(settings.database.path)
    await db.init_schema()
    storage_mod.init_storage(settings.files.dir)
    factory = db.get_session_factory()

    if args.command == "register":
        password = args.password or getpass.getpass("Password: ")
        if len(password) < 8:
            print("error: password must be at least 8 characters", file=sys.stderr)
            return 1
        async with factory() as session:
            existing = await session.execute(select(Account).where(Account.username == args.username))
            if existing.scalar_one_or_none() is not None:
                print(f"error: username '{args.username}' already taken", file=sys.stderr)
                return 1
            user = User(timezone=args.timezone)
            session.add(user)
            await session.flush()
            session.add(
                Account(
                    username=args.username,
                    password_hash=security.hash_password(password),
                    user_id=user.id,
                )
            )
            await session.commit()
        print(f"registered '{args.username}'")
        return 0

    if args.command == "list-accounts":
        async with factory() as session:
            result = await session.execute(select(Account).order_by(Account.id))
            for account in result.scalars().all():
                print(
                    f"{account.id}\t{account.username}\tuser_id={account.user_id}\t"
                    f"auth_version={account.auth_version}"
                )
        return 0

    if args.command == "revoke-all":
        async with factory() as session:
            result = await session.execute(select(Account).where(Account.username == args.username))
            account = result.scalar_one_or_none()
            if account is None:
                print(f"error: no such account '{args.username}'", file=sys.stderr)
                return 1
            account.auth_version += 1
            await session.commit()
        print(f"revoked all tokens for '{args.username}'")
        return 0

    if args.command == "reset-password":
        password = args.password or getpass.getpass("New password: ")
        if len(password) < 8:
            print("error: password must be at least 8 characters", file=sys.stderr)
            return 1
        async with factory() as session:
            result = await session.execute(select(Account).where(Account.username == args.username))
            account = result.scalar_one_or_none()
            if account is None:
                print(f"error: no such account '{args.username}'", file=sys.stderr)
                return 1
            account.password_hash = security.hash_password(password)
            account.auth_version += 1
            await session.commit()
        print(f"reset password for '{args.username}' (all tokens revoked)")
        return 0

    if args.command == "delete-account":
        async with factory() as session:
            result = await session.execute(select(Account).where(Account.username == args.username))
            account = result.scalar_one_or_none()
            if account is None:
                print(f"error: no such account '{args.username}'", file=sys.stderr)
                return 1
            if not args.yes:
                confirm = input(
                    f"this will permanently delete '{args.username}' and ALL of its data. "
                    "type the username to confirm: "
                )
                if confirm.strip() != args.username:
                    print("aborted", file=sys.stderr)
                    return 1
            await tools.delete_user(session, account.user_id)
            await session.commit()
        print(f"deleted account '{args.username}' and all its data")
        return 0

    print(f"unknown command: {args.command}", file=sys.stderr)
    return 1


def main() -> int:
    args = _build_parser().parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
