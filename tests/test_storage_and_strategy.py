from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_selector.storage import StockDatabase
from stock_selector.strategy import screen_stocks


class StorageAndStrategyTests(unittest.TestCase):
    def test_upsert_is_idempotent_and_screen_outputs_ranked_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = StockDatabase(Path(temp_dir) / "stock.db")
            db.initialize()
            stocks = pd.DataFrame(
                [
                    {
                        "symbol": "000001",
                        "ts_code": "000001.SZ",
                        "name": "Alpha Bank",
                        "exchange": "SZ",
                        "board": "main",
                        "list_date": "20000101",
                        "is_st": 0,
                        "source": "test",
                    },
                    {
                        "symbol": "300001",
                        "ts_code": "300001.SZ",
                        "name": "Beta Tech",
                        "exchange": "SZ",
                        "board": "chinext",
                        "list_date": "20100101",
                        "is_st": 0,
                        "source": "test",
                    },
                    {
                        "symbol": "688001",
                        "ts_code": "688001.SH",
                        "name": "Star Semi",
                        "exchange": "SH",
                        "board": "star",
                        "list_date": "20190101",
                        "is_st": 0,
                        "source": "test",
                    },
                ]
            )
            self.assertEqual(db.upsert_dataframe("stocks", stocks, keys=("symbol",)), 3)
            self.assertEqual(db.upsert_dataframe("stocks", stocks, keys=("symbol",)), 3)
            self.assertEqual(len(db.read_table("stocks")), 3)

            quotes = _quote_rows("000001", 10.0, 0.10) + _quote_rows("300001", 20.0, 0.05)
            db.upsert_dataframe("daily_quotes", pd.DataFrame(quotes), keys=("symbol", "trade_date", "adjust"))
            db.upsert_dataframe(
                "financial_indicators",
                pd.DataFrame(
                    [
                        _finance("000001", roe=15, margin=20, revenue_growth=12, profit_growth=10, debt=45, eps=1.2),
                        _finance("300001", roe=10, margin=15, revenue_growth=25, profit_growth=30, debt=30, eps=0.8),
                    ]
                ),
                keys=("symbol", "report_date"),
            )
            db.upsert_dataframe(
                "valuations",
                pd.DataFrame(
                    [
                        _valuation("000001", pe=8, pb=0.9, market_cap=500_000_000_000),
                        _valuation("300001", pe=35, pb=4.5, market_cap=80_000_000_000),
                    ]
                ),
                keys=("symbol", "trade_date"),
            )

            output = screen_stocks(db, "20250331")
            self.assertEqual(set(output.included["symbol"]), {"000001", "300001"})
            self.assertEqual(list(output.included["rank"]), [1, 2])
            self.assertTrue(output.included["total_score"].notna().all())
            self.assertIn("688001", set(output.excluded["symbol"]))
            reason = output.excluded.loc[output.excluded["symbol"] == "688001", "exclusion_reason"].iloc[0]
            self.assertIn("非沪深主板或创业板", reason)


def _quote_rows(symbol: str, base: float, daily_step: float) -> list[dict[str, object]]:
    start = datetime(2025, 1, 1)
    rows = []
    for i in range(70):
        date = (start + timedelta(days=i)).strftime("%Y%m%d")
        close = base + i * daily_step
        rows.append(
            {
                "symbol": symbol,
                "trade_date": date,
                "open": close - 0.1,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "pre_close": close - daily_step,
                "change": daily_step,
                "pct_change": daily_step / max(close - daily_step, 0.01) * 100,
                "volume": 1_000_000,
                "amount": 100_000_000,
                "adjust": "qfq",
                "source": "test",
            }
        )
    return rows


def _finance(symbol: str, roe: float, margin: float, revenue_growth: float, profit_growth: float, debt: float, eps: float) -> dict[str, object]:
    return {
        "symbol": symbol,
        "report_date": "20241231",
        "ann_date": "20250315",
        "roe": roe,
        "net_profit_margin": margin,
        "revenue_growth": revenue_growth,
        "net_profit_growth": profit_growth,
        "debt_to_assets": debt,
        "eps": eps,
        "source": "test",
    }


def _valuation(symbol: str, pe: float, pb: float, market_cap: float) -> dict[str, object]:
    return {
        "symbol": symbol,
        "trade_date": "20250331",
        "pe_ttm": pe,
        "pe_static": pe,
        "pb": pb,
        "peg": 1.0,
        "ps": 2.0,
        "market_cap": market_cap,
        "circulating_market_cap": market_cap * 0.8,
        "dividend_yield": 2.0,
        "source": "test",
    }


if __name__ == "__main__":
    unittest.main()
