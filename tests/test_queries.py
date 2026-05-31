from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_selector.queries import evaluate_spec, parse_ask
from stock_selector.storage import StockDatabase


class QueryTests(unittest.TestCase):
    def test_parse_chinese_query(self) -> None:
        spec = parse_ask("帮我选出股价在历史高位的百分三十以下的非ST与亏损股")
        self.assertEqual(spec.high_drawdown, 0.30)
        self.assertTrue(spec.exclude_st)
        self.assertTrue(spec.exclude_losing)

        ma_spec = parse_ask("价格在60周线附近的股")
        self.assertEqual(ma_spec.near_week_ma, 60)

        drop_spec = parse_ask("全A股里选一个月下跌大于30%的股")
        self.assertEqual(drop_spec.period_return_window, 20)
        self.assertEqual(drop_spec.period_return_threshold, -0.30)
        self.assertTrue(drop_spec.exclude_st)

        spoken_percent = parse_ask("一个月下跌大于百分30的股")
        self.assertEqual(spoken_percent.period_return_threshold, -0.30)

    def test_high_drawdown_query_excludes_losing_and_st(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = StockDatabase(Path(temp_dir) / "stock.db")
            db.initialize()
            db.upsert_dataframe(
                "stocks",
                pd.DataFrame(
                    [
                        _stock("000001", "Good A", 0),
                        _stock("000002", "Loss A", 0),
                        _stock("000003", "ST Bad", 1),
                    ]
                ),
                keys=("symbol",),
            )
            quotes = []
            quotes.extend(_quotes("000001", high=100, last=60))
            quotes.extend(_quotes("000002", high=100, last=55))
            quotes.extend(_quotes("000003", high=100, last=50))
            db.upsert_dataframe("daily_quotes", pd.DataFrame(quotes), keys=("symbol", "trade_date", "adjust"))
            db.upsert_dataframe(
                "financial_indicators",
                pd.DataFrame(
                    [
                        _finance("000001", eps=1.0, margin=10),
                        _finance("000002", eps=-0.2, margin=-3),
                        _finance("000003", eps=1.0, margin=10),
                    ]
                ),
                keys=("symbol", "report_date"),
            )
            result = evaluate_spec(db, parse_ask("历史高点回撤30%以上的非ST非亏损股"), "20260531")
            self.assertEqual(list(result["symbol"]), ["000001"])

    def test_monthly_drop_query_uses_20_trading_days_and_excludes_st(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = StockDatabase(Path(temp_dir) / "stock.db")
            db.initialize()
            db.upsert_dataframe(
                "stocks",
                pd.DataFrame(
                    [
                        _stock("000001", "Drop A", 0),
                        _stock("000002", "Small Drop", 0),
                        _stock("000003", "ST Drop", 1),
                        _stock("000004", "No History", 0),
                    ]
                ),
                keys=("symbol",),
            )
            quotes = []
            quotes.extend(_period_quotes("000001", start_close=100, last_close=65, days=21))
            quotes.extend(_period_quotes("000002", start_close=100, last_close=90, days=21))
            quotes.extend(_period_quotes("000003", start_close=100, last_close=50, days=21))
            quotes.extend(_period_quotes("000004", start_close=100, last_close=50, days=10))
            db.upsert_dataframe("daily_quotes", pd.DataFrame(quotes), keys=("symbol", "trade_date", "adjust"))

            result = evaluate_spec(db, parse_ask("一个月下跌超过30%的股票"), "20260531")

            self.assertEqual(list(result["symbol"]), ["000001"])
            self.assertAlmostEqual(result["period_return"].iloc[0], -0.35, places=4)
            self.assertEqual(result["period_start_close"].iloc[0], 100)


def _stock(symbol: str, name: str, is_st: int) -> dict[str, object]:
    return {
        "symbol": symbol,
        "ts_code": f"{symbol}.SZ",
        "name": name,
        "exchange": "SZ",
        "board": "main",
        "list_date": "20000101",
        "is_st": is_st,
        "source": "test",
    }


def _quotes(symbol: str, high: float, last: float) -> list[dict[str, object]]:
    start = datetime(2024, 1, 1)
    rows = []
    for i in range(320):
        close = high if i == 10 else high - (high - last) * i / 319
        rows.append(
            {
                "symbol": symbol,
                "trade_date": (start + timedelta(days=i)).strftime("%Y%m%d"),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "pre_close": close,
                "change": 0,
                "pct_change": 0,
                "volume": 1_000_000,
                "amount": 100_000_000,
                "adjust": "qfq",
                "source": "test",
            }
        )
    return rows


def _period_quotes(symbol: str, start_close: float, last_close: float, days: int) -> list[dict[str, object]]:
    start = datetime(2026, 1, 1)
    rows = []
    for i in range(days):
        close = start_close + (last_close - start_close) * i / max(days - 1, 1)
        rows.append(
            {
                "symbol": symbol,
                "trade_date": (start + timedelta(days=i)).strftime("%Y%m%d"),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "pre_close": close,
                "change": 0,
                "pct_change": 0,
                "volume": 1_000_000,
                "amount": 100_000_000,
                "adjust": "qfq",
                "source": "test",
            }
        )
    return rows


def _finance(symbol: str, eps: float, margin: float) -> dict[str, object]:
    return {
        "symbol": symbol,
        "report_date": "20251231",
        "ann_date": "20260331",
        "roe": 10,
        "net_profit_margin": margin,
        "revenue_growth": 10,
        "net_profit_growth": 10,
        "debt_to_assets": 40,
        "eps": eps,
        "source": "test",
    }


if __name__ == "__main__":
    unittest.main()
