from __future__ import annotations

import json
from datetime import datetime
from typing import Sequence
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd

from ..normalization import exchange_for_symbol, local_symbol, parse_date, to_float
from .base import DataProvider


class SinaProvider(DataProvider):
    name = "sina"

    def available(self) -> bool:
        return True

    def fetch_stock_basic(self) -> pd.DataFrame:
        return pd.DataFrame()

    def fetch_daily_quotes(self, symbols: Sequence[str], start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
        frames = []
        start_date = _date_obj(start)
        end_date = _date_obj(end)
        days = max((end_date - start_date).days + 20, 30)
        for symbol in symbols:
            code = local_symbol(symbol)
            market_code = f"{exchange_for_symbol(code).lower()}{code}"
            url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?" + urlencode(
                {"symbol": market_code, "scale": 240, "ma": "no", "datalen": min(days, 1023)}
            )
            with urlopen(url, timeout=20) as response:
                text = response.read().decode("utf-8", errors="ignore")
            rows = json.loads(text) if text.strip() else []
            frame = _normalize_rows(rows, code, adjust, self.name)
            if not frame.empty:
                frame = frame[(frame["trade_date"] >= parse_date(start)) & (frame["trade_date"] <= parse_date(end))]
                frames.append(frame)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True).dropna(subset=["symbol", "trade_date"])

    def fetch_market_snapshot(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        return pd.DataFrame(), pd.DataFrame()

    def fetch_financial_indicators(self, symbols: Sequence[str]) -> pd.DataFrame:
        return pd.DataFrame()


def _normalize_rows(rows: list[dict[str, object]], symbol: str, adjust: str, source: str) -> pd.DataFrame:
    normalized = []
    for row in rows:
        close = to_float(row.get("close"))
        normalized.append(
            {
                "symbol": symbol,
                "trade_date": parse_date(row.get("day")),
                "open": to_float(row.get("open")),
                "high": to_float(row.get("high")),
                "low": to_float(row.get("low")),
                "close": close,
                "volume": to_float(row.get("volume")),
                "amount": None,
                "adjust": adjust,
                "source": source,
            }
        )
    frame = pd.DataFrame(normalized)
    if frame.empty:
        return frame
    frame = frame.sort_values("trade_date")
    frame["pre_close"] = frame["close"].shift(1)
    frame["change"] = frame["close"] - frame["pre_close"]
    frame["pct_change"] = frame["change"] / frame["pre_close"] * 100
    return frame


def _date_obj(value: str) -> datetime:
    text = str(value).replace("-", "")
    return datetime.strptime(text[:8], "%Y%m%d")
