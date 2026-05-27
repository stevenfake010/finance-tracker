#!/usr/bin/env python3
"""Account list/add/delete with two-phase confirm on writes.

Usage:
    uv run scripts/accounts.py list
    uv run scripts/accounts.py add --name "支付宝" --type alipay [--confirm <token>]
    uv run scripts/accounts.py delete <id> [--confirm <token>]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    delete,
    emit_json,
    get_json,
    post_json,
    require_confirm,
)


def cmd_list(_args):
    rows = get_json("/api/accounts")
    emit_json({"count": len(rows), "accounts": rows})


def cmd_add(args):
    payload = {"name": args.name, "type": args.type}
    preview = [
        "操作: 新建账户",
        f"  名称: {args.name}",
        f"  类型: {args.type or '(未指定)'}",
    ]
    if not require_confirm(args.confirm, "accounts.add", payload, preview):
        return
    result = post_json("/api/accounts", payload)
    emit_json({"created": result})


def cmd_delete(args):
    rows = get_json("/api/accounts")
    target = next((r for r in rows if r["id"] == args.id), None)
    if not target:
        print(f"error: account #{args.id} not found", file=sys.stderr)
        sys.exit(1)

    # Block deleting an account with holdings — backend would FK-fail with
    # a confusing error. Surface it here in plain Chinese instead.
    holdings = get_json("/api/holdings", account_id=args.id)
    if holdings:
        emit_json({
            "blocked": True,
            "reason": f"账户下还有 {len(holdings)} 条持仓,先转移或删除它们。",
            "holdings_sample": [h["name"] for h in holdings[:5]],
        })
        return

    preview = [
        f"操作: 删除账户 #{args.id} ({target['name']})",
        "  ⚠️  不可恢复",
    ]
    if not require_confirm(args.confirm, "accounts.delete", {"id": args.id}, preview):
        return
    delete(f"/api/accounts/{args.id}")
    emit_json({"deleted_id": args.id})


def main():
    parser = argparse.ArgumentParser(description="Accounts management")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list")
    p_list.set_defaults(func=cmd_list)

    p_add = sub.add_parser("add")
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--type", help="alipay / bank / broker / other")
    p_add.add_argument("--confirm")
    p_add.set_defaults(func=cmd_add)

    p_del = sub.add_parser("delete")
    p_del.add_argument("id", type=int)
    p_del.add_argument("--confirm")
    p_del.set_defaults(func=cmd_delete)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
