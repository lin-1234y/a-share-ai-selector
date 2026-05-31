from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from .config import ScreenConfig
from .storage import StockDatabase


@dataclass(frozen=True)
class ScreenOutput:
    included: pd.DataFrame
    excluded: pd.DataFrame


def screen_stocks(db: StockDatabase, run_date: str, config: ScreenConfig | None = None) -> ScreenOutput:
    cfg = config or ScreenConfig()
    stocks = db.read_table("stocks")
    quotes = db.read_table("daily_quotes", "trade_date <= ?", (run_date,))
    finance = db.read_table("financial_indicators", "report_date <= ?", (run_date,))
    valuations = db.read_table("valuations", "trade_date <= ?", (run_date,))

    if stocks.empty:
        return ScreenOutput(_empty_results(), _empty_results())

    base = stocks.copy()
    latest_quotes = _latest_by_symbol(quotes, "trade_date")
    latest_finance = _latest_by_symbol(finance, "report_date")
    latest_valuations = _latest_by_symbol(valuations, "trade_date")
    momentum = _momentum_features(quotes)

    data = base.merge(latest_quotes, on="symbol", how="left", suffixes=("", "_quote"))
    data = data.merge(latest_finance, on="symbol", how="left", suffixes=("", "_finance"))
    data = data.merge(latest_valuations, on="symbol", how="left", suffixes=("", "_valuation"))
    data = data.merge(momentum, on="symbol", how="left")
    data["exclusion_reason"] = data.apply(lambda row: _exclusion_reason(row, run_date, cfg), axis=1)

    candidates = data[data["exclusion_reason"].isna()].copy()
    excluded = data[data["exclusion_reason"].notna()].copy()
    if candidates.empty:
        return ScreenOutput(_format_results(candidates), _format_results(excluded))

    candidates["quality_score"] = _quality_score(candidates)
    candidates["valuation_score"] = _valuation_score(candidates)
    candidates["momentum_score"] = _momentum_score(candidates)
    candidates["total_score"] = (
        candidates["quality_score"] * cfg.quality_weight
        + candidates["valuation_score"] * cfg.valuation_weight
        + candidates["momentum_score"] * cfg.momentum_weight
    )
    candidates = candidates.sort_values(["total_score", "symbol"], ascending=[False, True]).reset_index(drop=True)
    candidates["rank"] = candidates.index + 1

    excluded["rank"] = 0
    excluded["quality_score"] = None
    excluded["valuation_score"] = None
    excluded["momentum_score"] = None
    excluded["total_score"] = None
    return ScreenOutput(_format_results(candidates), _format_results(excluded))


def _latest_by_symbol(df: pd.DataFrame, date_column: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["symbol"])
    rows = df.copy()
    rows = rows.sort_values(["symbol", date_column])
    return rows.groupby("symbol", as_index=False).tail(1)


def _momentum_features(quotes: pd.DataFrame) -> pd.DataFrame:
    if quotes.empty:
        return pd.DataFrame(columns=["symbol", "return_20", "return_60", "avg_amount_20", "ma20_position", "quote_count"])

    features = []
    for symbol, group in quotes.sort_values("trade_date").groupby("symbol"):
        closes = pd.to_numeric(group["close"], errors="coerce").dropna()
        amounts = pd.to_numeric(group["amount"], errors="coerce").dropna()
        if closes.empty:
            features.append(
                {
                    "symbol": symbol,
                    "return_20": None,
                    "return_60": None,
                    "avg_amount_20": amounts.tail(20).mean() if not amounts.empty else None,
                    "ma20_position": None,
                    "quote_count": 0,
                }
            )
            continue
        latest_close = closes.iloc[-1]
        ma20 = closes.tail(20).mean() if len(closes) >= 20 else None
        features.append(
            {
                "symbol": symbol,
                "return_20": _period_return(closes, 20),
                "return_60": _period_return(closes, 60),
                "avg_amount_20": amounts.tail(20).mean() if not amounts.empty else None,
                "ma20_position": latest_close / ma20 - 1 if ma20 else None,
                "quote_count": len(closes),
            }
        )
    return pd.DataFrame(features)


def _period_return(closes: pd.Series, window: int) -> float | None:
    if len(closes) <= window:
        return None
    base = closes.iloc[-window - 1]
    if not base:
        return None
    return closes.iloc[-1] / base - 1


def _exclusion_reason(row: pd.Series, run_date: str, cfg: ScreenConfig) -> str | None:
    reasons = []
    if row.get("board") not in cfg.allowed_boards:
        reasons.append("非沪深主板或创业板")
    if int(row.get("is_st") or 0) == 1:
        reasons.append("ST或退市风险")
    if _listing_days(row.get("list_date"), run_date) is not None and _listing_days(row.get("list_date"), run_date) < cfg.min_listing_days:
        reasons.append("上市不足180天")
    if pd.isna(row.get("close")):
        reasons.append("缺少最新行情")
    if (row.get("quote_count") or 0) < 60:
        reasons.append("行情历史不足60日")
    if pd.isna(row.get("avg_amount_20")) or float(row.get("avg_amount_20") or 0) < cfg.min_avg_amount_20:
        reasons.append("20日成交额不足")
    quality_present = sum(pd.notna(row.get(column)) for column in _QUALITY_COLUMNS)
    if quality_present < 3:
        reasons.append("核心财务指标不足")
    valuation_present = sum(pd.notna(row.get(column)) and float(row.get(column) or 0) > 0 for column in _VALUATION_COLUMNS)
    if valuation_present < 2:
        reasons.append("核心估值指标不足")
    return ";".join(reasons) if reasons else None


def _listing_days(list_date: object, run_date: str) -> int | None:
    if pd.isna(list_date) or not list_date:
        return None
    try:
        start = datetime.strptime(str(list_date), "%Y%m%d")
        end = datetime.strptime(run_date, "%Y%m%d")
    except ValueError:
        return None
    return (end - start).days


_QUALITY_COLUMNS = ("roe", "net_profit_growth", "revenue_growth", "net_profit_margin", "debt_to_assets")
_VALUATION_COLUMNS = ("pe_ttm", "pb", "market_cap")


def _quality_score(df: pd.DataFrame) -> pd.Series:
    parts = [
        _rank(df["roe"], high_is_good=True),
        _rank(df["net_profit_growth"], high_is_good=True),
        _rank(df["revenue_growth"], high_is_good=True),
        _rank(df["net_profit_margin"], high_is_good=True),
        _rank(df["debt_to_assets"], high_is_good=False),
    ]
    return pd.concat(parts, axis=1).mean(axis=1).fillna(0)


def _valuation_score(df: pd.DataFrame) -> pd.Series:
    market_cap_score = _rank(df["market_cap"], high_is_good=True)
    parts = [
        _rank(_positive_only(df["pe_ttm"]), high_is_good=False),
        _rank(_positive_only(df["pb"]), high_is_good=False),
        _rank(_positive_only(df.get("peg", pd.Series(index=df.index, dtype=float))), high_is_good=False),
        market_cap_score,
    ]
    return pd.concat(parts, axis=1).mean(axis=1).fillna(0)


def _momentum_score(df: pd.DataFrame) -> pd.Series:
    parts = [
        _rank(df["return_20"], high_is_good=True),
        _rank(df["return_60"], high_is_good=True),
        _rank(df["ma20_position"], high_is_good=True),
        _rank(df["avg_amount_20"], high_is_good=True),
    ]
    return pd.concat(parts, axis=1).mean(axis=1).fillna(0)


def _rank(series: pd.Series, high_is_good: bool) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    if values.notna().sum() <= 1:
        return pd.Series(50.0, index=series.index).where(values.notna())
    ranked = values.rank(pct=True, ascending=not high_is_good) * 100
    return ranked


def _positive_only(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    return values.where(values > 0)


def _format_results(df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "rank",
        "symbol",
        "name",
        "total_score",
        "quality_score",
        "valuation_score",
        "momentum_score",
        "close",
        "roe",
        "pe_ttm",
        "pb",
        "return_20",
        "return_60",
        "avg_amount_20",
        "exclusion_reason",
    ]
    if df.empty:
        return pd.DataFrame(columns=columns)
    rows = df.copy()
    if "rank" not in rows:
        rows["rank"] = 0
    for column in columns:
        if column not in rows:
            rows[column] = None
    return rows[columns]


def _empty_results() -> pd.DataFrame:
    return _format_results(pd.DataFrame())
