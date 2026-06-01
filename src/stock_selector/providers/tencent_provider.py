from __future__ import annotations

import json
from datetime import datetime
from typing import Sequence
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd

from ..normalization import exchange_for_symbol, local_symbol, parse_date, to_float
from .base import DataProvider


class TencentProvider(DataProvider):
    name = "tencent"

    def available(self) -> bool:
        return True

    def fetch_stock_basic(self) -> pd.DataFrame:
        return pd.DataFrame()

    def fetch_daily_quotes(self, symbols: Sequence[str], start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
        frames = []
        for symbol in symbols:
            code = local_symbol(symbol)
            market_code = f"{exchange_for_symbol(code).lower()}{code}"
            start_dash = _dash_date(start)
            end_dash = _dash_date(end)
            params = f"{market_code},day,{start_dash},{end_dash},640,{adjust or 'qfq'}"
            url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?" + urlencode({"param": params})
            with urlopen(url, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            data = payload.get("data", {}).get(market_code, {})
            rows = data.get(f"{adjust or 'qfq'}day") or data.get("day") or []
            frame = _normalize_rows(rows, code, adjust, self.name)
            if not frame.empty:
                frames.append(frame)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True).dropna(subset=["symbol", "trade_date"])

    def fetch_market_snapshot(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        return pd.DataFrame(), pd.DataFrame()

    def fetch_financial_indicators(self, symbols: Sequence[str]) -> pd.DataFrame:
        return pd.DataFrame()


def _normalize_rows(rows: list[list[object]], symbol: str, adjust: str, source: str) -> pd.DataFrame:
    normalized = []
    for row in rows:
        if len(row) < 6:
            continue
        close = to_float(row[2])
        normalized.append(
            {
                "symbol": symbol,
                "trade_date": parse_date(row[0]),
                "open": to_float(row[1]),
                "close": close,
                "high": to_float(row[3]),
                "low": to_float(row[4]),
                "volume": to_float(row[5]),
                "amount": to_float(row[6]) if len(row) > 6 else None,
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


def _dash_date(value: str) -> str:
    text = str(value).replace("-", "")
    try:
        return datetime.strptime(text[:8], "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return text
