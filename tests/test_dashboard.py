from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_selector.dashboard import build_screen, build_stock_profile, build_summary
import stock_selector.dashboard as dashboard
from stock_selector.storage import StockDatabase


class DashboardTests(unittest.TestCase):
    def test_summary_screen_and_stock_profile_use_existing_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = StockDatabase(Path(temp_dir) / "stock.db")
            db.initialize()
            _seed_database(db)

            summary = build_summary(db)
            self.assertEqual(summary["stock_count"], 2)
            self.assertEqual(summary["latest_trade_date"], "20250410")

            screen = build_screen(db, "20250410", top=5)
            self.assertEqual(len(screen["rows"]), 2)
            self.assertEqual(screen["rows"][0]["rank"], 1)

            profile = build_stock_profile(db, "000001", "20250410")
            self.assertTrue(profile["found"])
            self.assertEqual(profile["stock"]["name"], "Alpha Bank")
            self.assertGreater(profile["features"]["return_20"], 0)
            self.assertEqual(len(profile["chart"]), 100)

    def test_stock_profile_auto_updates_missing_symbol_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = StockDatabase(Path(temp_dir) / "stock.db")
            db.initialize()
            db.upsert_dataframe(
                "stocks",
                pd.DataFrame(
                    [
                        {
                            "symbol": "000792",
                            "ts_code": "000792.SZ",
                            "name": "Salt Lake",
                            "exchange": "SZ",
                            "board": "main",
                            "list_date": "20000101",
                            "is_st": 0,
                            "source": "test",
                        }
                    ]
                ),
                keys=("symbol",),
            )
            original = dashboard.build_provider
            dashboard.build_provider = lambda db=None: _FakeProvider()
            try:
                profile = build_stock_profile(db, "000792", "20250410", auto_update=True)
            finally:
                dashboard.build_provider = original

            self.assertTrue(profile["found"])
            self.assertGreater(profile["latest_quote"]["close"], 0)
            self.assertEqual(profile["update_status"]["quote_rows"], 100)
            self.assertEqual(len(profile["chart"]), 100)


def _seed_database(db: StockDatabase) -> None:
    db.upsert_dataframe(
        "stocks",
        pd.DataFrame(
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
            ]
        ),
        keys=("symbol",),
    )
    db.upsert_dataframe(
        "daily_quotes",
        pd.DataFrame(_quote_rows("000001", 10.0, 0.10) + _quote_rows("300001", 20.0, 0.05)),
        keys=("symbol", "trade_date", "adjust"),
    )
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


def _quote_rows(symbol: str, base: float, daily_step: float) -> list[dict[str, object]]:
    start = datetime(2025, 1, 1)
    rows = []
    for i in range(100):
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
        "trade_date": "20250410",
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


class _FakeProvider:
    def fetch_daily_quotes(self, symbols: list[str], start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
        return pd.DataFrame(_quote_rows(symbols[0], 10.0, 0.1))

    def fetch_valuations(self, symbols: list[str], start: str | None = None, end: str | None = None) -> pd.DataFrame:
        return pd.DataFrame([_valuation(symbols[0], pe=12, pb=1.5, market_cap=100_000_000_000)])

    def fetch_financial_indicators(self, symbols: list[str]) -> pd.DataFrame:
        return pd.DataFrame([_finance(symbols[0], roe=12, margin=20, revenue_growth=10, profit_growth=11, debt=30, eps=0.9)])


if __name__ == "__main__":
    unittest.main()
