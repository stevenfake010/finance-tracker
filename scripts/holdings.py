#!/usr/bin/env python3
"""Holdings CRUD with two-phase confirm for write ops.

Subcommands:
    list     [--category C] [--account-id N] [--limit N]
    add      --name "易方达蓝筹" [--symbol 005827] [--shares 1234.5]
             [--account-id 1] [--category a_share] [--currency CNY]
             [--asset-kind fund_open] [--market-value 12345] [--confirm <token>]
    patch    <id> [--field=value ...] [--confirm <token>]
    delete   <id> [--confirm <token>]

Write ops first call without --confirm and print a preview ending in
CONFIRM_REQUIRED:<token>. The agent shows it to the user, asks yes/no,
and on yes calls again with --confirm <token>.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    CATEGORY_LABELS,
    delete,
    emit_json,
    fmt_cny,
    get_json,
    patch_json,
    post_json,
    require_confirm,
)

CATEGORIES = list(CATEGORY_LABELS.keys())
ASSET_KINDS = ["fund_open", "etf", "stock", "cash", "gold", "other"]
CURRENCIES = ["CNY", "USD", "HKD"]


def cmd_list(args):
    params: dict = {}
    if args.category:
        params["category"] = args.category
    if args.account_id is not None:
        params["account_id"] = args.account_id
    rows = get_json("/api/holdings", **params)
    if args.limit:
        rows = rows[: args.limit]
    # Trim to fields the agent needs to compose a reply — full row has
    # cost_basis, notes, timestamps the IM user rarely asks about.
    trimmed = [
        {
            "id": r["id"],
            "name": r["name"],
            "symbol": r.get("symbol"),
            "category": r.get("category"),
            "category_label": CATEGORY_LABELS.get(r.get("category", ""), r.get("category")),
            "category_source": r.get("category_source"),
            "currency": r.get("currency"),
            "shares": r.get("shares"),
            "latest_value_cny": r.get("latest_value_cny"),
            "latest_value_display": fmt_cny(r.get("latest_value_cny")),
            "account_id": r.get("account_id"),
            "account_name": r.get("account_name"),
        }
        for r in rows
    ]
    emit_json({"count": len(trimmed), "holdings": trimmed})


def cmd_add(args):
    payload = {
        "name": args.name,
        "symbol": args.symbol,
        "shares": args.shares,
        "market_value": args.market_value,
        "account_id": args.account_id,
        "currency": args.currency,
        "category": args.category,
        "category_source": "manual" if args.category else "inferred",
        "asset_kind": args.asset_kind,
        "notes": args.notes,
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    preview = [
        "操作: 新增持仓",
        f"  名称:   {payload.get('name')}",
        f"  代码:   {payload.get('symbol') or '—'}",
        f"  份额:   {payload.get('shares') or '—'}",
        f"  市值:   {fmt_cny(payload.get('market_value'))}",
        f"  币种:   {payload.get('currency', 'CNY')}",
        f"  分类:   {CATEGORY_LABELS.get(payload.get('category', ''), payload.get('category') or '自动推断')}",
        f"  账户:   account_id={payload.get('account_id') or '—'}",
    ]
    if not require_confirm(args.confirm, "holdings.add", payload, preview):
        return

    result = post_json("/api/holdings", payload)
    emit_json({"created": result})


def cmd_patch(args):
    fields = {}
    for k in ("name", "symbol", "shares", "market_value", "currency", "category",
              "asset_kind", "account_id", "cost_basis", "notes"):
        v = getattr(args, k.replace("-", "_"), None)
        if v is not None:
            fields[k] = v
    if not fields:
        print("error: 至少需要指定一个 --field=value", file=sys.stderr)
        sys.exit(2)
    if "category" in fields:
        fields["category_source"] = "manual"

    # Show what's actually changing — fetch the current row so the preview
    # is "old → new" rather than just "new", which is much harder to verify
    # on a small phone screen.
    current = get_json(f"/api/holdings", )
    cur_row = next((r for r in current if r["id"] == args.id), None)
    if not cur_row:
        print(f"error: holding #{args.id} not found", file=sys.stderr)
        sys.exit(1)

    preview = [f"操作: 修改持仓 #{args.id} ({cur_row['name']})"]
    for k, v in fields.items():
        if k == "category_source":
            continue
        old = cur_row.get(k)
        if k == "category":
            old = CATEGORY_LABELS.get(old, old)
            v_display = CATEGORY_LABELS.get(v, v)
            preview.append(f"  {k}: {old} → {v_display}")
        else:
            preview.append(f"  {k}: {old} → {v}")

    payload = {"id": args.id, "fields": fields}
    if not require_confirm(args.confirm, "holdings.patch", payload, preview):
        return

    result = patch_json(f"/api/holdings/{args.id}", fields)
    emit_json({"updated": result})


def cmd_delete(args):
    rows = get_json("/api/holdings")
    target = next((r for r in rows if r["id"] == args.id), None)
    if not target:
        print(f"error: holding #{args.id} not found", file=sys.stderr)
        sys.exit(1)

    preview = [
        f"操作: 永久删除持仓 #{args.id}",
        f"  名称:   {target['name']} ({target.get('symbol') or '无代码'})",
        f"  份额:   {target.get('shares') or '—'}",
        f"  市值:   {fmt_cny(target.get('latest_value_cny'))}",
        f"  分类:   {CATEGORY_LABELS.get(target.get('category', ''), target.get('category'))}",
        "",
        "⚠️  不可恢复 — 该持仓 + 关联净值历史将被一并清除。",
    ]
    if not require_confirm(args.confirm, "holdings.delete", {"id": args.id}, preview):
        return

    delete(f"/api/holdings/{args.id}")
    emit_json({"deleted_id": args.id})


def main():
    parser = argparse.ArgumentParser(description="Holdings CRUD")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list holdings")
    p_list.add_argument("--category", choices=CATEGORIES)
    p_list.add_argument("--account-id", type=int)
    p_list.add_argument("--limit", type=int)
    p_list.set_defaults(func=cmd_list)

    p_add = sub.add_parser("add", help="add a holding (requires --confirm)")
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--symbol")
    p_add.add_argument("--shares", type=float)
    p_add.add_argument("--market-value", type=float)
    p_add.add_argument("--account-id", type=int)
    p_add.add_argument("--currency", choices=CURRENCIES, default=None)
    p_add.add_argument("--category", choices=CATEGORIES)
    p_add.add_argument("--asset-kind", choices=ASSET_KINDS)
    p_add.add_argument("--notes")
    p_add.add_argument("--confirm")
    p_add.set_defaults(func=cmd_add)

    p_patch = sub.add_parser("patch", help="patch a holding (requires --confirm)")
    p_patch.add_argument("id", type=int)
    p_patch.add_argument("--name")
    p_patch.add_argument("--symbol")
    p_patch.add_argument("--shares", type=float)
    p_patch.add_argument("--market-value", type=float)
    p_patch.add_argument("--currency", choices=CURRENCIES)
    p_patch.add_argument("--category", choices=CATEGORIES)
    p_patch.add_argument("--asset-kind", choices=ASSET_KINDS)
    p_patch.add_argument("--account-id", type=int)
    p_patch.add_argument("--cost-basis", type=float)
    p_patch.add_argument("--notes")
    p_patch.add_argument("--confirm")
    p_patch.set_defaults(func=cmd_patch)

    p_del = sub.add_parser("delete", help="delete a holding (requires --confirm)")
    p_del.add_argument("id", type=int)
    p_del.add_argument("--confirm")
    p_del.set_defaults(func=cmd_delete)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
