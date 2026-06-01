from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Sequence

import pandas as pd

from .storage import StockDatabase


DEFAULT_UNIVERSE_BOARDS = ("main", "chinext", "star")


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
    job_id: int | None = None
    source_counts: dict[str, int] | None = None


def universe_symbols(
    db: StockDatabase,
    boards: Sequence[str] = DEFAULT_UNIVERSE_BOARDS,
) -> list[str]:
    return db.active_symbols(boards=boards)


def market_data_status(
    db: StockDatabase,
    boards: Sequence[str] = DEFAULT_UNIVERSE_BOARDS,
) -> UniverseMarketStatus:
    board_values = tuple(boards)
    placeholders = ", ".join(["?"] * len(board_values))
    universe_count = int(
        db.query_value(f"SELECT COUNT(*) FROM stocks WHERE board IN ({placeholders}) AND is_st = 0", board_values) or 0
    )
    if not universe_count:
        return UniverseMarketStatus(0, 0, 0, None)
    quoted = db.query_value(
        f"""
        SELECT COUNT(DISTINCT q.symbol)
        FROM daily_quotes q
        JOIN stocks s ON s.symbol = q.symbol
        WHERE s.board IN ({placeholders}) AND s.is_st = 0
        """,
        board_values,
    )
    latest = db.query_value(
        f"""
        SELECT MAX(q.trade_date)
        FROM daily_quotes q
        JOIN stocks s ON s.symbol = q.symbol
        WHERE s.board IN ({placeholders}) AND s.is_st = 0
        """,
        board_values,
    )
    quoted_count = int(quoted or 0)
    return UniverseMarketStatus(
        universe_count=universe_count,
        quoted_symbol_count=quoted_count,
        missing_symbol_count=max(universe_count - quoted_count, 0),
        latest_trade_date=None if latest is None else str(latest),
    )


def update_universe_market(
    db: StockDatabase,
    provider,
    start: str,
    end: str,
    adjust: str = "qfq",
    boards: Sequence[str] = DEFAULT_UNIVERSE_BOARDS,
    batch_size: int = 20,
    workers: int = 6,
    skip_current: bool = True,
    progress_callback: Callable[[UniverseMarketProgress], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> UniverseMarketUpdate:
    symbols = universe_symbols(db, boards=boards)
    if not symbols:
        result = UniverseMarketUpdate(0, 0, 0, 0, 0, 0, None, errors=("股票池为空，请先更新股票基础信息。",))
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
                    job_id=None,
                    source_counts={},
                )
            )
        return result

    errors: list[str] = []
    source_counts: dict[str, int] = {}
    started_at = _now()
    job_id = _create_job(db, start, end, boards, len(symbols))
    pending_symbols, skipped_symbols = _pending_symbols(db, symbols, end) if skip_current else (symbols, [])
    batches = _chunks(pending_symbols, max(1, batch_size))
    quote_rows = 0
    completed_symbols = 0
    failed_symbols = 0
    current_batch = 0

    def emit(running: bool, current_batch_value: int, finished_at: str | None = None) -> None:
        status = market_data_status(db, boards=boards)
        _update_job(
            db,
            job_id,
            "running" if running else "finished",
            completed_symbols,
            len(skipped_symbols),
            failed_symbols,
            quote_rows,
            status.latest_trade_date,
            finished_at=finished_at,
            message=errors[-1] if errors else None,
        )
        if progress_callback:
            progress_callback(
                UniverseMarketProgress(
                    running=running,
                    total_symbols=len(symbols),
                    completed_symbols=completed_symbols,
                    skipped_symbols=len(skipped_symbols),
                    failed_symbols=failed_symbols,
                    current_batch=current_batch_value,
                    total_batches=len(batches),
                    quote_rows=quote_rows,
                    latest_trade_date=status.latest_trade_date,
                    started_at=started_at,
                    updated_at=_now(),
                    finished_at=finished_at,
                    errors=tuple(errors[-20:]),
                    job_id=job_id,
                    source_counts=dict(source_counts),
                )
            )

    emit(True, 0)
    cancelled = False
    for index, batch in enumerate(batches, start=1):
        current_batch = index
        if should_cancel and should_cancel():
            errors.append("用户已停止全市场行情更新。")
            cancelled = True
            break
        with ThreadPoolExecutor(max_workers=max(1, int(workers))) as executor:
            futures = {
                executor.submit(provider.fetch_daily_quotes, [symbol], start, end, adjust): symbol
                for symbol in batch
            }
            for future in as_completed(futures):
                symbol = futures[future]
                if should_cancel and should_cancel():
                    errors.append("用户已停止全市场行情更新。")
                    cancelled = True
                    break
                try:
                    quotes = future.result()
                    if quotes.empty:
                        raise RuntimeError("数据源没有返回行情")
                except Exception as exc:
                    failed_symbols += 1
                    message = f"{symbol} 更新失败: {exc}"
                    errors.append(message)
                    _record_update_failure(db, job_id, symbol, getattr(provider, "name", "provider"), exc)
                    emit(True, index)
                    continue
                quote_rows += db.upsert_dataframe("daily_quotes", quotes, keys=("symbol", "trade_date", "adjust"))
                completed_symbols += 1
                if "source" in quotes:
                    for source, count in quotes["source"].value_counts().to_dict().items():
                        source_counts[str(source)] = source_counts.get(str(source), 0) + int(count)
                _record_quality_checks(db, job_id, symbol, quotes)
                emit(True, index)
        if cancelled:
            break

    quality_issues = _count_quality_issues(db, job_id)
    if quality_issues:
        errors.append(f"发现 {quality_issues} 条行情质量问题，请在问题股票里查看。")
    status = market_data_status(db, boards=boards)
    final_status = "cancelled" if cancelled else "finished"
    finished_at = _now()
    _update_job(
        db,
        job_id,
        final_status,
        completed_symbols,
        len(skipped_symbols),
        failed_symbols,
        quote_rows,
        status.latest_trade_date,
        finished_at=finished_at,
        message=errors[-1] if errors else None,
    )
    emit(False, current_batch if batches else 0, finished_at=finished_at)
    return UniverseMarketUpdate(
        symbol_count=len(symbols),
        completed_symbol_count=completed_symbols,
        skipped_symbol_count=len(skipped_symbols),
        failed_symbol_count=failed_symbols,
        quote_rows=quote_rows,
        valuation_rows=0,
        latest_trade_date=status.latest_trade_date,
        errors=tuple(errors),
    )


def latest_market_job(db: StockDatabase) -> dict[str, object]:
    jobs = db.read_table("market_update_jobs").sort_values("id")
    if jobs.empty:
        return {}
    return _clean_record(jobs.iloc[-1].to_dict())


def recent_market_failures(db: StockDatabase, limit: int = 100) -> list[dict[str, object]]:
    rows = db.read_table("market_update_failures").sort_values("id", ascending=False).head(limit)
    return [_clean_record(row) for row in rows.to_dict(orient="records")]


def market_quality_report(db: StockDatabase, limit: int = 100) -> dict[str, object]:
    checks = db.read_table("market_quality_checks")
    if checks.empty:
        return {"issue_count": 0, "rows": []}
    failed = checks[checks["status"].isin(["failed", "warning"])].sort_values("id", ascending=False)
    return {
        "issue_count": int(len(failed)),
        "rows": [_clean_record(row) for row in failed.head(limit).to_dict(orient="records")],
    }


def _pending_symbols(db: StockDatabase, symbols: Sequence[str], end: str) -> tuple[list[str], list[str]]:
    if not symbols:
        return [], []
    quotes = db.read_table("(SELECT symbol, MAX(trade_date) AS latest FROM daily_quotes GROUP BY symbol)")
    latest_by_symbol = dict(zip(quotes.get("symbol", []), quotes.get("latest", []), strict=False)) if not quotes.empty else {}
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


def _create_job(db: StockDatabase, start: str, end: str, boards: Sequence[str], total_symbols: int) -> int:
    return db.execute(
        """
        INSERT INTO market_update_jobs (
            started_at, start_date, end_date, universe_boards, status, total_symbols, message
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (_now(), start, end, ",".join(boards), "running", total_symbols, "行情更新已启动"),
    )


def _update_job(
    db: StockDatabase,
    job_id: int,
    status: str,
    completed: int,
    skipped: int,
    failed: int,
    rows: int,
    latest_trade_date: str | None,
    finished_at: str | None = None,
    message: str | None = None,
) -> None:
    db.execute(
        """
        UPDATE market_update_jobs
        SET status = ?, completed_symbols = ?, skipped_symbols = ?, failed_symbols = ?,
            quote_rows = ?, latest_trade_date = ?, finished_at = COALESCE(?, finished_at),
            message = COALESCE(?, message)
        WHERE id = ?
        """,
        (status, completed, skipped, failed, rows, latest_trade_date, finished_at, message, job_id),
    )


def _record_update_failure(db: StockDatabase, job_id: int, symbol: str, provider: str, exc: Exception) -> None:
    db.execute(
        """
        INSERT INTO market_update_failures (job_id, symbol, provider, error, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (job_id, symbol, provider, str(exc), _now()),
    )
    db.record_error(provider, "fetch_daily_quotes", exc, symbol=symbol)


def _record_quality_checks(db: StockDatabase, job_id: int, symbol: str, quotes: pd.DataFrame) -> None:
    rows = []
    for _, row in quotes.iterrows():
        trade_date = str(row.get("trade_date") or "")
        open_price = row.get("open")
        high = row.get("high")
        low = row.get("low")
        close = row.get("close")
        amount = row.get("amount")
        volume = row.get("volume")
        if pd.notna(high) and pd.notna(low) and pd.notna(open_price) and pd.notna(close):
            if high < max(open_price, close) or low > min(open_price, close):
                rows.append((job_id, symbol, trade_date, "ohlc", "failed", "开高低收价格关系异常", _now()))
        if pd.isna(close) or close is None:
            rows.append((job_id, symbol, trade_date, "close", "failed", "收盘价缺失", _now()))
        if pd.isna(volume) or volume is None:
            rows.append((job_id, symbol, trade_date, "volume", "warning", "成交量缺失", _now()))
        if pd.isna(amount) or amount is None:
            rows.append((job_id, symbol, trade_date, "amount", "warning", "成交额缺失", _now()))
    for item in rows:
        db.execute(
            """
            INSERT INTO market_quality_checks (
                job_id, symbol, trade_date, check_name, status, message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            item,
        )


def _count_quality_issues(db: StockDatabase, job_id: int) -> int:
    value = db.query_value(
        "SELECT COUNT(*) FROM market_quality_checks WHERE job_id = ? AND status IN ('failed', 'warning')",
        (job_id,),
    )
    return int(value or 0)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _clean_record(record: dict[str, object]) -> dict[str, object]:
    cleaned = {}
    for key, value in record.items():
        if pd.isna(value):
            cleaned[key] = None
        else:
            cleaned[key] = value
    return cleaned
