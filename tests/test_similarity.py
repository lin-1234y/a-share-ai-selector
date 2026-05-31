from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_selector.similarity import SimilarityRequest, similar_kline
from stock_selector.storage import StockDatabase


class SimilarityTests(unittest.TestCase):
    def test_identical_shape_with_different_prices_scores_high(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = StockDatabase(Path(temp_dir) / "stock.db")
            db.initialize()
            db.upsert_dataframe(
                "stocks",
                pd.DataFrame([_stock("000001", "Ref"), _stock("000002", "Twin"), _stock("300001", "Opposite")]),
                keys=("symbol",),
            )
            pattern = [10, 11, 12, 11, 13, 15, 14, 16, 18, 17, 19, 20, 19, 21, 22, 24, 23, 25, 27, 28]
            quotes = []
            quotes.extend(_quotes("000001", pattern))
            quotes.extend(_quotes("000002", [value * 3 for value in pattern]))
            quotes.extend(_quotes("300001", list(reversed(pattern))))
            db.upsert_dataframe("daily_quotes", pd.DataFrame(quotes), keys=("symbol", "trade_date", "adjust"))

            result = similar_kline(db, SimilarityRequest("000001", "20250120", "1m", top=5))

            self.assertEqual(result["rows"][0]["symbol"], "000002")
            self.assertGreater(result["rows"][0]["similarity"], 99)
            opposite = next(row for row in result["rows"] if row["symbol"] == "300001")
            self.assertLess(opposite["similarity"], 50)

    def test_insufficient_reference_history_returns_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = StockDatabase(Path(temp_dir) / "stock.db")
            db.initialize()
            db.upsert_dataframe("stocks", pd.DataFrame([_stock("000001", "Ref")]), keys=("symbol",))
            db.upsert_dataframe("daily_quotes", pd.DataFrame(_quotes("000001", [10, 11])), keys=("symbol", "trade_date", "adjust"))

            result = similar_kline(db, SimilarityRequest("000001", "20250120", "1m", top=5))

            self.assertEqual(result["rows"], [])
            self.assertIn("不足", result["message"])


def _stock(symbol: str, name: str) -> dict[str, object]:
    return {
        "symbol": symbol,
        "ts_code": f"{symbol}.SZ",
        "name": name,
        "exchange": "SZ",
        "board": "main" if symbol.startswith("000") else "chinext",
        "list_date": "20000101",
        "is_st": 0,
        "source": "test",
    }


def _quotes(symbol: str, closes: list[float]) -> list[dict[str, object]]:
    start = datetime(2025, 1, 1)
    rows = []
    for i, close in enumerate(closes):
        rows.append(
            {
                "symbol": symbol,
                "trade_date": (start + timedelta(days=i)).strftime("%Y%m%d"),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "pre_close": closes[i - 1] if i else close,
                "change": 0,
                "pct_change": 0,
                "volume": 1_000_000,
                "amount": 100_000_000 + i,
                "adjust": "qfq",
                "source": "test",
            }
        )
    return rows


if __name__ == "__main__":
    unittest.main()
