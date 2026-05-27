#!/usr/bin/env python3
"""Screenshot-driven holdings sync for manual position adjustments.

Two-phase flow:
  preview  — POST /api/holdings/sync (apply=false) → show diff, DO NOT save
  apply    — POST /api/holdings/sync (apply=true)  → execute + recompute snapshot

Usage:
    # Preview (dry-run)
    uv run scripts/sync_holdings.py preview <account_id> '<json_items>'

    # Apply (execute)
    uv run scripts/sync_holdings.py apply <account_id> '<json_items>'

    <json_items> is a JSON array of {name, market_value} objects.

Output (JSON):
    {
      "account_id": 4,
      "applied": false,
      "changes": [...],
      "summary": { "matched": 5, "new": 1, "orphaned": 3 }
    }
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import CATEGORY_LABELS, client, die, emit_json, fmt_cny  # noqa: E402


def _build_preview_table(result: dict) -> str:
    """Render a human-readable table for the AI to present to the user."""
    lines: list[str] = []
    changes = result.get("changes", [])
    summary = result.get("summary", {})

    lines.append("")
    lines.append(f"📊 账户 #{result['account_id']} 调仓预览")
    lines.append(
        f"   匹配更新: {summary.get('matched', 0)} | "
        f"新增待确认: {summary.get('new', 0)} | "
        f"截图无但DB有: {summary.get('orphaned', 0)}"
    )
    lines.append("")

    matched = [c for c in changes if c["action"] == "matched"]
    new_items = [c for c in changes if c["action"] == "new"]
    orphaned = [c for c in changes if c["action"] == "orphaned"]

    if matched:
        lines.append("✅ 已匹配 · 将自动更新")
        for c in matched:
            old_mv = c.get("old_market_value")
            new_mv = c.get("new_market_value")
            diff = ""
            if old_mv is not None and new_mv is not None:
                delta = new_mv - old_mv
                sign = "+" if delta >= 0 else ""
                diff = f"  ({sign}{fmt_cny(delta)})"
            note = f"  [{c.get('diff_note')}]" if c.get("diff_note") else ""
            lines.append(
                f"  {c['db_name']}  →  {fmt_cny(new_mv)}{diff}{note}"
            )
        lines.append("")

    if new_items:
        lines.append("🆕 新增 · 需手动确认分类")
        for i, c in enumerate(new_items):
            lines.append(f"  [{i}] {c['ss_name']}  {fmt_cny(c.get('new_market_value'))}")
        lines.append("")

    if orphaned:
        lines.append("❓ 截图无但DB有 · 可能已清仓或漏截")
        for c in orphaned:
            mv = c.get("old_market_value")
            cat = CATEGORY_LABELS.get(
                c.get("old_category", ""), c.get("old_category", "?")
            ) if c.get("old_category") else ""
            lines.append(f"  {c['db_name']}  {fmt_cny(mv)}  {cat}")
        lines.append("")

    return "\n".join(lines)


def cmd_preview(args):
    try:
        items = json.loads(args.json_items)
    except json.JSONDecodeError as e:
        die(f"JSON 解析失败: {e}")

    payload = {
        "account_id": args.account_id,
        "items": items,
        "apply": False,
    }

    with client() as c:
        r = c.post("/api/holdings/sync", json=payload,
                   timeout=30.0)
    if not r.is_success:
        try:
            body = r.json()
        except ValueError:
            body = r.text
        die(f"sync preview 失败 HTTP {r.status_code}: {body}")

    result = r.json()
    emit_json(result)

    # Also print the table for the AI to consume
    print(_build_preview_table(result), file=sys.stderr)


def cmd_apply(args):
    try:
        items = json.loads(args.json_items)
    except json.JSONDecodeError as e:
        die(f"JSON 解析失败: {e}")

    # Allow overrides for "new" items: --override 0:category=bond --override 1:symbol=000123
    overrides: dict[int, dict] = {}
    if args.override:
        for ov in args.override:
            idx_str, rest = ov.split(":", 1)
            field, value = rest.split("=", 1)
            idx = int(idx_str)
            if idx not in overrides:
                overrides[idx] = {}
            # category, symbol, asset_kind, currency all accepted
            overrides[idx][field] = value

    # Apply overrides to items
    for idx, fields in overrides.items():
        if 0 <= idx < len(items):
            items[idx].update(fields)

    payload = {
        "account_id": args.account_id,
        "items": items,
        "apply": True,
    }

    with client() as c:
        r = c.post("/api/holdings/sync", json=payload,
                   timeout=30.0)
    if not r.is_success:
        try:
            body = r.json()
        except ValueError:
            body = r.text
        die(f"sync apply 失败 HTTP {r.status_code}: {body}")

    result = r.json()
    emit_json(result)

    summary = result.get("summary", {})
    print(
        f"\n✅ 已更新 {summary.get('updated', 0)} 条, "
        f"删除 {summary.get('orphaned', 0)} 条",
        file=sys.stderr,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Screenshot-driven holdings sync"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_prev = sub.add_parser("preview", help="dry-run match and show diff")
    p_prev.add_argument("account_id", type=int)
    p_prev.add_argument("json_items")
    p_prev.set_defaults(func=cmd_preview)

    p_apply = sub.add_parser("apply", help="execute sync and update DB")
    p_apply.add_argument("account_id", type=int)
    p_apply.add_argument("json_items")
    p_apply.add_argument("--override", action="append", default=[],
                         help="per-item edit: '<index>:<field>=<value>', repeatable")
    p_apply.set_defaults(func=cmd_apply)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
