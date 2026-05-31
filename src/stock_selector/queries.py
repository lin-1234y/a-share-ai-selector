from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .config import DEFAULT_EXPORT_DIR
from .normalization import is_st_name
from .storage import StockDatabase


@dataclass(frozen=True)
class AskSpec:
    high_drawdown: float | None = None
    near_week_ma: int | None = None
    period_return_window: int | None = None
    period_return_threshold: float | None = None
    near_pct: float = 0.05
    exclude_st: bool = True
    exclude_losing: bool = True


def parse_ask(query: str) -> AskSpec:
    text = query.lower().replace("％", "%")
    percent = _first_percent(text)
    high_drawdown = None
    near_week_ma = None
    period_return_window = None
    period_return_threshold = None

    if "历史" in text and ("高" in text or "新高" in text):
        high_drawdown = percent if percent is not None else 0.30
    if "60周" in text or "60 周" in text:
        near_week_ma = 60
    if _asks_for_monthly_drop(text):
        period_return_window = 20
        period_return_threshold = -(percent if percent is not None else 0.30)

    return AskSpec(
        high_drawdown=high_drawdown,
        near_week_ma=near_week_ma,
        period_return_window=period_return_window,
        period_return_threshold=period_return_threshold,
        near_pct=0.05,
        exclude_st=("包含st" not in text and "包含 st" not in text),
        exclude_losing=("亏损" in text or "盈利" in text or "非亏损" in text),
    )


def run_ask(db: StockDatabase, query: str, run_date: str, top: int, export_dir: Path = DEFAULT_EXPORT_DIR) -> Path:
    spec = parse_ask(query)
    results = evaluate_spec(db, spec, run_date).head(top)
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / f"ask_{run_date}_{_safe_name(query)}.csv"
    results.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def evaluate_spec(db: StockDatabase, spec: AskSpec, run_date: str) -> pd.DataFrame:
    stocks = db.read_table("stocks")
    quotes = db.read_table("daily_quotes", "trade_date <= ?", (run_date,))
    finance = db.read_table("financial_indicators", "report_date <= ?", (run_date,))
    valuations = db.read_table("valuations", "trade_date <= ?", (run_date,))
    if stocks.empty or quotes.empty:
        return _empty_query_results()

    latest = _latest_quotes(quotes)
    features = _quote_features(quotes, spec)
    latest_finance = _latest_by_symbol(finance, "report_date")
    latest_valuations = _latest_by_symbol(valuations, "trade_date")

    data = stocks.merge(latest, on="symbol", how="inner", suffixes=("", "_quote"))
    data = data.merge(features, on="symbol", how="left")
    data = data.merge(latest_finance, on="symbol", how="left", suffixes=("", "_finance"))
    data = data.merge(latest_valuations, on="symbol", how="left", suffixes=("", "_valuation"))
    data["reason"] = data.apply(lambda row: _why_match(row, spec), axis=1)
    data = data[data["reason"].notna()].copy()
    if data.empty:
        return _empty_query_results()

    data["score"] = _query_score(data, spec)
    data = data.sort_values(["score", "symbol"], ascending=[False, True]).reset_index(drop=True)
    data["rank"] = data.index + 1
    columns = [
        "rank",
        "symbol",
        "name",
        "close",
        "historical_high",
        "drawdown_from_high",
        "weekly_ma60",
        "distance_to_weekly_ma60",
        "eps",
        "net_profit_margin",
        "pe_ttm",
        "pb",
        "period_start_close",
        "period_return",
        "latest_trade_date",
        "score",
        "reason",
    ]
    for column in columns:
        if column not in data:
            data[column] = None
    return data[columns]


def _latest_quotes(quotes: pd.DataFrame) -> pd.DataFrame:
    return _latest_by_symbol(quotes, "trade_date")


def _latest_by_symbol(df: pd.DataFrame, date_column: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["symbol"])
    rows = df.copy().sort_values(["symbol", date_column])
    return rows.groupby("symbol", as_index=False).tail(1)


def _quote_features(quotes: pd.DataFrame, spec: AskSpec) -> pd.DataFrame:
    rows = []
    for symbol, group in quotes.sort_values("trade_date").groupby("symbol"):
        group_rows = group.sort_values("trade_date").copy()
        closes = pd.to_numeric(group_rows["close"], errors="coerce")
        close = closes.iloc[-1] if not closes.empty else None
        high = closes.max() if not closes.empty else None
        row = {
            "symbol": symbol,
            "historical_high": high,
            "drawdown_from_high": (close / high - 1) if high and close else None,
            "weekly_ma60": None,
            "distance_to_weekly_ma60": None,
            "period_start_close": None,
            "period_return": None,
            "latest_trade_date": group_rows["trade_date"].iloc[-1] if not group_rows.empty else None,
        }
        if spec.period_return_window:
            clean_closes = closes.dropna()
            if len(clean_closes) > spec.period_return_window:
                base = clean_closes.iloc[-spec.period_return_window - 1]
                if base:
                    row["period_start_close"] = base
                    row["period_return"] = clean_closes.iloc[-1] / base - 1
        if spec.near_week_ma:
            weekly = _weekly_closes(group)
            if len(weekly) >= spec.near_week_ma:
                ma = weekly.rolling(spec.near_week_ma).mean().iloc[-1]
                row["weekly_ma60"] = ma
                row["distance_to_weekly_ma60"] = close / ma - 1 if ma and close else None
        rows.append(row)
    return pd.DataFrame(rows)


def _weekly_closes(group: pd.DataFrame) -> pd.Series:
    rows = group.copy()
    rows["date"] = pd.to_datetime(rows["trade_date"], format="%Y%m%d", errors="coerce")
    rows["close"] = pd.to_numeric(rows["close"], errors="coerce")
    rows = rows.dropna(subset=["date", "close"]).sort_values("date")
    if rows.empty:
        return pd.Series(dtype=float)
    return rows.set_index("date")["close"].resample("W-FRI").last().dropna()


def _why_match(row: pd.Series, spec: AskSpec) -> str | None:
    reasons = []
    if spec.exclude_st and (int(row.get("is_st") or 0) == 1 or is_st_name(row.get("name"))):
        return None
    if spec.exclude_losing and not _is_profitable(row):
        return None
    if spec.high_drawdown is not None:
        drawdown = row.get("drawdown_from_high")
        if pd.isna(drawdown) or drawdown > -spec.high_drawdown:
            return None
        reasons.append(f"较历史高点回撤{abs(drawdown):.1%}")
    if spec.near_week_ma is not None:
        distance = row.get("distance_to_weekly_ma60")
        if pd.isna(distance) or abs(float(distance)) > spec.near_pct:
            return None
        reasons.append(f"距离60周线{distance:.1%}")
    if spec.period_return_window is not None and spec.period_return_threshold is not None:
        period_return = row.get("period_return")
        if pd.isna(period_return) or float(period_return) > spec.period_return_threshold:
            return None
        reasons.append(f"近{spec.period_return_window}日下跌{abs(float(period_return)):.1%}")
    if not reasons:
        return None
    return "；".join(reasons)


def _is_profitable(row: pd.Series) -> bool:
    eps = row.get("eps")
    margin = row.get("net_profit_margin")
    if pd.notna(eps) and float(eps) > 0:
        return True
    if pd.notna(margin) and float(margin) > 0:
        return True
    return False


def _query_score(df: pd.DataFrame, spec: AskSpec) -> pd.Series:
    score = pd.Series(0.0, index=df.index)
    if spec.high_drawdown is not None:
        score += (-pd.to_numeric(df["drawdown_from_high"], errors="coerce")).fillna(0) * 100
    if spec.near_week_ma is not None:
        distance = pd.to_numeric(df["distance_to_weekly_ma60"], errors="coerce").abs()
        score += (1 - distance / spec.near_pct).clip(lower=0).fillna(0) * 100
    if spec.period_return_window is not None:
        score += (-pd.to_numeric(df["period_return"], errors="coerce")).clip(lower=0).fillna(0) * 100
    eps = df["eps"] if "eps" in df else pd.Series(index=df.index, dtype=float)
    score += pd.to_numeric(eps, errors="coerce").clip(lower=0).fillna(0)
    return score


def _asks_for_monthly_drop(text: str) -> bool:
    has_period = any(token in text for token in ("一个月", "近一个月", "最近一个月", "近20日", "近 20 日", "20日", "20 日"))
    has_drop = "跌" in text or "下跌" in text or "跌幅" in text
    return has_period and has_drop


def _first_percent(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if match:
        return float(match.group(1)) / 100
    match = re.search(r"百分(?:之)?\s*(\d+(?:\.\d+)?)", text)
    if match:
        return float(match.group(1)) / 100
    chinese = {"百分三十": 0.30, "百分之三十": 0.30, "三十": 0.30}
    for key, value in chinese.items():
        if key in text:
            return value
    return None


def _safe_name(query: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "_", query).strip("_")
    return text[:40] or "query"


def _empty_query_results() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "rank",
            "symbol",
            "name",
            "close",
            "historical_high",
            "drawdown_from_high",
            "weekly_ma60",
            "distance_to_weekly_ma60",
            "eps",
            "net_profit_margin",
            "pe_ttm",
            "pb",
            "period_start_close",
            "period_return",
            "latest_trade_date",
            "score",
            "reason",
        ]
    )
