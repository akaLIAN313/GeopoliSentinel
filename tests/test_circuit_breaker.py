"""
Circuit breaker integration test.

Sends a mock "leadership decapitation" article to Claude and asserts that:
  1. crisis_resonance_index == 1.0
  2. executive_summary contains "系统性重构已触发"
  3. The annotation [CIRCUIT BREAKER ACTIVE] appears somewhere in the response fields

Run:
    uv run python tests/test_circuit_breaker.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

import anthropic
from models import Article
from prompts import load_daily_system_prompt, SUBMIT_ANALYSIS_TOOL
from analyzer import build_user_message

# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------

MOCK_STATE = """\
## 状态文档 — 截至 2026-04-01

### 以色列压力态势
- 当前压力分数: 7/10（趋势: → 持平）
- 核心驱动事件（持续追踪中）:
  - 内塔尼亚胡腐败审判: 进行中，政治压力持续

### 伊朗压力态势
- 当前压力分数: 6/10（趋势: → 持平）
- 核心驱动事件:
  - 里亚尔持续贬值，通货膨胀率高企

### 地区代理人网络态势
- 真主党: 战斗力部分恢复中
- 胡塞武装: 红海袭击维持低频
- 伊拉克什叶派民兵: 活动平静
- 网络整体评估: 协调能力受损，仍可局部动员

### 美国军事与外交态势
- CENTCOM部署: 一艘航母战斗群在波斯湾
- 外交立场: 支持以色列，推动加沙停火
- 对伊朗信号: 维持制裁，核谈判僵持

### 冲突共振指数 (CRI) 近期走势
- 2026-03-26: 52%
- 2026-03-27: 55%
- 2026-03-28: 53%
- 2026-03-29: 57%
- 2026-03-30: 58%
- 2026-03-31: 60%
- 2026-04-01: 61%

### 待观察信号
- 伊朗核设施附近异常车辆活动: 首次出现于 2026-03-30，尚无后续发展
"""

# Trigger condition 1: Leadership Decapitation
MOCK_ARTICLES = [
    Article(
        id="deadbeef0001",
        title="BREAKING: Israeli Prime Minister Netanyahu pronounced dead following assassination attempt in Jerusalem",
        summary=(
            "Benjamin Netanyahu was assassinated in Jerusalem earlier today. "
            "Gunfire erupted during a public appearance; he was rushed to Hadassah Medical Center "
            "but could not be saved. The Israeli Defense Cabinet has convened an emergency session. "
            "National elections are expected to be called within 90 days under Israeli law."
        ),
        source="Reuters (MOCK — test only)",
        stream="A",
        published_at=datetime(2026, 4, 2, 8, 0, 0, tzinfo=timezone.utc),
        url="https://example.com/mock",
    ),
]

# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run_circuit_breaker_test() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY not set")

    print("Building user message with leadership-decapitation trigger article …")
    user_message = build_user_message(MOCK_STATE, MOCK_ARTICLES)

    client = anthropic.Anthropic(api_key=api_key)

    print("Calling Claude (this uses real API credits) …")
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4096,
        system=load_daily_system_prompt(),
        tools=[SUBMIT_ANALYSIS_TOOL],
        tool_choice={"type": "tool", "name": "submit_analysis"},
        messages=[{"role": "user", "content": user_message}],
    )

    report_input: dict | None = None
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_analysis":
            report_input = block.input
            break

    if report_input is None:
        raise AssertionError(f"Claude did not return submit_analysis. stop_reason={response.stop_reason}")

    cri = report_input["crisis_resonance_index"]
    summary = report_input["executive_summary"]

    print(f"\n--- Results ---")
    print(f"CRI:               {cri:.2f}")
    print(f"IL pressure score: {report_input['israel_pressure_score']}")
    print(f"IR pressure score: {report_input['iran_pressure_score']}")
    print(f"Executive summary:\n{summary}\n")

    # --- Assertions ---
    errors: list[str] = []

    if cri != 1.0:
        errors.append(f"FAIL: crisis_resonance_index expected 1.0, got {cri}")
    else:
        print("PASS: crisis_resonance_index == 1.0")

    if "系统性重构已触发" not in summary:
        errors.append(f"FAIL: executive_summary missing '系统性重构已触发'")
    else:
        print("PASS: executive_summary contains '系统性重构已触发'")

    full_response_text = str(report_input)
    if "[CIRCUIT BREAKER ACTIVE]" not in full_response_text:
        errors.append("FAIL: [CIRCUIT BREAKER ACTIVE] annotation missing from response")
    else:
        print("PASS: [CIRCUIT BREAKER ACTIVE] annotation present")

    if errors:
        print("\n" + "\n".join(errors))
        sys.exit(1)
    else:
        print("\nAll circuit breaker assertions passed.")


if __name__ == "__main__":
    run_circuit_breaker_test()
