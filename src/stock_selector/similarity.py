from __future__ import annotations

from dataclasses import dataclass
from math import sqrt

import pandas as pd

from .storage import StockDatabase
from .universe import DEFAULT_UNIVERSE_BOARDS


WINDOWS = {
    "1m": 20,
    "month": 20,
    "一个月": 20,
    "half_year": 120,
    "6m": 120,
    "半年": 120,
    "1y": 240,
    "year": 240,
    "一年": 240,
}


@dataclass(frozen=True)
class SimilarityRequest:
    reference_symbol: str
    run_date: str
    window: str = "1m"
    top: int = 30


def similar_kline(db: StockDatabase, request: SimilarityRequest) -> dict[str, object]:
    window = _window_days(request.window)
    stocks = db.read_table("stocks")
    quotes = db.read_table("daily_quotes", "trade_date <= ?", (request.run_date,))
    if stocks.empty or quotes.empty:
        return _empty_response(request, window, "本地行情库为空，请先更新全市场行情。")

    universe = stocks[
        stocks["board"].isin(DEFAULT_UNIVERSE_BOARDS)
        & (pd.to_numeric(stocks["is_st"], errors="coerce").fillna(0) == 0)
    ].copy()
    symbols = set(universe["symbol"])
    quotes = quotes[quotes["symbol"].isin(symbols)].sort_values(["symbol", "trade_date"])
    reference = quotes[quotes["symbol"] == request.reference_symbol].copy()
    if len(reference) < window:
        return _empty_response(request, window, f"参考股票行情不足 {window} 个交易日。")

    reference_window = reference.tail(window)
    reference_vector = _shape_vector(reference_window)
    if reference_vector is None:
        return _empty_response(request, window, "参考股票行情无法计算形态。")

    names = universe.set_index("symbol")["name"].to_dict()
    rows: list[dict[str, object]] = []
    search_rows = max(window, 252)
    for symbol, group in quotes.groupby("symbol"):
        if symbol == request.reference_symbol:
            continue
        candidate_quotes = group.tail(search_rows)
        best = _best_match(symbol, candidate_quotes, reference_vector, window)
        if best is None:
            continue
        best["name"] = names.get(symbol)
        rows.append(best)

    if not rows:
        return _empty_response(request, window, "可匹配股票行情不足。")

    result = pd.DataFrame(rows).sort_values(["similarity", "symbol"], ascending=[False, True]).head(request.top)
    result = result.reset_index(drop=True)
    result["rank"] = result.index + 1
    columns = [
        "rank",
        "symbol",
        "name",
        "similarity",
        "match_start",
        "match_end",
        "period_return",
        "max_drawdown",
        "amount_change",
        "reason",
    ]
    return {
        "reference_symbol": request.reference_symbol,
        "run_date": request.run_date,
        "window": request.window,
        "window_days": window,
        "rows": _records(result[columns]),
        "message": None,
    }


def _best_match(symbol: str, quotes: pd.DataFrame, reference_vector: list[float], window: int) -> dict[str, object] | None:
    if len(quotes) < window:
        return None
    best: dict[str, object] | None = None
    best_similarity = -1.0
    max_start = len(quotes) - window
    for start in range(max_start + 1):
        segment = quotes.iloc[start : start + window]
        vector = _shape_vector(segment)
        if vector is None:
            continue
        similarity = _similarity(reference_vector, vector)
        if similarity > best_similarity:
            features = _window_features(segment)
            best_similarity = similarity
            best = {
                "symbol": symbol,
                "similarity": similarity,
                "match_start": str(segment["trade_date"].iloc[0]),
                "match_end": str(segment["trade_date"].iloc[-1]),
                "period_return": features["period_return"],
                "max_drawdown": features["max_drawdown"],
                "amount_change": features["amount_change"],
                "reason": f"走势形状相似度{similarity:.1f}，区间收益{features['period_return']:.1%}",
            }
    return best


def _shape_vector(quotes: pd.DataFrame) -> list[float] | None:
    closes = pd.to_numeric(quotes["close"], errors="coerce").dropna()
    if len(closes) < 2:
        return None
    base = closes.iloc[0]
    if not base:
        return None
    path = (closes / base - 1).tolist()
    mean = sum(path) / len(path)
    variance = sum((value - mean) ** 2 for value in path) / len(path)
    std = sqrt(variance)
    if std < 1e-9:
        return [0.0 for _ in path]
    return [(value - mean) / std for value in path]


def _similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    rmse = sqrt(sum((a - b) ** 2 for a, b in zip(left, right)) / len(left))
    return max(0.0, min(100.0, 100.0 / (1.0 + rmse)))


def _window_features(quotes: pd.DataFrame) -> dict[str, float | None]:
    closes = pd.to_numeric(quotes["close"], errors="coerce").dropna()
    amounts = pd.to_numeric(quotes["amount"], errors="coerce").dropna()
    period_return = closes.iloc[-1] / closes.iloc[0] - 1 if len(closes) > 1 and closes.iloc[0] else 0.0
    running_high = closes.cummax()
    drawdowns = closes / running_high - 1
    amount_change = None
    if len(amounts) > 1 and amounts.iloc[0]:
        amount_change = amounts.iloc[-1] / amounts.iloc[0] - 1
    return {
        "period_return": float(period_return),
        "max_drawdown": float(drawdowns.min()) if not drawdowns.empty else None,
        "amount_change": float(amount_change) if amount_change is not None else None,
    }


def _window_days(value: str) -> int:
    return WINDOWS.get(str(value).strip().lower(), 20)


def _records(df: pd.DataFrame) -> list[dict[str, object]]:
    rows = []
    for row in df.to_dict(orient="records"):
        rows.append({key: None if pd.isna(value) else value for key, value in row.items()})
    return rows


def _empty_response(request: SimilarityRequest, window: int, message: str) -> dict[str, object]:
    return {
        "reference_symbol": request.reference_symbol,
        "run_date": request.run_date,
        "window": request.window,
        "window_days": window,
        "rows": [],
        "message": message,
    }
