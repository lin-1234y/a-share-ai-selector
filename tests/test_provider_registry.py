from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_selector.providers.base import DataProvider
from stock_selector.providers.registry import CompositeProvider


class ProviderRegistryTests(unittest.TestCase):
    def test_composite_falls_back_after_provider_failure(self) -> None:
        provider = CompositeProvider([FailingProvider(), WorkingProvider()])
        result = provider.fetch_stock_basic()
        self.assertEqual(result.iloc[0]["symbol"], "000001")


class FailingProvider(DataProvider):
    name = "failing"

    def available(self) -> bool:
        return True

    def fetch_stock_basic(self) -> pd.DataFrame:
        raise RuntimeError("temporary failure")

    def fetch_daily_quotes(self, symbols, start, end, adjust="qfq") -> pd.DataFrame:
        raise RuntimeError("temporary failure")

    def fetch_market_snapshot(self):
        raise RuntimeError("temporary failure")

    def fetch_financial_indicators(self, symbols) -> pd.DataFrame:
        raise RuntimeError("temporary failure")


class WorkingProvider(DataProvider):
    name = "working"

    def available(self) -> bool:
        return True

    def fetch_stock_basic(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "symbol": "000001",
                    "ts_code": "000001.SZ",
                    "name": "Ping An Bank",
                    "exchange": "SZ",
                    "board": "main",
                    "list_date": "19910403",
                    "is_st": 0,
                    "source": self.name,
                }
            ]
        )

    def fetch_daily_quotes(self, symbols, start, end, adjust="qfq") -> pd.DataFrame:
        return pd.DataFrame()

    def fetch_market_snapshot(self):
        return pd.DataFrame(), pd.DataFrame()

    def fetch_financial_indicators(self, symbols) -> pd.DataFrame:
        return pd.DataFrame()


if __name__ == "__main__":
    unittest.main()
