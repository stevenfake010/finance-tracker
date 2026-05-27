"""Shared HTTP client + chart utilities for finance-tracker skill scripts.

Two responsibilities:
1. `client()` — yield a configured httpx.Client pointed at the user's backend.
2. `setup_chart()` — install the bundled CJK font into matplotlib so charts
   render Chinese labels even on a font-bare server.

The skill is invoked headlessly. We force matplotlib's Agg backend before any
pyplot import — otherwise on a server without a display it tries Tkinter and
crashes with `cannot connect to X server`.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import httpx

# Constants mirrored from frontend/src/constants.js so the skill's charts and
# IM replies use the same taxonomy as the web UI.
CATEGORY_LABELS: dict[str, str] = {
    "a_share": "A 股",
    "us_stock": "美股",
    "hk_stock": "港股",
    "bond": "债券",
    "gold": "黄金",
    "commodity": "商品",
    "cash": "现金/货币",
    "other": "其他",
}

CATEGORY_COLORS: dict[str, str] = {
    "a_share": "#ef4444",
    "us_stock": "#3b82f6",
    "hk_stock": "#8b5cf6",
    "bond": "#10b981",
    "gold": "#f59e0b",
    "commodity": "#f97316",
    "cash": "#64748b",
    "other": "#94a3b8",
}

CATEGORY_ORDER: list[str] = list(CATEGORY_LABELS.keys())

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
SKILL_ROOT = Path(__file__).resolve().parent.parent


def _base_url() -> str:
    return os.environ.get("FINANCE_TRACKER_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _timeout() -> float:
    return float(os.environ.get("FINANCE_TRACKER_TIMEOUT", "30"))


@contextmanager
def client() -> Iterator[httpx.Client]:
    """Open an httpx.Client with the backend base URL + sensible timeout.

    We translate connection errors into a single, agent-friendly hint instead
    of a stack trace — the SKILL.md instructs the agent to look for this.
    """
    try:
        with httpx.Client(base_url=_base_url(), timeout=_timeout()) as c:
            yield c
    except httpx.ConnectError as e:
        die(
            f"无法连接后端 {_base_url()} ({e}). "
            "请确认 FastAPI 在跑: uv run uvicorn app.main:app --port 8000"
        )


def get_json(path: str, **params) -> dict | list:
    with client() as c:
        r = c.get(path, params=params or None)
        _raise_for_status(r)
        return r.json()


def post_json(path: str, payload: dict | list) -> dict | list:
    with client() as c:
        r = c.post(path, json=payload)
        _raise_for_status(r)
        return r.json() if r.content else {}


def patch_json(path: str, payload: dict) -> dict:
    with client() as c:
        r = c.patch(path, json=payload)
        _raise_for_status(r)
        return r.json()


def delete(path: str) -> None:
    with client() as c:
        r = c.delete(path)
        _raise_for_status(r)


def _raise_for_status(r: httpx.Response) -> None:
    if r.is_success:
        return
    try:
        body = r.json()
    except ValueError:
        body = r.text
    die(f"HTTP {r.status_code} {r.request.method} {r.request.url.path}: {body}")


def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    sys.exit(code)


# ---------- chart helpers ----------

def setup_chart() -> None:
    """Configure matplotlib for headless CJK rendering.

    Call this BEFORE the first `import matplotlib.pyplot`. We register every
    .ttf/.otf in the bundled `fonts/` dir, then point rcParams at the first
    one that supports CJK glyphs.
    """
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import font_manager, rcParams

    fonts_dir = SKILL_ROOT / "fonts"
    registered: list[str] = []
    if fonts_dir.is_dir():
        for f in sorted(fonts_dir.glob("*.[ot]tf")):
            try:
                font_manager.fontManager.addfont(str(f))
                registered.append(font_manager.FontProperties(fname=str(f)).get_name())
            except Exception:
                # A broken font shouldn't block the whole render — skip it.
                pass

    # Preference order: bundled font (if any) → common system CJK fallbacks
    # → DejaVu (renders boxes for CJK but at least labels Latin correctly).
    candidates = registered + [
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "PingFang SC",
        "Microsoft YaHei",
        "WenQuanYi Zen Hei",
        "DejaVu Sans",
    ]
    rcParams["font.sans-serif"] = candidates
    rcParams["axes.unicode_minus"] = False  # use ASCII hyphen for negative ticks


def output_chart_path(fig, name_hint: str) -> Path:
    """Save fig to a deterministic temp path and return it.

    Use a content hash so re-running the same query overwrites in place
    rather than littering /tmp.
    """
    import tempfile

    h = hashlib.md5(name_hint.encode()).hexdigest()[:10]
    out = Path(tempfile.gettempdir()) / f"finance-tracker-{name_hint}-{h}.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor="white")
    return out


# ---------- formatting ----------

def fmt_cny(v: float | None) -> str:
    """Format CNY with 万 / 亿 suffix when the magnitude calls for it."""
    if v is None:
        return "—"
    if abs(v) >= 1e8:
        return f"{v / 1e8:.2f} 亿"
    if abs(v) >= 1e4:
        return f"{v / 1e4:.2f} 万"
    return f"{v:,.0f}"


def emit_json(obj) -> None:
    """Print a single JSON object on stdout — what the agent parses."""
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def emit_chart(path: Path) -> None:
    """Trailer the agent looks for: `CHART:<abs path>`."""
    print(f"CHART:{path}")


# ---------- two-phase confirm ----------

def confirm_token(op: str, payload: dict) -> str:
    """Deterministic token for a write op. Same operation → same token, so
    the agent passing back `--confirm <token>` proves it really saw the
    preview we issued (and didn't fabricate a different op).
    """
    blob = json.dumps({"op": op, "payload": payload}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def require_confirm(args_confirm: str | None, op: str, payload: dict, preview_lines: list[str]) -> bool:
    """Returns True if the caller passed a matching `--confirm <token>`.

    If not, prints the preview block + CONFIRM_REQUIRED:<token> and returns
    False so the caller can `sys.exit(0)` cleanly.
    """
    expected = confirm_token(op, payload)
    if args_confirm == expected:
        return True
    if args_confirm:
        die(f"二次确认 token 不匹配,操作已拒绝。预期 {expected!r},收到 {args_confirm!r}。")
    print("=" * 50)
    for line in preview_lines:
        print(line)
    print("=" * 50)
    print(f"CONFIRM_REQUIRED:{expected}")
    return False
