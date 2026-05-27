#!/usr/bin/env python3
"""Generate daily asset report with code-computed change data.

Outputs a formatted report to stdout AND writes to finance_refresh_done.txt.
All numbers come from backend API endpoints — the AI never computes change
manually. This eliminates the "涨跌幻觉" issue caused by mixing data sources.

Usage:
    uv run scripts/daily_report.py

Output format:
    💰 资产日报 · 2026-05-23
    总资产：¥2,999,404.90 · 较昨日 +¥5,356 (+0.18%)
      债券：¥1,332,128.24 (44.4%)
      ...
    n天后涨跌：近30天 -¥21,409 (-0.71%)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    CATEGORY_LABELS,
    CATEGORY_COLORS,
    CATEGORY_ORDER,
    client,
    die,
    fmt_cny,
    get_json,
)

OUTPUT_FILE = Path.home() / ".openclaw/workspace/bookkeeping/finance_refresh_done.txt"


def _sign(v: float) -> str:
    return "+" if v >= 0 else ""


def format_report() -> str:
    """Fetch all data from backend APIs and format a daily report.

    Returns the full report string. Also writes to OUTPUT_FILE.
    """
    # ── 1. Current allocation ──────────────────────────────────────────
    allocation = get_json("/api/analytics/allocation")
    if not allocation or not allocation.get("items"):
        return "⚠️ 暂无持仓数据，请先导入资产。"
    total = allocation["total_cny"]
    date_str = allocation.get("date", "")
    items = allocation["items"]

    # ── 2. Daily change (code-computed from snapshots) ─────────────────
    daily = get_json("/api/analytics/daily_change")
    diff = daily.get("diff")
    pct = daily.get("pct")

    # ── 3. 30-day change ───────────────────────────────────────────────
    trend = get_json("/api/analytics/trend", days=60).get("points", [])
    change_30d = None
    if trend and len(trend) > 1:
        from datetime import date, timedelta
        target = (date.today() - timedelta(days=30)).isoformat()
        earlier = next((p for p in reversed(trend) if p["date"] <= target), trend[0])
        base = earlier.get("total_cny")
        if base and base > 0:
            change_30d = {
                "diff": total - base,
                "pct": (total - base) / base * 100.0,
                "from_date": earlier.get("date"),
            }

    # ── 4. Build report ────────────────────────────────────────────────
    lines: list[str] = []

    # Header
    header = f"💰 资产日报 · {date_str}"
    if diff is not None and pct is not None:
        header += f" · 较昨日 {_sign(diff)}{fmt_cny(diff)} ({_sign(pct)}{pct:.2f}%)"
    lines.append(header)

    # Total
    lines.append(f"总资产：{fmt_cny(total)}")

    # Allocation breakdown (sorted by value descending, same as API)
    for i in items:
        cat = i["category"]
        v = i["value_cny"]
        p = i["pct"]
        label = CATEGORY_LABELS.get(cat, cat)
        lines.append(f"  {label}：{fmt_cny(v)} ({p:.1f}%)")

    # 30-day change
    if change_30d:
        c = change_30d
        actual_days = (date.today() - date.fromisoformat(c['from_date'])).days
        label = f"近{actual_days}天涨跌（自{c['from_date']}）"
        lines.append(
            f"{label}："
            f"{_sign(c['diff'])}{fmt_cny(c['diff'])} "
            f"({_sign(c['pct'])}{c['pct']:.2f}%)"
        )

    report = "\n".join(lines)

    # ── 5. Write to file ───────────────────────────────────────────────
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(report + "\n", encoding="utf-8")

    return report


def main():
    try:
        report = format_report()
        print(report)
    except Exception as e:
        die(f"日报生成失败: {e}")


if __name__ == "__main__":
    main()
