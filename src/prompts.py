"""
Prompt loaders and tool schemas shared by analyzer.py and bootstrap.py.

System prompts are assembled at call time by reading from knowledges/*.md,
so analysts can iterate on the analytical framework without touching Python.
The tool schema (SUBMIT_ANALYSIS_TOOL) stays here as it is pure code structure.
"""

from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).parents[1] / "knowledges"


def _read(filename: str) -> str:
    return (KNOWLEDGE_DIR / filename).read_text(encoding="utf-8").strip()


def load_daily_system_prompt() -> str:
    """Assemble the system prompt used in every daily analysis run."""
    # circuit_breaker_protocol has highest priority — prepended before war theory
    return (
        _read("circuit_breaker_protocol.md")
        + "\n\n"
        + _read("diversionary_war_theory.md")
        + "\n\n"
        + _read("analysis_instructions.md")
    )


def load_bootstrap_system_prompt() -> str:
    """Assemble the system prompt for the cold-start bootstrap agent."""
    return load_daily_system_prompt() + "\n\n" + _read("bootstrap_agenda.md")


# ---------------------------------------------------------------------------
# Tool schema for structured output
# Claude MUST call this with tool_choice={"type": "tool", "name": "submit_analysis"}
# ---------------------------------------------------------------------------

SUBMIT_ANALYSIS_TOOL = {
    "name": "submit_analysis",
    "description": (
        "提交今日分析结果，包含压力评分、冲突共振指数、执行摘要，以及更新后供明日使用的状态文档。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "israel_pressure_score": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "以色列内部政治压力指数（1=极低，10=极高生存危机）",
            },
            "israel_pressure_reason": {
                "type": "string",
                "description": "以色列压力评分的简短理由（1-2句，中文）",
            },
            "iran_pressure_score": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "伊朗内部经济/政治压力指数（1=稳定，10=濒临崩溃）",
            },
            "iran_pressure_reason": {
                "type": "string",
                "description": "伊朗压力评分的简短理由（1-2句，中文）",
            },
            "crisis_resonance_index": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "冲突共振指数，0.0-1.0（0.75 表示 75%）",
            },
            "executive_summary": {
                "type": "string",
                "description": "300字以内的中文深度预测简报",
            },
            "top_signals": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 3,
                "description": "驱动本次评分的关键新闻标题（最多3条）",
            },
            "updated_state_document": {
                "type": "string",
                "description": "完整的更新后状态文档（Markdown格式，供明日分析使用，≤1000字）",
            },
        },
        "required": [
            "israel_pressure_score",
            "israel_pressure_reason",
            "iran_pressure_score",
            "iran_pressure_reason",
            "crisis_resonance_index",
            "executive_summary",
            "top_signals",
            "updated_state_document",
        ],
    },
}
