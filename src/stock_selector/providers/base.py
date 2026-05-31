from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

import pandas as pd


class DataProvider(ABC):
    name: str

    @abstractmethod
    def available(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def fetch_stock_basic(self) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def fetch_daily_quotes(self, symbols: Sequence[str], start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def fetch_market_snapshot(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        raise NotImplementedError

    def fetch_valuations(
        self,
        symbols: Sequence[str],
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        return pd.DataFrame()

    @abstractmethod
    def fetch_financial_indicators(self, symbols: Sequence[str]) -> pd.DataFrame:
        raise NotImplementedError
