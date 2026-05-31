from __future__ import annotations

from typing import Sequence

import pandas as pd

from ..storage import StockDatabase
from .akshare_provider import AkShareProvider
from .baostock_provider import BaostockProvider
from .base import DataProvider
from .tushare_provider import TushareProvider


class CompositeProvider(DataProvider):
    name = "composite"

    def __init__(self, providers: Sequence[DataProvider], db: StockDatabase | None = None):
        self.providers = list(providers)
        self.db = db

    def available(self) -> bool:
        return any(provider.available() for provider in self.providers)

    def fetch_stock_basic(self) -> pd.DataFrame:
        return self._first_success("fetch_stock_basic")

    def fetch_daily_quotes(self, symbols: Sequence[str], start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
        return self._first_success("fetch_daily_quotes", symbols, start, end, adjust)

    def fetch_market_snapshot(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        return self._first_success("fetch_market_snapshot")

    def fetch_valuations(
        self,
        symbols: Sequence[str],
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        return self._first_success("fetch_valuations", symbols, start, end)

    def fetch_financial_indicators(self, symbols: Sequence[str]) -> pd.DataFrame:
        return self._first_success("fetch_financial_indicators", symbols)

    def _first_success(self, method: str, *args):
        last_error: Exception | None = None
        for provider in self.providers:
            if not provider.available():
                continue
            try:
                result = getattr(provider, method)(*args)
            except Exception as exc:
                last_error = exc
                if self.db is not None:
                    self.db.record_error(provider.name, method, exc)
                continue
            if _has_data(result):
                return result
        if last_error is not None:
            raise last_error
        raise RuntimeError(
            "No data provider is available. Install akshare or set TUSHARE_TOKEN with tushare installed."
        )


def build_provider(db: StockDatabase | None = None) -> CompositeProvider:
    providers: list[DataProvider] = [BaostockProvider(), TushareProvider(), AkShareProvider()]
    return CompositeProvider(providers, db=db)


def _has_data(result: object) -> bool:
    if isinstance(result, pd.DataFrame):
        return not result.empty
    if isinstance(result, tuple):
        return any(isinstance(item, pd.DataFrame) and not item.empty for item in result)
    return result is not None
