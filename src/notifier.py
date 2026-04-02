"""
Phase 3 — Telegram Notification

Reads data/latest_report.json and sends the formatted daily briefing to the
configured Telegram chat. Also exposes send_error_notification() so other
phases can push failure alerts without silently dying.

Run standalone (after analyzer.py has produced latest_report.json):
    python src/notifier.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telegram import Bot
from telegram.helpers import escape_markdown

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))
from models import ResonanceReport

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parents[1]
REPORT_PATH = ROOT / "data" / "latest_report.json"

# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

def _alert_emoji(cri: float) -> str:
    if cri >= 0.75:
        return "🚨"
    if cri >= 0.50:
        return "⚠️"
    return "🔭"


def _index_emoji(cri: float) -> str:
    if cri >= 0.75:
        return "🔴"
    if cri >= 0.50:
        return "🟡"
    return "🟢"


def _escape(text: str) -> str:
    """Escape text for Telegram MarkdownV2."""
    return escape_markdown(text, version=2)


def format_report(report: ResonanceReport) -> str:
    """
    Build the Telegram MarkdownV2 message string.
    Dynamic fields are escaped; structural symbols are hardcoded safe.
    """
    cri_pct = report.crisis_resonance_index * 100
    alert = _alert_emoji(report.crisis_resonance_index)
    idx_emoji = _index_emoji(report.crisis_resonance_index)

    signals = "\n".join(
        f"• {_escape(s)}" for s in report.top_signals
    ) or "• （无关键信号）"

    # Truncate executive_summary at 300 Chinese chars just in case
    summary = report.executive_summary[:300]

    return (
        f"{alert} *Project Resonance 日报 — {_escape(report.analysis_date)}*\n"
        f"\n"
        f"🇮🇱 *以色列内部压力指数:* {report.israel_pressure_score}/10\n"
        f"   {_escape(report.israel_pressure_reason)}\n"
        f"\n"
        f"🇮🇷 *伊朗内部压力指数:* {report.iran_pressure_score}/10\n"
        f"   {_escape(report.iran_pressure_reason)}\n"
        f"\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{idx_emoji} *冲突共振指数 \\(CRI\\):* {_escape(f'{cri_pct:.1f}')}%\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"📡 *驱动信号:*\n"
        f"{signals}\n"
        f"\n"
        f"📋 *深度简报:*\n"
        f"{_escape(summary)}\n"
        f"\n"
        f"📰 本次分析文章数: {report.articles_analyzed} 条"
    )


# ---------------------------------------------------------------------------
# Telegram send helpers
# ---------------------------------------------------------------------------

async def _send(token: str, chat_id: str, text: str) -> None:
    async with Bot(token=token) as bot:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="MarkdownV2",
        )


def _send_message(token: str, chat_id: str, text: str) -> None:
    asyncio.run(_send(token, chat_id, text))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_report(report: ResonanceReport) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.warning("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping notification.")
        return

    text = format_report(report)
    _send_message(token, chat_id, text)
    logger.info("Report sent to Telegram chat %s", chat_id)


def send_error_notification(phase: str, error: str) -> None:
    """Send a short failure alert. Called by other phases on unrecoverable errors."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return

    text = (
        f"🔴 *Resonance 运行失败*\n\n"
        f"阶段: {_escape(phase)}\n"
        f"错误: {_escape(error[:300])}"
    )
    try:
        _send_message(token, chat_id, text)
    except Exception as exc:
        logger.error("Failed to send error notification: %s", exc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not REPORT_PATH.exists():
        logger.error("latest_report.json not found at %s — run analyzer.py first.", REPORT_PATH)
        sys.exit(1)

    with open(REPORT_PATH, encoding="utf-8") as f:
        data = json.load(f)

    report = ResonanceReport(**data)
    send_report(report)


if __name__ == "__main__":
    main()
