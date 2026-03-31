"""
Bootstrap Pass (ADR-002 / ADR-003) — Cold-start state document generator.

Runs once when data/state.md does not exist (or with --force to regenerate).
Uses Perplexity API for 5 scripted geopolitical queries (concurrent), then
hands all results to Claude which synthesizes an initial state.md via the
submit_analysis tool.

Why Perplexity instead of a free agentic web_search loop:
  - The 5 research directions are known upfront — no need for Claude to decide
    what to search.
  - Perplexity's synthesis quality is higher for "current state of X" queries.
  - Fewer round-trips: 5 parallel Perplexity calls + 1 Claude call vs. 5-15
    agentic tool calls.

Run standalone:
    python src/bootstrap.py           # skips if state.md already exists
    python src/bootstrap.py --force   # overwrites existing state.md
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import anthropic
import requests

sys.path.insert(0, str(Path(__file__).parent))
from prompts import load_bootstrap_system_prompt, SUBMIT_ANALYSIS_TOOL

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parents[1]
STATE_PATH = ROOT / "data" / "state.md"

# ---------------------------------------------------------------------------
# The 5 scripted Perplexity queries — one per research direction
# English queries consistently yield better Perplexity results.
# Labels are used to section the context fed to Claude.
# ---------------------------------------------------------------------------

PERPLEXITY_QUERIES: list[tuple[str, str]] = [
    (
        "以色列内政危机",
        "Netanyahu corruption trial current status, Ben-Gvir Smotrich coalition stability, "
        "Israel domestic protests scale 2025",
    ),
    (
        "伊朗政权稳定性",
        "Iranian Rial exchange rate trend, inflation rate, IRGC internal dynamics, "
        "Khamenei health and succession 2025",
    ),
    (
        "军事与外交信号",
        "Israel Iran US military incidents, diplomatic signals, sanctions developments "
        "last 90 days 2025",
    ),
    (
        "伊朗核谈判",
        "Iran nuclear deal JCPOA negotiations status, uranium enrichment level, "
        "IAEA latest report 2025",
    ),
    (
        "地区代理人网络",
        "Hezbollah Houthi Iraqi Shia militia proxy network activity last 30 days 2025",
    ),
]

PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
PERPLEXITY_MODEL = "sonar"

# ---------------------------------------------------------------------------
# State persistence (duplicated from analyzer.py to avoid circular import)
# ---------------------------------------------------------------------------

def save_state(content: str) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(content, encoding="utf-8")

# ---------------------------------------------------------------------------
# Perplexity query
# ---------------------------------------------------------------------------

def _query_perplexity(label: str, query: str, api_key: str) -> tuple[str, str]:
    """Run a single Perplexity query. Returns (label, answer_text)."""
    resp = requests.post(
        PERPLEXITY_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": PERPLEXITY_MODEL,
            "messages": [{"role": "user", "content": query}],
        },
        timeout=30,
    )
    resp.raise_for_status()
    answer = resp.json()["choices"][0]["message"]["content"]
    logger.info("Perplexity [%s]: %d chars", label, len(answer))
    return label, answer


def fetch_all_perplexity(api_key: str) -> dict[str, str]:
    """Run all 5 queries concurrently. Returns {label: answer}."""
    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            pool.submit(_query_perplexity, label, query, api_key): label
            for label, query in PERPLEXITY_QUERIES
        }
        for future in as_completed(futures):
            label = futures[future]
            try:
                _, answer = future.result()
                results[label] = answer
            except Exception as exc:
                logger.warning("Perplexity query failed [%s]: %s", label, exc)
                results[label] = f"（查询失败：{exc}）"
    return results

# ---------------------------------------------------------------------------
# User message builder
# ---------------------------------------------------------------------------

def build_bootstrap_message(research: dict[str, str]) -> str:
    """
    Format Perplexity research results into the user message Claude receives.
    Claude's job: synthesize all findings into an initial state.md via submit_analysis.
    """
    today = date.today().isoformat()
    sections = [
        f"## 调研背景\n\n"
        f"以下是通过 Perplexity 检索得到的5个方向的最新情报，检索日期：{today}。\n"
        f"请综合所有信息，生成一份初始状态文档（updated_state_document 字段），"
        f"其他字段（压力评分、CRI、摘要、关键信号）也请基于此次调研给出初始估计值。"
    ]
    for label, answer in research.items():
        sections.append(f"### {label}\n\n{answer.strip()}")
    return "\n\n---\n\n".join(sections)

# ---------------------------------------------------------------------------
# Core bootstrap run
# ---------------------------------------------------------------------------

def run_bootstrap() -> None:
    """
    Execute the full bootstrap pass:
      1. Fetch 5 Perplexity queries concurrently
      2. Call Claude to synthesize → submit_analysis tool call
      3. Save updated_state_document to data/state.md
    """
    perplexity_key = os.environ.get("PERPLEXITY_API_KEY")
    if not perplexity_key:
        raise EnvironmentError("PERPLEXITY_API_KEY is not set")

    logger.info("=== Bootstrap: fetching Perplexity research (5 queries) ===")
    research = fetch_all_perplexity(perplexity_key)

    user_message = build_bootstrap_message(research)

    logger.info("=== Bootstrap: calling Claude for synthesis ===")
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4096,
        system=load_bootstrap_system_prompt(),
        tools=[SUBMIT_ANALYSIS_TOOL],
        tool_choice={"type": "tool", "name": "submit_analysis"},
        messages=[{"role": "user", "content": user_message}],
    )

    state_doc: str | None = None
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_analysis":
            state_doc = block.input.get("updated_state_document")
            break

    if not state_doc:
        raise ValueError(
            f"Claude did not return a submit_analysis tool call. "
            f"stop_reason={response.stop_reason}"
        )

    save_state(state_doc)
    logger.info("Bootstrap complete — state.md written (%d chars)", len(state_doc))

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Bootstrap initial state.md via Perplexity + Claude")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing state.md (default: skip if it already exists)",
    )
    args = parser.parse_args()

    if STATE_PATH.exists() and not args.force:
        logger.info("state.md already exists — skipping bootstrap. Use --force to regenerate.")
        return

    run_bootstrap()
    print(f"state.md written to {STATE_PATH}")


if __name__ == "__main__":
    main()
