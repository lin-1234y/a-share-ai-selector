from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd

from .normalization import board_for_symbol, is_st_name, local_symbol, to_float
from .storage import StockDatabase
from .universe import DEFAULT_UNIVERSE_BOARDS, market_data_status


@dataclass(frozen=True)
class MarketSnapshotUpdate:
    total_symbols: int
    matched_symbols: int
    quote_rows: int
    trade_date: str
    source: str
    message: str


def update_market_snapshot_quotes(
    db: StockDatabase,
    trade_date: str,
    boards: tuple[str, ...] = DEFAULT_UNIVERSE_BOARDS,
) -> MarketSnapshotUpdate:
    job_id = _create_job(db, trade_date, boards)
    try:
        quotes = fetch_akshare_spot_quotes(trade_date)
        symbols = set(db.active_symbols(boards=boards))
        if symbols:
            quotes = quotes[quotes["symbol"].isin(symbols)].copy()
        quote_rows = db.upsert_dataframe("daily_quotes", quotes, keys=("symbol", "trade_date", "adjust"))
        status = market_data_status(db, boards=boards)
        matched = int(quotes["symbol"].nunique()) if not quotes.empty else 0
        message = f"已用东方财富快照更新 {matched} 只股票。"
        _finish_job(db, job_id, "finished", matched, 0, quote_rows, status.latest_trade_date, message)
        return MarketSnapshotUpdate(
            total_symbols=status.universe_count,
            matched_symbols=matched,
            quote_rows=quote_rows,
            trade_date=trade_date,
            source="akshare_spot_em",
            message=message,
        )
    except Exception as exc:
        _finish_job(db, job_id, "failed", 0, 1, 0, None, str(exc))
        db.record_error("akshare_spot_em", "update_market_snapshot_quotes", exc)
        raise


def fetch_akshare_spot_quotes(trade_date: str) -> pd.DataFrame:
    import akshare as ak

    spot = ak.stock_zh_a_spot_em()
    if spot.empty:
        return pd.DataFrame()
    rows = []
    for _, row in spot.iterrows():
        try:
            symbol = local_symbol(_value(row, "代码", "code"))
        except ValueError:
            continue
        board = board_for_symbol(symbol)
        if board not in set(DEFAULT_UNIVERSE_BOARDS):
            continue
        name = str(_value(row, "名称", "name") or "")
        if is_st_name(name):
            continue
        close = to_float(_value(row, "最新价", "最新", "price"))
        pre_close = to_float(_value(row, "昨收", "pre_close"))
        change = to_float(_value(row, "涨跌额", "change"))
        if change is None and close is not None and pre_close:
            change = close - pre_close
        rows.append(
            {
                "symbol": symbol,
                "trade_date": trade_date,
                "open": to_float(_value(row, "今开", "开盘", "open")),
                "high": to_float(_value(row, "最高", "high")),
                "low": to_float(_value(row, "最低", "low")),
                "close": close,
                "pre_close": pre_close,
                "change": change,
                "pct_change": to_float(_value(row, "涨跌幅", "pct_change")),
                "volume": to_float(_value(row, "成交量", "volume")),
                "amount": to_float(_value(row, "成交额", "amount")),
                "adjust": "qfq",
                "source": "akshare_spot_em",
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.dropna(subset=["symbol", "trade_date", "close"])


def _value(row: pd.Series, *names: str) -> object:
    for name in names:
        if name in row and pd.notna(row[name]):
            return row[name]
    return None


def _create_job(db: StockDatabase, trade_date: str, boards: tuple[str, ...]) -> int:
    now = _now()
    total = int(db.query_value(
        f"SELECT COUNT(*) FROM stocks WHERE board IN ({', '.join(['?'] * len(boards))}) AND is_st = 0",
        boards,
    ) or 0)
    return db.execute(
        """
        INSERT INTO market_update_jobs (
            started_at, start_date, end_date, universe_boards, status, total_symbols, message
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (now, trade_date, trade_date, ",".join(boards), "running", total, "正在用全市场快照更新行情"),
    )


def _finish_job(
    db: StockDatabase,
    job_id: int,
    status: str,
    completed: int,
    failed: int,
    rows: int,
    latest_trade_date: str | None,
    message: str,
) -> None:
    db.execute(
        """
        UPDATE market_update_jobs
        SET status = ?, finished_at = ?, completed_symbols = ?, failed_symbols = ?,
            quote_rows = ?, latest_trade_date = ?, message = ?
        WHERE id = ?
        """,
        (status, _now(), completed, failed, rows, latest_trade_date, message, job_id),
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
