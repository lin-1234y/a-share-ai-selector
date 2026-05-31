from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd


SCHEMA_SQL = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS stocks (
    symbol TEXT PRIMARY KEY,
    ts_code TEXT,
    name TEXT NOT NULL,
    exchange TEXT NOT NULL,
    board TEXT NOT NULL,
    list_date TEXT,
    is_st INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_quotes (
    symbol TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    pre_close REAL,
    change REAL,
    pct_change REAL,
    volume REAL,
    amount REAL,
    adjust TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, trade_date, adjust)
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    symbol TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    price REAL,
    pct_change REAL,
    volume REAL,
    amount REAL,
    market_cap REAL,
    circulating_market_cap REAL,
    turnover_rate REAL,
    pe_ttm REAL,
    pb REAL,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, snapshot_date)
);

CREATE TABLE IF NOT EXISTS financial_indicators (
    symbol TEXT NOT NULL,
    report_date TEXT NOT NULL,
    ann_date TEXT,
    roe REAL,
    net_profit_margin REAL,
    revenue_growth REAL,
    net_profit_growth REAL,
    debt_to_assets REAL,
    eps REAL,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, report_date)
);

CREATE TABLE IF NOT EXISTS valuations (
    symbol TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    pe_ttm REAL,
    pe_static REAL,
    pb REAL,
    peg REAL,
    ps REAL,
    market_cap REAL,
    circulating_market_cap REAL,
    dividend_yield REAL,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS screen_results (
    run_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    rank INTEGER NOT NULL,
    name TEXT,
    total_score REAL,
    quality_score REAL,
    valuation_score REAL,
    momentum_score REAL,
    close REAL,
    roe REAL,
    pe_ttm REAL,
    pb REAL,
    return_20 REAL,
    return_60 REAL,
    avg_amount_20 REAL,
    exclusion_reason TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_date, symbol)
);

CREATE TABLE IF NOT EXISTS fetch_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    action TEXT NOT NULL,
    symbol TEXT,
    error TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class StockDatabase:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        conn = self.connect()
        try:
            conn.executescript(SCHEMA_SQL)
            conn.commit()
        finally:
            conn.close()

    def upsert_dataframe(self, table: str, df: pd.DataFrame, keys: Sequence[str]) -> int:
        if df.empty:
            return 0
        self.initialize()
        now = datetime.now(UTC).isoformat(timespec="seconds")
        rows = df.copy()
        if "updated_at" in table_columns(table):
            rows["updated_at"] = now
        columns = [column for column in rows.columns if column in table_columns(table)]
        rows = rows[columns]
        placeholders = ", ".join(["?"] * len(columns))
        insert_columns = ", ".join(columns)
        update_columns = [column for column in columns if column not in keys]
        update_clause = ", ".join([f"{column}=excluded.{column}" for column in update_columns])
        key_clause = ", ".join(keys)
        sql = (
            f"INSERT INTO {table} ({insert_columns}) VALUES ({placeholders}) "
            f"ON CONFLICT ({key_clause}) DO UPDATE SET {update_clause}"
        )
        values = [tuple(_sqlite_value(value) for value in row) for row in rows.itertuples(index=False, name=None)]
        conn = self.connect()
        try:
            conn.executemany(sql, values)
            conn.commit()
        finally:
            conn.close()
        return len(values)

    def record_error(self, provider: str, action: str, error: Exception, symbol: str | None = None) -> None:
        self.initialize()
        conn = self.connect()
        try:
            conn.execute(
                """
                INSERT INTO fetch_errors (provider, action, symbol, error, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (provider, action, symbol, str(error), datetime.now(UTC).isoformat(timespec="seconds")),
            )
            conn.commit()
        finally:
            conn.close()

    def active_symbols(self, boards: Iterable[str] = ("main", "chinext")) -> list[str]:
        self.initialize()
        board_values = tuple(boards)
        placeholders = ", ".join(["?"] * len(board_values))
        conn = self.connect()
        try:
            rows = conn.execute(
                f"""
                SELECT symbol
                FROM stocks
                WHERE board IN ({placeholders}) AND is_st = 0
                ORDER BY symbol
                """,
                board_values,
            ).fetchall()
        finally:
            conn.close()
        return [row["symbol"] for row in rows]

    def read_table(self, table: str, where: str = "", params: Sequence[object] = ()) -> pd.DataFrame:
        self.initialize()
        sql = f"SELECT * FROM {table}"
        if where:
            sql += f" WHERE {where}"
        conn = self.connect()
        try:
            return pd.read_sql_query(sql, conn, params=params)
        finally:
            conn.close()

    def write_screen_results(self, run_date: str, results: pd.DataFrame) -> int:
        rows = results.copy()
        rows["run_date"] = run_date
        rows["created_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        return self.upsert_dataframe("screen_results", rows, keys=("run_date", "symbol"))


def table_columns(table: str) -> tuple[str, ...]:
    columns = {
        "stocks": (
            "symbol",
            "ts_code",
            "name",
            "exchange",
            "board",
            "list_date",
            "is_st",
            "source",
            "updated_at",
        ),
        "daily_quotes": (
            "symbol",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "pre_close",
            "change",
            "pct_change",
            "volume",
            "amount",
            "adjust",
            "source",
            "updated_at",
        ),
        "market_snapshots": (
            "symbol",
            "snapshot_date",
            "price",
            "pct_change",
            "volume",
            "amount",
            "market_cap",
            "circulating_market_cap",
            "turnover_rate",
            "pe_ttm",
            "pb",
            "source",
            "updated_at",
        ),
        "financial_indicators": (
            "symbol",
            "report_date",
            "ann_date",
            "roe",
            "net_profit_margin",
            "revenue_growth",
            "net_profit_growth",
            "debt_to_assets",
            "eps",
            "source",
            "updated_at",
        ),
        "valuations": (
            "symbol",
            "trade_date",
            "pe_ttm",
            "pe_static",
            "pb",
            "peg",
            "ps",
            "market_cap",
            "circulating_market_cap",
            "dividend_yield",
            "source",
            "updated_at",
        ),
        "screen_results": (
            "run_date",
            "symbol",
            "rank",
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
            "created_at",
        ),
    }
    return columns[table]


def _sqlite_value(value: object) -> object:
    if pd.isna(value):
        return None
    return value
