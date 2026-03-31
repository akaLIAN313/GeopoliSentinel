from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Article(BaseModel):
    id: str  # 12-char SHA256 prefix — used for dedup
    title: str
    summary: str  # truncated to 500 chars at ingestion
    source: str
    stream: Literal["A", "B", "C"]
    published_at: datetime  # always UTC
    url: str


class ResonanceReport(BaseModel):
    israel_pressure_score: int = Field(ge=1, le=10)
    israel_pressure_reason: str
    iran_pressure_score: int = Field(ge=1, le=10)
    iran_pressure_reason: str
    crisis_resonance_index: float = Field(ge=0.0, le=1.0)  # 0.75 = 75%
    executive_summary: str  # ≤300 Chinese chars
    top_signals: list[str] = Field(max_length=3)
    analysis_date: str  # ISO date string, set by code not LLM
    articles_analyzed: int  # set by code not LLM
