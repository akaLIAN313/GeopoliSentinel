"""
Phase 2 — Daily AI Analysis (DRAFT)
Loads state.md + today's delta articles, calls Claude with tool use to produce
a structured ResonanceReport and an updated state.md in one shot.

How the main work function hands the pipeline output to the agent
-----------------------------------------------------------------
1.  fetcher.run_pipeline()   →  list[Article]           (delta, ≤20 items)
2.  load_state()             →  str | None              (yesterday's state.md)
3.  build_user_message()     →  str                     (state + articles, formatted)
4.  client.messages.create() →  tool_use block          (ResonanceReport + new state)
5.  save_state()  +  save_report()                       (persist artefacts)
6.  return ResonanceReport                               (handed to notifier)

The agent's "skill" contract
----------------------------
Claude receives a single user message containing:
  - Section 1: previous state.md (or a cold-start notice)
  - Section 2: today's delta articles grouped by stream
Claude MUST respond with exactly one `submit_analysis` tool call containing:
  - All ResonanceReport fields (scores, CRI, summary, signals)
  - updated_state_document: the new state.md it wrote for tomorrow
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))
from fetcher import load_articles, run_pipeline, save_articles
from models import Article, ResonanceReport
from polymarket import fetch_and_save as fetch_polymarket
from prompts import load_daily_system_prompt, SUBMIT_ANALYSIS_TOOL

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parents[1]
STATE_PATH = ROOT / "data" / "state.md"
ARTICLES_PATH = ROOT / "data" / "articles.json"
REPORT_PATH = ROOT / "data" / "latest_report.json"
HISTORY_DIR = ROOT / "data" / "history"

# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def load_state() -> str | None:
    if STATE_PATH.exists():
        return STATE_PATH.read_text(encoding="utf-8")
    return None


def save_state(content: str) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(content, encoding="utf-8")


def save_report(report: ResonanceReport) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    payload = report.model_dump(mode="json")
    REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    history_path = HISTORY_DIR / f"{report.analysis_date}.json"
    history_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Message builder  — this is the "skill" input assembly
# ---------------------------------------------------------------------------

def build_user_message(state: str | None, articles: list[Article]) -> str:
    """
    Compose the user-turn message that the agent receives.

    Structure
    ---------
    ## 当前状态文档
    <state.md content, or cold-start notice>

    ---

    ## 今日增量新闻 (N 条)

    ### 数据流 A — 以色列内部危机
    [A] <source> | <date>
    标题: ...
    摘要: ...

    ### 数据流 B — 伊朗内部危机
    ...

    ### 数据流 C — 军事异动（参照）
    ...
    """
    parts: list[str] = []

    # --- Section 1: state ---
    if state:
        parts.append(f"## 当前状态文档\n\n{state.strip()}")
    else:
        parts.append(
            "## 当前状态文档\n\n"
            "（无历史状态记录。这是系统首次运行，请基于今日新闻建立初始状态文档。）"
        )

    # --- Section 2: delta articles grouped by stream ---
    stream_headers = {
        "A": "数据流 A — 以色列内部危机",
        "B": "数据流 B — 伊朗内部危机",
        "C": "数据流 C — 军事异动（参照组）",
    }

    article_sections: list[str] = []
    for stream in ("A", "B", "C"):
        stream_articles = [a for a in articles if a.stream == stream]
        if not stream_articles:
            continue
        lines = [f"### {stream_headers[stream]}"]
        for a in stream_articles:
            lines.append(
                f"\n[{a.stream}] {a.source} | "
                f"{a.published_at.strftime('%Y-%m-%d %H:%M')} UTC\n"
                f"标题: {a.title}\n"
                f"摘要: {a.summary}"
            )
        article_sections.append("\n".join(lines))

    total = len(articles)
    parts.append(f"## 今日增量新闻 ({total} 条)\n\n" + "\n\n".join(article_sections))

    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# Core analysis call — how the main function puts the pipeline to the agent
# ---------------------------------------------------------------------------

def run_analysis(articles: list[Article]) -> ResonanceReport:
    """
    Hand the pipeline output to Claude.

    Claude's job (enforced via tool_choice):
      - Read the state document and today's delta
      - Score Israel/Iran pressure using Diversionary War Theory logic
      - Produce a ResonanceReport
      - Write an updated state.md for tomorrow

    Returns a validated ResonanceReport.
    Raises on Claude API error or missing tool call in response.
    """
    state = load_state()

    # Auto-bootstrap if no state exists
    if state is None:
        logger.info("No state.md found — running bootstrap first …")
        from bootstrap import run_bootstrap
        run_bootstrap()
        state = load_state()
        if state is None:
            raise RuntimeError("Bootstrap completed but state.md still missing.")

    user_message = build_user_message(state, articles)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4096,
        system=load_daily_system_prompt(),        # war theory + output instructions
        tools=[SUBMIT_ANALYSIS_TOOL],            # enforces schema via tool use
        tool_choice={"type": "tool", "name": "submit_analysis"},  # must call exactly this
        messages=[{"role": "user", "content": user_message}],
    )

    # Extract the tool call — tool_choice guarantees exactly one
    report_input: dict | None = None
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_analysis":
            report_input = block.input
            break

    if report_input is None:
        raise ValueError(f"Claude did not return a submit_analysis tool call. stop_reason={response.stop_reason}")

    # Persist the new state before returning (side-effect)
    new_state = report_input.pop("updated_state_document")
    save_state(new_state)
    logger.info("state.md updated (%d chars)", len(new_state))

    report = ResonanceReport(
        **report_input,
        analysis_date=date.today().isoformat(),
        articles_analyzed=len(articles),
    )

    save_report(report)
    logger.info(
        "Analysis complete: IL=%d/10  IR=%d/10  CRI=%.0f%%",
        report.israel_pressure_score,
        report.iran_pressure_score,
        report.crisis_resonance_index * 100,
    )

    return report


# ---------------------------------------------------------------------------
# CLI / GitHub Actions entry point
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # Step 0: snapshot Polymarket odds
    logger.info("=== Phase 0: fetching Polymarket odds ===")
    try:
        fetch_polymarket()
    except Exception as exc:
        logger.warning("Polymarket fetch failed (non-fatal): %s", exc)

    # Step 1: run the data pipeline
    logger.info("=== Phase 1: fetching delta articles ===")
    articles = run_pipeline()
    save_articles(articles)

    if not articles:
        logger.warning("No articles passed the filter today — analysis will run on empty delta.")

    # Step 2: hand pipeline output to the agent
    logger.info("=== Phase 2: running AI analysis ===")
    report = run_analysis(articles)

    # Step 3: return report path for notifier (Phase 3 reads latest_report.json)
    logger.info("Report saved to %s", REPORT_PATH)
    return report


if __name__ == "__main__":
    main()
