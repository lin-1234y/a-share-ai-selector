from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Sequence

import pandas as pd

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
    completed_symbol_count: int
    skipped_symbol_count: int
    failed_symbol_count: int
    quote_rows: int
    valuation_rows: int
    latest_trade_date: str | None
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class UniverseMarketProgress:
    running: bool
    total_symbols: int
    completed_symbols: int
    skipped_symbols: int
    failed_symbols: int
    current_batch: int
    total_batches: int
    quote_rows: int
    latest_trade_date: str | None
    started_at: str | None
    updated_at: str | None
    finished_at: str | None
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
    batch_size: int = 50,
    skip_current: bool = True,
    progress_callback: Callable[[UniverseMarketProgress], None] | None = None,
) -> UniverseMarketUpdate:
    symbols = universe_symbols(db, boards=boards)
    if not symbols:
        result = UniverseMarketUpdate(
            symbol_count=0,
            completed_symbol_count=0,
            skipped_symbol_count=0,
            failed_symbol_count=0,
            quote_rows=0,
            valuation_rows=0,
            latest_trade_date=None,
            errors=("股票池为空，请先更新基础信息。",),
        )
        if progress_callback:
            progress_callback(
                UniverseMarketProgress(
                    running=False,
                    total_symbols=0,
                    completed_symbols=0,
                    skipped_symbols=0,
                    failed_symbols=0,
                    current_batch=0,
                    total_batches=0,
                    quote_rows=0,
                    latest_trade_date=None,
                    started_at=None,
                    updated_at=_now(),
                    finished_at=_now(),
                    errors=result.errors,
                )
            )
        return result

    errors: list[str] = []
    started_at = _now()
    pending_symbols, skipped_symbols = _pending_symbols(db, symbols, end) if skip_current else (symbols, [])
    batches = _chunks(pending_symbols, max(1, batch_size))
    quote_rows = 0
    completed_symbols = 0
    failed_symbols = 0

    def emit(running: bool, current_batch: int, finished_at: str | None = None) -> None:
        if not progress_callback:
            return
        status = market_data_status(db, boards=boards)
        progress_callback(
            UniverseMarketProgress(
                running=running,
                total_symbols=len(symbols),
                completed_symbols=completed_symbols,
                skipped_symbols=len(skipped_symbols),
                failed_symbols=failed_symbols,
                current_batch=current_batch,
                total_batches=len(batches),
                quote_rows=quote_rows,
                latest_trade_date=status.latest_trade_date,
                started_at=started_at,
                updated_at=_now(),
                finished_at=finished_at,
                errors=tuple(errors[-20:]),
            )
        )

    emit(True, 0)
    for index, batch in enumerate(batches, start=1):
        try:
            quotes = provider.fetch_daily_quotes(batch, start, end, adjust)
        except Exception as exc:
            failed_symbols += len(batch)
            message = f"第{index}批失败({batch[0]}-{batch[-1]}): {exc}"
            errors.append(message)
            db.record_error(getattr(provider, "name", "provider"), "fetch_daily_quotes", exc)
            emit(True, index)
            continue
        quote_rows += db.upsert_dataframe("daily_quotes", quotes, keys=("symbol", "trade_date", "adjust"))
        completed_symbols += len(batch)
        emit(True, index)

    status = market_data_status(db, boards=boards)
    result = UniverseMarketUpdate(
        symbol_count=len(symbols),
        completed_symbol_count=completed_symbols,
        skipped_symbol_count=len(skipped_symbols),
        failed_symbol_count=failed_symbols,
        quote_rows=quote_rows,
        valuation_rows=0,
        latest_trade_date=status.latest_trade_date,
        errors=tuple(errors),
    )
    emit(False, len(batches), finished_at=_now())
    return result


def _pending_symbols(db: StockDatabase, symbols: Sequence[str], end: str) -> tuple[list[str], list[str]]:
    quotes = db.read_table("daily_quotes")
    if quotes.empty:
        return list(symbols), []
    scoped = quotes[quotes["symbol"].isin(set(symbols))]
    if scoped.empty:
        return list(symbols), []
    latest_by_symbol = scoped.groupby("symbol")["trade_date"].max().to_dict()
    pending: list[str] = []
    skipped: list[str] = []
    for symbol in symbols:
        latest = latest_by_symbol.get(symbol)
        if latest is not None and str(latest) >= end:
            skipped.append(symbol)
        else:
            pending.append(symbol)
    return pending, skipped


def _chunks(values: Sequence[str], size: int) -> list[list[str]]:
    return [list(values[index : index + size]) for index in range(0, len(values), size)]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
