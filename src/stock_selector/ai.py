from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from .fundamentals import StockInsight, summarize_without_ai
from .normalization import local_symbol


DEFAULT_MODEL = "gpt-4.1-mini"


@dataclass(frozen=True)
class ParsedQuery:
    task: str
    reference_symbol: str | None = None
    window: str = "1m"
    filters: dict[str, object] | None = None
    explanation: str = ""
    ai_enabled: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "task": self.task,
            "reference_symbol": self.reference_symbol,
            "window": self.window,
            "filters": self.filters or {},
            "explanation": self.explanation,
            "ai_enabled": self.ai_enabled,
        }


def parse_natural_query(query: str) -> ParsedQuery:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return _fallback_parse(query)
    try:
        payload = _responses_request(
            api_key=api_key,
            instructions=(
                "你是A股量化系统的查询解析器。只把用户输入解析成JSON，不输出投资建议。"
                "task只能是similar_kline、screen、stock_insight、unknown。"
                "window只能是1m、half_year、1y。reference_symbol如能识别股票代码则填6位代码。"
            ),
            text=query,
            schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["task", "reference_symbol", "window", "filters", "explanation"],
                "properties": {
                    "task": {"type": "string", "enum": ["similar_kline", "screen", "stock_insight", "unknown"]},
                    "reference_symbol": {"type": "string"},
                    "window": {"type": "string", "enum": ["1m", "half_year", "1y"]},
                    "filters": {"type": "string"},
                    "explanation": {"type": "string"},
                },
            },
        )
    except Exception:
        return _fallback_parse(query)
    return ParsedQuery(
        task=str(payload.get("task") or "unknown"),
        reference_symbol=_safe_symbol(payload.get("reference_symbol")),
        window=str(payload.get("window") or "1m"),
        filters={"text": str(payload.get("filters") or "")},
        explanation=str(payload.get("explanation") or ""),
        ai_enabled=True,
    )


def summarize_stock_insight(insight: StockInsight) -> dict[str, object]:
    api_key = os.environ.get("OPENAI_API_KEY")
    fallback = summarize_without_ai(insight)
    if not api_key:
        return {"ai_enabled": False, "summary": fallback}
    source = insight.to_dict()
    try:
        payload = _responses_request(
            api_key=api_key,
            instructions=(
                "你是A股研究助手。只能基于输入JSON里的新闻、公告、财务、估值、主营业务总结；"
                "不要编造不存在的信息；必须提示这不是投资建议。"
            ),
            text=json.dumps(source, ensure_ascii=False, default=str),
            schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["summary", "strengths", "risks", "watch_items"],
                "properties": {
                    "summary": {"type": "string"},
                    "strengths": {"type": "array", "items": {"type": "string"}},
                    "risks": {"type": "array", "items": {"type": "string"}},
                    "watch_items": {"type": "array", "items": {"type": "string"}},
                },
            },
        )
    except Exception:
        return {"ai_enabled": False, "summary": fallback}
    payload["ai_enabled"] = True
    return payload


def _responses_request(api_key: str, instructions: str, text: str, schema: dict[str, object]) -> dict[str, object]:
    body = {
        "model": os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
        "input": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": text},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "stock_selector_output",
                "strict": True,
                "schema": schema,
            }
        },
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(exc.read().decode("utf-8", errors="ignore")) from exc
    text_output = _extract_output_text(data)
    return json.loads(text_output)


def _extract_output_text(response: dict[str, object]) -> str:
    if response.get("output_text"):
        return str(response["output_text"])
    for item in response.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) or []:
            if isinstance(content, dict) and content.get("text"):
                return str(content["text"])
    raise RuntimeError("OpenAI response did not contain output text.")


def _fallback_parse(query: str) -> ParsedQuery:
    text = query.lower()
    task = "similar_kline" if any(token in text for token in ("相似", "走势", "形态", "k线", "k 线")) else "unknown"
    window = "1m"
    if "半年" in text or "6个月" in text or "六个月" in text:
        window = "half_year"
    elif "一年" in text or "1年" in text:
        window = "1y"
    symbol = None
    match = re.search(r"\d{6}", query)
    if match:
        symbol = match.group(0)
    return ParsedQuery(
        task=task,
        reference_symbol=symbol,
        window=window,
        filters={},
        explanation="未配置OPENAI_API_KEY，使用本地规则解析。",
        ai_enabled=False,
    )


def _safe_symbol(value: object) -> str | None:
    if value is None:
        return None
    try:
        return local_symbol(value)
    except ValueError:
        return None
