"""
Bootstrap Pass (ADR-002 / ADR-003) — Cold-start state document generator.

Runs once when data/state.md does not exist (or with --force to regenerate).

Data sourcing strategy — all 5 directions via Tavily search API:
  Directions 1 & 2 are domain-pinned to ISW / ICG for authoritative expert
  analysis. Directions 3-5 are open-web searches for gaps those sources don't
  cover (Israeli coalition politics, Iranian economy, nuclear/IAEA status).

  1. 军事与代理人网络  — domain:understandingwar.org  (ISW Iran Updates)
  2. 地区冲突动态     — domain:crisisgroup.org        (ICG Middle East alerts)
  3. 以色列内政危机   — open web (Netanyahu trial, Ben-Gvir, coalition)
  4. 伊朗经济压力     — open web (Rial, inflation, IRGC economy)
  5. 伊朗核谈判       — open web (JCPOA, IAEA, uranium enrichment)

Why Tavily instead of direct scraping + Perplexity:
  - ISW/ICG block naive requests via Cloudflare; Tavily bypasses this cleanly.
  - Tavily returns full extracted article text, not just RSS snippets.
  - One API, one code pattern — simpler than feedparser + Perplexity mixed stack.
  - Domain-pinning (include_domains) guarantees ISW/ICG sourcing for tier-1
    directions without relying on search ranking luck.
  - Cost: ~$0.02-0.05 total per bootstrap run (runs rarely).

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
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))
from prompts import load_bootstrap_system_prompt, SUBMIT_ANALYSIS_TOOL

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parents[1]
STATE_PATH = ROOT / "data" / "state.md"

# ---------------------------------------------------------------------------
# Tavily query definitions — one per research direction.
#
# Each entry is a dict with:
#   label          — section heading in the Claude user message (Chinese)
#   query          — English search query (Tavily performs better in English)
#   include_domains — optional list; pins results to authoritative sources
#   max_results    — how many articles to pull per direction
# ---------------------------------------------------------------------------

TAVILY_QUERIES: list[dict] = [
    {
        "label": "军事异动与代理人网络 (ISW)",
        "query": (
            "Iran Iraq Syria Hezbollah Houthi militia military activity IRGC "
            "US CENTCOM strikes latest update"
        ),
        "include_domains": ["understandingwar.org"],
        "max_results": 5,
    },
    {
        "label": "地区冲突动态 (ICG)",
        "query": (
            "Israel Iran Middle East conflict crisis analysis latest"
        ),
        "include_domains": ["crisisgroup.org"],
        "max_results": 4,
    },
    {
        "label": "以色列内政危机",
        "query": (
            "Netanyahu corruption trial verdict Ben-Gvir Smotrich coalition "
            "collapse Israel domestic protest 2025"
        ),
        "include_domains": [],
        "max_results": 4,
    },
    {
        "label": "伊朗经济与政权稳定性",
        "query": (
            "Iranian Rial exchange rate inflation unemployment IRGC economy "
            "Khamenei succession protest 2025"
        ),
        "include_domains": [],
        "max_results": 4,
    },
    {
        "label": "伊朗核谈判与IAEA",
        "query": (
            "Iran JCPOA nuclear deal negotiations uranium enrichment 60 percent "
            "IAEA report 2025"
        ),
        "include_domains": [],
        "max_results": 4,
    },
]

TAVILY_URL = "https://api.tavily.com/search"

# ---------------------------------------------------------------------------
# State persistence (duplicated from analyzer.py to avoid circular import)
# ---------------------------------------------------------------------------

def save_state(content: str) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(content, encoding="utf-8")

# ---------------------------------------------------------------------------
# Tavily query
# ---------------------------------------------------------------------------

def _query_tavily(entry: dict, api_key: str) -> tuple[str, str]:
    """
    Run a single Tavily search. Returns (label, formatted_text).

    Each result is formatted as:
        [Source Title](url)
        <content excerpt>
    so Claude sees both provenance and substance.
    """
    payload: dict = {
        "api_key": api_key,
        "query": entry["query"],
        "search_depth": "advanced",
        "max_results": entry["max_results"],
        "include_answer": False,
        "include_raw_content": False,
    }
    if entry["include_domains"]:
        payload["include_domains"] = entry["include_domains"]

    resp = requests.post(TAVILY_URL, json=payload, timeout=30)
    resp.raise_for_status()

    results = resp.json().get("results", [])
    if not results:
        return entry["label"], "（无搜索结果）"

    parts: list[str] = []
    for r in results:
        title = r.get("title", "Untitled")
        url = r.get("url", "")
        content = r.get("content", "").strip()
        parts.append(f"**[{title}]({url})**\n{content}")

    text = "\n\n".join(parts)
    logger.info("Tavily [%s]: %d results, %d chars", entry["label"], len(results), len(text))
    return entry["label"], text


def fetch_all_tavily(api_key: str) -> dict[str, str]:
    """Run all queries concurrently. Returns {label: formatted_text}."""
    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            pool.submit(_query_tavily, entry, api_key): entry["label"]
            for entry in TAVILY_QUERIES
        }
        for future in as_completed(futures):
            label = futures[future]
            try:
                _, text = future.result()
                results[label] = text
            except Exception as exc:
                logger.warning("Tavily query failed [%s]: %s", label, exc)
                results[label] = f"（查询失败：{exc}）"
    return results

# ---------------------------------------------------------------------------
# User message builder
# ---------------------------------------------------------------------------

def build_bootstrap_message(research: dict[str, str]) -> str:
    """
    Format Tavily research into the user message Claude receives.
    Directions 1-2 are clearly labelled as ISW/ICG so Claude weights them
    appropriately as expert primary sources vs. general web results.
    """
    today = date.today().isoformat()
    sections = [
        f"## 调研背景\n\n"
        f"以下是通过 Tavily 检索得到的5个方向最新情报，检索日期：{today}。\n"
        f"前两个方向（ISW、ICG）为权威智库一手分析；后三个方向为开放网络检索。\n"
        f"请综合所有信息，生成初始状态文档（updated_state_document 字段），"
        f"并给出压力评分、CRI、摘要和关键信号的初始估计值。"
    ]
    # Preserve insertion order so ISW/ICG always appear first in context
    for label in [e["label"] for e in TAVILY_QUERIES]:
        answer = research.get(label, "（无数据）")
        sections.append(f"### {label}\n\n{answer.strip()}")
    return "\n\n---\n\n".join(sections)

# ---------------------------------------------------------------------------
# Core bootstrap run
# ---------------------------------------------------------------------------

def run_bootstrap() -> None:
    """
    Execute the full bootstrap pass:
      1. Fetch 5 Tavily queries concurrently (ISW/ICG domain-pinned + open web)
      2. Call Claude to synthesize → submit_analysis tool call
      3. Save updated_state_document to data/state.md
    """
    tavily_key = os.environ.get("TAVILY_API_KEY")
    if not tavily_key:
        raise EnvironmentError("TAVILY_API_KEY is not set")

    logger.info("=== Bootstrap: fetching research via Tavily (5 queries) ===")
    research = fetch_all_tavily(tavily_key)

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

    parser = argparse.ArgumentParser(
        description="Bootstrap initial state.md via Tavily (ISW/ICG + open web) + Claude"
    )
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
