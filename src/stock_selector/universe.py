from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .storage import StockDatabase


DEFAULT_UNIVERSE_BOARDS = ("main", "chinext")


@dataclass(frozen=True)
class UniverseMarketStatus:
    universe_count: int
    quoted_symbol_count: int
    missing_symbol_count: int
    latest_trade_date: str | None


@dataclass(frozen=True)
class UniverseMarketUpdate:
    symbol_count: int
    quote_rows: int
    valuation_rows: int
    latest_trade_date: str | None
    errors: tuple[str, ...] = ()


def universe_symbols(
    db: StockDatabase,
    boards: Sequence[str] = DEFAULT_UNIVERSE_BOARDS,
) -> list[str]:
    return db.active_symbols(boards=boards)


def market_data_status(
    db: StockDatabase,
    boards: Sequence[str] = DEFAULT_UNIVERSE_BOARDS,
) -> UniverseMarketStatus:
    symbols = set(universe_symbols(db, boards=boards))
    quotes = db.read_table("daily_quotes")
    if quotes.empty or not symbols:
        return UniverseMarketStatus(
            universe_count=len(symbols),
            quoted_symbol_count=0,
            missing_symbol_count=len(symbols),
            latest_trade_date=None,
        )
    scoped = quotes[quotes["symbol"].isin(symbols)].copy()
    quoted_symbols = set(scoped["symbol"].dropna().unique())
    latest = scoped["trade_date"].dropna().max() if not scoped.empty else None
    return UniverseMarketStatus(
        universe_count=len(symbols),
        quoted_symbol_count=len(quoted_symbols),
        missing_symbol_count=max(len(symbols) - len(quoted_symbols), 0),
        latest_trade_date=None if latest is None else str(latest),
    )


def update_universe_market(
    db: StockDatabase,
    provider,
    start: str,
    end: str,
    adjust: str = "qfq",
    boards: Sequence[str] = DEFAULT_UNIVERSE_BOARDS,
) -> UniverseMarketUpdate:
    symbols = universe_symbols(db, boards=boards)
    if not symbols:
        return UniverseMarketUpdate(
            symbol_count=0,
            quote_rows=0,
            valuation_rows=0,
            latest_trade_date=None,
            errors=("股票池为空，请先更新基础信息。",),
        )

    errors: list[str] = []
    quotes = provider.fetch_daily_quotes(symbols, start, end, adjust)
    quote_rows = db.upsert_dataframe("daily_quotes", quotes, keys=("symbol", "trade_date", "adjust"))
    valuation_rows = 0
    try:
        valuations = provider.fetch_valuations(symbols, start, end)
    except Exception as exc:
        db.record_error("composite", "fetch_valuations", exc)
        errors.append(f"估值更新失败: {exc}")
    else:
        valuation_rows = db.upsert_dataframe("valuations", valuations, keys=("symbol", "trade_date"))

    status = market_data_status(db, boards=boards)
    return UniverseMarketUpdate(
        symbol_count=len(symbols),
        quote_rows=quote_rows,
        valuation_rows=valuation_rows,
        latest_trade_date=status.latest_trade_date,
        errors=tuple(errors),
    )
