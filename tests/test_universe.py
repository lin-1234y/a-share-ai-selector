from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_selector.storage import StockDatabase
from stock_selector.universe import market_data_status, update_universe_market


class UniverseTests(unittest.TestCase):
    def test_update_universe_market_is_idempotent_and_reports_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = StockDatabase(Path(temp_dir) / "stock.db")
            db.initialize()
            db.upsert_dataframe(
                "stocks",
                pd.DataFrame(
                    [
                        _stock("000001", "Alpha", "main"),
                        _stock("300001", "Beta", "chinext"),
                        _stock("688001", "Star", "star"),
                    ]
                ),
                keys=("symbol",),
            )
            provider = _FakeUniverseProvider()

            first = update_universe_market(db, provider, "20250101", "20250125")
            second = update_universe_market(db, provider, "20250101", "20250125")
            status = market_data_status(db)

            self.assertEqual(first.symbol_count, 2)
            self.assertEqual(first.quote_rows, 50)
            self.assertEqual(second.quote_rows, 50)
            self.assertEqual(len(db.read_table("daily_quotes")), 50)
            self.assertEqual(status.universe_count, 2)
            self.assertEqual(status.quoted_symbol_count, 2)
            self.assertEqual(status.missing_symbol_count, 0)


def _stock(symbol: str, name: str, board: str) -> dict[str, object]:
    return {
        "symbol": symbol,
        "ts_code": f"{symbol}.SZ",
        "name": name,
        "exchange": "SZ",
        "board": board,
        "list_date": "20000101",
        "is_st": 0,
        "source": "test",
    }


class _FakeUniverseProvider:
    def fetch_daily_quotes(self, symbols: list[str], start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
        rows = []
        start_date = datetime(2025, 1, 1)
        for symbol in symbols:
            for i in range(25):
                close = 10 + i
                rows.append(
                    {
                        "symbol": symbol,
                        "trade_date": (start_date + timedelta(days=i)).strftime("%Y%m%d"),
                        "open": close,
                        "high": close,
                        "low": close,
                        "close": close,
                        "pre_close": close,
                        "change": 0,
                        "pct_change": 0,
                        "volume": 1_000_000,
                        "amount": 100_000_000,
                        "adjust": adjust,
                        "source": "test",
                    }
                )
        return pd.DataFrame(rows)

    def fetch_valuations(self, symbols: list[str], start: str | None = None, end: str | None = None) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "symbol": symbol,
                    "trade_date": "20250125",
                    "pe_ttm": 10,
                    "pe_static": 10,
                    "pb": 1,
                    "peg": 1,
                    "ps": 1,
                    "market_cap": 100_000_000,
                    "circulating_market_cap": 80_000_000,
                    "dividend_yield": 1,
                    "source": "test",
                }
                for symbol in symbols
            ]
        )


if __name__ == "__main__":
    unittest.main()
