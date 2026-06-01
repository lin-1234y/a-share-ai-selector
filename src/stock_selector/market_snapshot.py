from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from .normalization import board_for_symbol, exchange_for_symbol, is_st_name, local_symbol, to_float
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
    symbols = db.active_symbols(boards=boards)
    source = "sina_realtime"
    try:
        try:
            quotes = fetch_sina_realtime_quotes(symbols, trade_date)
        except Exception as first_exc:
            db.record_error("sina_realtime", "update_market_snapshot_quotes", first_exc)
            try:
                source = "eastmoney_spot"
                quotes = fetch_eastmoney_spot_quotes(trade_date)
            except Exception as second_exc:
                db.record_error("eastmoney_spot", "update_market_snapshot_quotes", second_exc)
                source = "akshare_spot_em"
                quotes = fetch_akshare_spot_quotes(trade_date)

        symbol_set = set(symbols)
        if symbol_set:
            quotes = quotes[quotes["symbol"].isin(symbol_set)].copy()
        if quotes.empty:
            raise RuntimeError("market snapshot returned no usable quote rows")
        quote_rows = db.upsert_dataframe("daily_quotes", quotes, keys=("symbol", "trade_date", "adjust"))
        status = market_data_status(db, boards=boards)
        matched = int(quotes["symbol"].nunique())
        message = f"fast snapshot updated {matched} stocks"
        _finish_job(db, job_id, "finished", matched, 0, quote_rows, status.latest_trade_date, message)
        return MarketSnapshotUpdate(
            total_symbols=status.universe_count,
            matched_symbols=matched,
            quote_rows=quote_rows,
            trade_date=trade_date,
            source=source,
            message=message,
        )
    except Exception as exc:
        _finish_job(db, job_id, "failed", 0, 1, 0, None, str(exc))
        db.record_error(source, "update_market_snapshot_quotes", exc)
        raise


def fetch_eastmoney_spot_quotes(trade_date: str) -> pd.DataFrame:
    page_size = 200
    rows = []
    for page in range(1, 80):
        params = {
            "pn": str(page),
            "pz": str(page_size),
            "po": "1",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": "m:1+t:2,m:1+t:23,m:0+t:6,m:0+t:80",
            "fields": "f12,f14,f2,f3,f4,f5,f6,f15,f16,f17,f18",
        }
        request = Request(
            "https://push2.eastmoney.com/api/qt/clist/get?" + urlencode(params),
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
        )
        payload = None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urlopen(request, timeout=20) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except Exception as exc:
                last_error = exc
                time.sleep(0.5 * (attempt + 1))
        if payload is None:
            if rows:
                break
            raise RuntimeError(f"eastmoney snapshot page {page} failed: {last_error}")
        page_rows = payload.get("data", {}).get("diff", []) or []
        if not page_rows:
            break
        rows.extend(page_rows)
        if len(page_rows) < page_size:
            break
    normalized = []
    for row in rows:
        try:
            symbol = local_symbol(row.get("f12"))
        except ValueError:
            continue
        board = board_for_symbol(symbol)
        if board not in set(DEFAULT_UNIVERSE_BOARDS):
            continue
        name = str(row.get("f14") or "")
        if is_st_name(name):
            continue
        close = to_float(row.get("f2"))
        pre_close = to_float(row.get("f18"))
        change = to_float(row.get("f4"))
        if change is None and close is not None and pre_close:
            change = close - pre_close
        normalized.append(
            {
                "symbol": symbol,
                "trade_date": trade_date,
                "open": to_float(row.get("f17")),
                "high": to_float(row.get("f15")),
                "low": to_float(row.get("f16")),
                "close": close,
                "pre_close": pre_close,
                "change": change,
                "pct_change": to_float(row.get("f3")),
                "volume": to_float(row.get("f5")),
                "amount": to_float(row.get("f6")),
                "adjust": "qfq",
                "source": "eastmoney_spot",
            }
        )
    frame = pd.DataFrame(normalized)
    if frame.empty:
        return frame
    return frame.dropna(subset=["symbol", "trade_date", "close"])


def fetch_sina_realtime_quotes(symbols: list[str], trade_date: str) -> pd.DataFrame:
    rows = []
    for batch in _chunks(symbols, 700):
        codes = ",".join(_sina_code(symbol) for symbol in batch)
        request = Request(
            f"https://hq.sinajs.cn/list={codes}",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
        )
        with urlopen(request, timeout=20) as response:
            text = response.read().decode("gbk", errors="ignore")
        rows.extend(_parse_sina_lines(text, trade_date))
        time.sleep(0.2)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.dropna(subset=["symbol", "trade_date", "close"])


def _parse_sina_lines(text: str, trade_date: str) -> list[dict[str, object]]:
    rows = []
    for line in text.splitlines():
        if '="' not in line:
            continue
        prefix, payload = line.split('="', 1)
        symbol = local_symbol(prefix)
        values = payload.rstrip('";').split(",")
        if len(values) < 32 or not values[0]:
            continue
        name = values[0]
        if is_st_name(name):
            continue
        open_price = to_float(values[1])
        pre_close = to_float(values[2])
        close = to_float(values[3])
        high = to_float(values[4])
        low = to_float(values[5])
        volume = to_float(values[8])
        amount = to_float(values[9])
        if close is None or close <= 0:
            continue
        change = close - pre_close if pre_close else None
        pct_change = change / pre_close * 100 if change is not None and pre_close else None
        rows.append(
            {
                "symbol": symbol,
                "trade_date": trade_date,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "pre_close": pre_close,
                "change": change,
                "pct_change": pct_change,
                "volume": volume,
                "amount": amount,
                "adjust": "qfq",
                "source": "sina_realtime",
            }
        )
    return rows


def fetch_akshare_spot_quotes(trade_date: str) -> pd.DataFrame:
    import akshare as ak

    spot = ak.stock_zh_a_spot_em()
    if spot.empty:
        return pd.DataFrame()
    rows = []
    for _, row in spot.iterrows():
        try:
            symbol = local_symbol(_value(row, "\u4ee3\u7801", "code"))
        except ValueError:
            continue
        board = board_for_symbol(symbol)
        if board not in set(DEFAULT_UNIVERSE_BOARDS):
            continue
        name = str(_value(row, "\u540d\u79f0", "name") or "")
        if is_st_name(name):
            continue
        close = to_float(_value(row, "\u6700\u65b0\u4ef7", "price"))
        pre_close = to_float(_value(row, "\u6628\u6536", "pre_close"))
        change = to_float(_value(row, "\u6da8\u8dcc\u989d", "change"))
        if change is None and close is not None and pre_close:
            change = close - pre_close
        rows.append(
            {
                "symbol": symbol,
                "trade_date": trade_date,
                "open": to_float(_value(row, "\u4eca\u5f00", "open")),
                "high": to_float(_value(row, "\u6700\u9ad8", "high")),
                "low": to_float(_value(row, "\u6700\u4f4e", "low")),
                "close": close,
                "pre_close": pre_close,
                "change": change,
                "pct_change": to_float(_value(row, "\u6da8\u8dcc\u5e45", "pct_change")),
                "volume": to_float(_value(row, "\u6210\u4ea4\u91cf", "volume")),
                "amount": to_float(_value(row, "\u6210\u4ea4\u989d", "amount")),
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


def _sina_code(symbol: str) -> str:
    return f"{exchange_for_symbol(symbol).lower()}{local_symbol(symbol)}"


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _create_job(db: StockDatabase, trade_date: str, boards: tuple[str, ...]) -> int:
    now = _now()
    total = int(
        db.query_value(
            f"SELECT COUNT(*) FROM stocks WHERE board IN ({', '.join(['?'] * len(boards))}) AND is_st = 0",
            boards,
        )
        or 0
    )
    return db.execute(
        """
        INSERT INTO market_update_jobs (
            started_at, start_date, end_date, universe_boards, status, total_symbols, message
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (now, trade_date, trade_date, ",".join(boards), "running", total, "fast snapshot update started"),
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
