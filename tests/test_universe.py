from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_selector.storage import StockDatabase
from stock_selector.universe import UniverseMarketProgress, market_data_status, update_universe_market


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

            progress: list[UniverseMarketProgress] = []
            first = update_universe_market(db, provider, "20250101", "20250125", batch_size=1, progress_callback=progress.append)
            second = update_universe_market(db, provider, "20250101", "20250125", batch_size=1)
            status = market_data_status(db)

            self.assertEqual(first.symbol_count, 3)
            self.assertEqual(first.completed_symbol_count, 3)
            self.assertEqual(first.quote_rows, 75)
            self.assertEqual(second.skipped_symbol_count, 3)
            self.assertEqual(second.quote_rows, 0)
            self.assertEqual(len(db.read_table("daily_quotes")), 75)
            self.assertEqual(status.universe_count, 3)
            self.assertEqual(status.quoted_symbol_count, 3)
            self.assertEqual(status.missing_symbol_count, 0)
            self.assertFalse(progress[-1].running)
            self.assertIsNotNone(progress[-1].job_id)

    def test_update_universe_market_keeps_successful_symbols_when_one_symbol_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = StockDatabase(Path(temp_dir) / "stock.db")
            db.initialize()
            db.upsert_dataframe(
                "stocks",
                pd.DataFrame(
                    [
                        _stock("000001", "Alpha", "main"),
                        _stock("300001", "Beta", "chinext"),
                        _stock("300002", "Gamma", "chinext"),
                    ]
                ),
                keys=("symbol",),
            )

            result = update_universe_market(db, _FailingUniverseProvider(), "20250101", "20250125", batch_size=20)

            self.assertEqual(result.completed_symbol_count, 2)
            self.assertEqual(result.failed_symbol_count, 1)
            self.assertEqual(len(db.read_table("daily_quotes")), 50)
            self.assertTrue(any("300001" in error for error in result.errors))

    def test_update_universe_market_can_be_cancelled_without_losing_written_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = StockDatabase(Path(temp_dir) / "stock.db")
            db.initialize()
            db.upsert_dataframe(
                "stocks",
                pd.DataFrame([_stock("000001", "Alpha", "main"), _stock("300001", "Beta", "chinext")]),
                keys=("symbol",),
            )
            progress: list[UniverseMarketProgress] = []

            result = update_universe_market(
                db,
                _FakeUniverseProvider(),
                "20250101",
                "20250125",
                batch_size=20,
                progress_callback=progress.append,
                should_cancel=lambda: any(item.completed_symbols >= 1 for item in progress),
            )

            self.assertEqual(result.completed_symbol_count, 1)
            self.assertEqual(result.failed_symbol_count, 0)
            self.assertEqual(len(db.read_table("daily_quotes")), 25)
            self.assertFalse(progress[-1].running)
            self.assertTrue(any("停止" in error for error in result.errors))


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


class _FailingUniverseProvider(_FakeUniverseProvider):
    def fetch_daily_quotes(self, symbols: list[str], start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
        if symbols == ["300001"]:
            raise RuntimeError("network failed")
        return super().fetch_daily_quotes(symbols, start, end, adjust)


if __name__ == "__main__":
    unittest.main()
