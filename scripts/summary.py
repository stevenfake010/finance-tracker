#!/usr/bin/env python3
"""Print portfolio summary JSON + render allocation donut PNG.

Usage:
    uv run scripts/summary.py [--days 30] [--no-chart]

Output (stdout):
    {
      "date": "2026-05-13",
      "total_cny": 1234567.89,
      "change": {"diff": 12345.0, "pct": 1.01, "from_date": "2026-04-13"} | null,
      "categories": [{"category": "us_stock", "label": "美股", "value_cny": ..., "pct": ...}, ...],
      "category_count": 5,
      "zero_count": 2
    }
    CHART:/tmp/finance-tracker-summary-XXXX.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure the script can be invoked from any CWD via `uv run scripts/summary.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    CATEGORY_COLORS,
    CATEGORY_LABELS,
    emit_chart,
    emit_json,
    fmt_cny,
    get_json,
    output_chart_path,
    setup_chart,
)


def compute_change(trend: list[dict], current_total: float, days: int) -> dict | None:
    """Find the snapshot closest to (but not later than) `days` days ago.

    Mirrors frontend Dashboard.computeChange so IM and web report the same
    period-on-period number.
    """
    if not trend or len(trend) < 2 or current_total is None:
        return None
    from datetime import date, timedelta
    target = (date.today() - timedelta(days=days)).isoformat()
    earlier = next((p for p in reversed(trend) if p["date"] <= target), trend[0])
    base = earlier["total_cny"]
    if not base:
        return None
    diff = current_total - base
    return {
        "diff": diff,
        "pct": diff / base * 100.0,
        "from_date": earlier["date"],
    }


def render_donut(items: list[dict], total: float) -> Path:
    setup_chart()
    import matplotlib.pyplot as plt

    visible = [i for i in items if i["value_cny"] > 0]
    if not visible:
        # Caller should have guarded, but render an explicit empty plate
        # rather than crash on a divide-by-zero.
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.text(0.5, 0.5, "暂无可视化数据", ha="center", va="center", fontsize=14, color="#94a3b8")
        ax.axis("off")
        return output_chart_path(fig, "summary-empty")

    labels = [CATEGORY_LABELS.get(i["category"], i["category"]) for i in visible]
    values = [i["value_cny"] for i in visible]
    colors = [CATEGORY_COLORS.get(i["category"], "#94a3b8") for i in visible]

    fig, ax = plt.subplots(figsize=(7, 5.5))
    # Hide percent labels for tiny slices to avoid overlap; the legend shows
    # the full breakdown anyway.
    def _autopct(p):
        return f"{p:.1f}%" if p >= 3 else ""

    wedges, _texts, autotexts = ax.pie(
        values,
        colors=colors,
        autopct=_autopct,
        startangle=90,
        wedgeprops={"width": 0.42, "edgecolor": "white", "linewidth": 2},
        pctdistance=0.78,
    )
    for at in autotexts:
        at.set_color("white")
        at.set_fontsize(10)
        at.set_fontweight("bold")

    ax.text(
        0, 0.05, fmt_cny(total),
        ha="center", va="center", fontsize=20, fontweight="bold", color="#1e293b",
    )
    ax.text(0, -0.1, "总资产 (CNY)", ha="center", va="center", fontsize=10, color="#64748b")

    legend_labels = [f"{l} · {fmt_cny(v)}" for l, v in zip(labels, values)]
    ax.legend(
        wedges,
        legend_labels,
        loc="center left",
        bbox_to_anchor=(1.02, 0.5),
        frameon=False,
        fontsize=10,
    )
    ax.set_aspect("equal")
    return output_chart_path(fig, "summary-donut")


def main():
    parser = argparse.ArgumentParser(description="Portfolio summary")
    parser.add_argument("--days", type=int, default=30, help="window for change calc")
    parser.add_argument("--no-chart", action="store_true")
    args = parser.parse_args()

    allocation = get_json("/api/analytics/allocation")
    items = allocation.get("items", [])
    total = allocation.get("total_cny", 0.0)

    if not items:
        emit_json({
            "date": allocation.get("date"),
            "total_cny": 0,
            "change": None,
            "categories": [],
            "category_count": 0,
            "zero_count": 0,
            "empty": True,
            "hint": "尚无持仓。让用户在 Web 端添加,或发支付宝/招行截图过来导入。",
        })
        return

    trend = get_json("/api/analytics/trend", days=max(args.days + 30, 90)).get("points", [])
    change = compute_change(trend, total, args.days)

    enriched = [
        {
            "category": i["category"],
            "label": CATEGORY_LABELS.get(i["category"], i["category"]),
            "value_cny": i["value_cny"],
            "pct": i["pct"],
        }
        for i in items
    ]

    payload = {
        "date": allocation.get("date"),
        "total_cny": total,
        "change_window_days": args.days,
        "change": change,
        "categories": enriched,
        "category_count": sum(1 for i in items if i["value_cny"] > 0),
        "zero_count": sum(1 for i in items if i["value_cny"] == 0),
    }
    emit_json(payload)

    if not args.no_chart:
        path = render_donut(items, total)
        emit_chart(path)


if __name__ == "__main__":
    main()
