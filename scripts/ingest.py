#!/usr/bin/env python3
"""Two-phase screenshot import.

  parse <image-path>            — call vision, cache the parsed preview
                                   on disk, return preview_id + items
  save  <preview-id>            — apply edits, batch-insert into holdings
        [--account 1]              global default account for items missing one
        [--override 0:name=...]    per-row field edits before save
        [--confirm <token>]

The two-phase split exists because vision is slow + costs money: re-running
it just to fix a typo would be wasteful, and chat-IM is too narrow a channel
to do live JSON editing.

Cached previews live under $TMPDIR for 30 minutes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    CATEGORY_LABELS,
    client,
    die,
    emit_json,
    fmt_cny,
    post_json,
    require_confirm,
)

PREVIEW_TTL_SECONDS = 30 * 60
_CACHE_DIR = Path(tempfile.gettempdir()) / "finance-tracker-previews"


def _cache_path(preview_id: str) -> Path:
    return _CACHE_DIR / f"{preview_id}.json"


def _save_preview(items: list[dict], raw_text: str | None) -> str:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(items, sort_keys=True, ensure_ascii=False).encode()
    pid = "p_" + hashlib.sha256(blob + str(time.time()).encode()).hexdigest()[:8]
    _cache_path(pid).write_text(
        json.dumps({"items": items, "raw_text": raw_text, "ts": time.time()},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return pid


def _load_preview(preview_id: str) -> list[dict]:
    p = _cache_path(preview_id)
    if not p.exists():
        die(f"预览 {preview_id} 不存在或已被清理。请重新发图。")
    age = time.time() - p.stat().st_mtime
    if age > PREVIEW_TTL_SECONDS:
        die(f"预览 {preview_id} 已过期({age/60:.0f} 分钟前生成)。请重新发图。")
    return json.loads(p.read_text(encoding="utf-8"))["items"]


def _apply_overrides(items: list[dict], overrides: list[str]) -> list[dict]:
    """Each override: '<index>:<field>=<value>'. Field is one of
    name|symbol|shares|category|currency|account_id|market_value.
    Numeric fields auto-cast.
    """
    valid_fields = {"name", "symbol", "shares", "category", "currency",
                    "account_id", "market_value", "account_hint"}
    numeric = {"shares", "account_id", "market_value"}
    items = [dict(i) for i in items]  # copy
    for ov in overrides:
        try:
            idx_str, rest = ov.split(":", 1)
            field, value = rest.split("=", 1)
            idx = int(idx_str)
        except ValueError:
            die(f"无法解析 override {ov!r},格式应为 '<index>:<field>=<value>'")
        if field not in valid_fields:
            die(f"override 字段 {field!r} 不允许,可用: {sorted(valid_fields)}")
        if idx < 0 or idx >= len(items):
            die(f"override 索引 {idx} 越界(共 {len(items)} 条)")
        if field in numeric:
            try:
                items[idx][field] = float(value) if field != "account_id" else int(value)
            except ValueError:
                die(f"override 值 {value!r} 无法转为数字")
        else:
            items[idx][field] = value
        # If the user changed category, mark it manual so the classifier
        # won't overwrite it on save.
        if field == "category":
            items[idx]["category_source"] = "manual"
    return items


def cmd_parse(args):
    image_path = Path(args.image_path).expanduser().resolve()
    if not image_path.is_file():
        die(f"找不到图片: {image_path}")
    mime, _ = mimetypes.guess_type(image_path)
    if not mime or not mime.startswith("image/"):
        die(f"不是图片文件: {image_path} (mime={mime})")

    with client() as c, image_path.open("rb") as fh:
        files = {"file": (image_path.name, fh, mime)}
        r = c.post("/api/ingest/screenshot", files=files,
                   timeout=float(os.environ.get("FINANCE_TRACKER_TIMEOUT", "60")))
    if not r.is_success:
        try:
            body = r.json()
        except ValueError:
            body = r.text
        die(f"vision 调用失败 HTTP {r.status_code}: {body}")
    data = r.json()
    items = data.get("parsed", [])
    if not items:
        emit_json({
            "preview_id": None,
            "items": [],
            "raw_text": data.get("raw_text"),
            "hint": "vision 没返回任何条目。可能是截图不清晰或不是持仓页。",
        })
        return

    preview_id = _save_preview(items, data.get("raw_text"))
    # Decorate items with index + category label so the agent can render a
    # clean table without joining to constants.
    decorated = [
        {
            "index": idx,
            "name": it.get("name"),
            "symbol": it.get("symbol"),
            "shares": it.get("shares"),
            "market_value": it.get("market_value"),
            "market_value_display": fmt_cny(it.get("market_value")),
            "category": it.get("category"),
            "category_label": CATEGORY_LABELS.get(it.get("category", ""), it.get("category")),
            "currency": it.get("currency", "CNY"),
            "account_hint": it.get("account_hint"),
        }
        for idx, it in enumerate(items)
    ]
    emit_json({
        "preview_id": preview_id,
        "expires_in_seconds": PREVIEW_TTL_SECONDS,
        "count": len(items),
        "items": decorated,
        "raw_text": data.get("raw_text"),
        "next_step": (
            "向用户展示这些条目,确认无误后调用: "
            f"uv run scripts/ingest.py save {preview_id} --account <id> [--override ...]"
        ),
    })


def cmd_save(args):
    items = _load_preview(args.preview_id)
    if args.override:
        items = _apply_overrides(items, args.override)

    # Apply default account for any item that's still missing one. Backend
    # will accept account_id=None but the user almost always wants holdings
    # tied to an account.
    if args.account is not None:
        for it in items:
            if it.get("account_id") is None:
                it["account_id"] = args.account

    # Strip preview-only fields that the backend rejects.
    cleaned = []
    for it in items:
        clean = {k: v for k, v in it.items() if k != "account_hint"}
        cleaned.append(clean)

    payload = {"holdings": cleaned, "backfill_history": True}

    preview_lines = [f"操作: 批量保存 {len(cleaned)} 条持仓"]
    for i, it in enumerate(cleaned):
        cat = CATEGORY_LABELS.get(it.get("category", ""), it.get("category") or "?")
        preview_lines.append(
            f"  [{i}] {it.get('name')} "
            f"({it.get('symbol') or 'no-sym'}) "
            f"× {it.get('shares') or '—'} "
            f"= {fmt_cny(it.get('market_value'))} "
            f"[{cat}] → account_id={it.get('account_id') or '—'}"
        )
    preview_lines.append("")
    preview_lines.append("保存后会触发后台回填近 365 天净值(可能耗时 30-120 秒)。")

    if not require_confirm(args.confirm, "ingest.save",
                           {"preview_id": args.preview_id, "items": cleaned},
                           preview_lines):
        return

    result = post_json("/api/holdings/batch", payload)
    # Don't keep preview after successful save — prevents accidental double-saves.
    try:
        _cache_path(args.preview_id).unlink()
    except FileNotFoundError:
        pass
    emit_json({
        "saved_count": len(result) if isinstance(result, list) else 0,
        "saved_ids": [r.get("id") for r in result] if isinstance(result, list) else [],
        "note": "净值回填在后台进行,稍后用 refresh 或直接看 summary 即可。",
    })


def main():
    parser = argparse.ArgumentParser(description="Screenshot ingest (two-phase)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_parse = sub.add_parser("parse", help="parse image → preview JSON + cache")
    p_parse.add_argument("image_path")
    p_parse.set_defaults(func=cmd_parse)

    p_save = sub.add_parser("save", help="apply preview to DB (requires --confirm)")
    p_save.add_argument("preview_id")
    p_save.add_argument("--account", type=int, help="default account_id for items lacking one")
    p_save.add_argument("--override", action="append", default=[],
                        help="per-row edit: '<index>:<field>=<value>', repeatable")
    p_save.add_argument("--confirm")
    p_save.set_defaults(func=cmd_save)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
