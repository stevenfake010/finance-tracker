#!/usr/bin/env python3
"""Portfolio value trend chart.

Two modes:
  stacked  — area chart, one band per category, biggest at the bottom of the
             stack. Best for "组合演变" questions.
  line     — single line of total CNY over time. Best for "总额走势" questions.

Usage:
    uv run scripts/trend.py [--days 365] [--mode stacked|line]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    CATEGORY_COLORS,
    CATEGORY_LABELS,
    CATEGORY_ORDER,
    emit_chart,
    emit_json,
    fmt_cny,
    get_json,
    output_chart_path,
    setup_chart,
)


def render(points: list[dict], mode: str, days: int) -> Path:
    setup_chart()
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from datetime import datetime

    dates = [datetime.fromisoformat(p["date"]) for p in points]
    totals = [p["total_cny"] for p in points]

    fig, ax = plt.subplots(figsize=(10, 5))

    if mode == "line":
        ax.plot(dates, totals, color="#0ea5e9", linewidth=2.0)
        ax.fill_between(dates, totals, color="#0ea5e9", alpha=0.08)
        ax.set_title(f"资产总额近 {days} 天", fontsize=12, color="#334155", loc="left", pad=12)
    else:
        # Build a category-by-time matrix, zero-fill missing categories so
        # stackplot doesn't blow up on ragged inputs.
        per_cat: dict[str, list[float]] = {c: [] for c in CATEGORY_ORDER}
        for p in points:
            br = p.get("breakdown") or {}
            for c in CATEGORY_ORDER:
                per_cat[c].append(br.get(c, 0.0))
        # Visible = ever non-zero; sort by total contribution desc so the
        # biggest band sits at the bottom of the stack.
        totals_per_cat = {c: sum(v) for c, v in per_cat.items()}
        visible = [c for c in CATEGORY_ORDER if totals_per_cat[c] > 0]
        visible.sort(key=lambda c: -totals_per_cat[c])

        if not visible:
            ax.text(0.5, 0.5, "暂无趋势数据", ha="center", va="center",
                    fontsize=14, color="#94a3b8", transform=ax.transAxes)
            ax.axis("off")
            return output_chart_path(fig, "trend-empty")

        ax.stackplot(
            dates,
            *[per_cat[c] for c in visible],
            labels=[CATEGORY_LABELS.get(c, c) for c in visible],
            colors=[CATEGORY_COLORS.get(c, "#94a3b8") for c in visible],
            alpha=0.85,
        )
        ax.legend(loc="upper left", fontsize=9, frameon=False, ncol=2)
        ax.set_title(f"资产分类堆积近 {days} 天", fontsize=12, color="#334155", loc="left", pad=12)

    # Axis polish — same look-and-feel for both modes.
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#cbd5e1")
    ax.spines["bottom"].set_color("#cbd5e1")
    ax.tick_params(colors="#64748b", labelsize=9)
    ax.grid(axis="y", linestyle="--", linewidth=0.5, color="#e2e8f0")

    # X axis: switch tick density and label format based on the time span
    # so 7-day and 365-day plots both stay readable.
    span = (dates[-1] - dates[0]).days if len(dates) > 1 else 1
    if span <= 31:
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=max(span // 8, 1)))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    elif span <= 180:
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=max(span // 56, 1)))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    else:
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=max(span // 270, 1)))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate(rotation=0, ha="center")

    # Y axis: 万 / 亿 suffix to match the IM caption format.
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: fmt_cny(v)))

    return output_chart_path(fig, f"trend-{mode}-{days}")


def main():
    parser = argparse.ArgumentParser(description="Portfolio trend chart")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--mode", choices=["stacked", "line"], default="stacked")
    args = parser.parse_args()

    data = get_json("/api/analytics/trend", days=args.days)
    points = data.get("points", [])

    if len(points) < 2:
        emit_json({
            "points": len(points),
            "empty": True,
            "hint": "趋势图至少需要 2 天数据。先触发一次 refresh 让 snapshots 表写入。",
        })
        return

    first = points[0]
    last = points[-1]
    diff = last["total_cny"] - first["total_cny"]
    pct = (diff / first["total_cny"] * 100.0) if first["total_cny"] else 0.0

    emit_json({
        "mode": args.mode,
        "days": args.days,
        "from": {"date": first["date"], "total_cny": first["total_cny"]},
        "to":   {"date": last["date"],  "total_cny": last["total_cny"]},
        "diff": diff,
        "pct": pct,
        "point_count": len(points),
    })

    path = render(points, args.mode, args.days)
    emit_chart(path)


if __name__ == "__main__":
    main()
