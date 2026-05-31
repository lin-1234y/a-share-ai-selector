from __future__ import annotations

import os
from datetime import datetime
from typing import Sequence

import pandas as pd

from ..normalization import board_for_symbol, exchange_for_symbol, is_st_name, local_symbol, parse_date, ts_code
from .base import DataProvider


class TushareProvider(DataProvider):
    name = "tushare"

    def __init__(self, token: str | None = None):
        self.token = token or os.getenv("TUSHARE_TOKEN")
        self._pro = None

    def available(self) -> bool:
        if not self.token:
            return False
        try:
            import tushare  # noqa: F401
        except Exception:
            return False
        return True

    @property
    def pro(self):
        if self._pro is None:
            import tushare as ts

            self._pro = ts.pro_api(self.token)
        return self._pro

    def fetch_stock_basic(self) -> pd.DataFrame:
        df = self.pro.stock_basic(
            exchange="",
            list_status="L",
            fields="ts_code,symbol,name,area,industry,market,list_date",
        )
        rows = []
        for _, row in df.iterrows():
            symbol = local_symbol(row.get("symbol") or row.get("ts_code"))
            name = str(row.get("name") or "")
            rows.append(
                {
                    "symbol": symbol,
                    "ts_code": row.get("ts_code") or ts_code(symbol),
                    "name": name,
                    "exchange": exchange_for_symbol(symbol),
                    "board": board_for_symbol(symbol),
                    "list_date": parse_date(row.get("list_date")),
                    "is_st": int(is_st_name(name)),
                    "source": self.name,
                }
            )
        return pd.DataFrame(rows)

    def fetch_daily_quotes(self, symbols: Sequence[str], start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
        frames = []
        for symbol in symbols:
            code = ts_code(symbol)
            df = self.pro.daily(ts_code=code, start_date=start, end_date=end)
            if df.empty:
                continue
            frame = pd.DataFrame(
                {
                    "symbol": df["ts_code"].map(local_symbol),
                    "trade_date": df["trade_date"].map(parse_date),
                    "open": df["open"],
                    "high": df["high"],
                    "low": df["low"],
                    "close": df["close"],
                    "pre_close": df["pre_close"],
                    "change": df["change"],
                    "pct_change": df["pct_chg"],
                    "volume": df["vol"],
                    "amount": df["amount"] * 1000.0,
                    "adjust": "",
                    "source": self.name,
                }
            )
            frames.append(frame)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True).dropna(subset=["symbol", "trade_date"])

    def fetch_market_snapshot(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        trade_date = datetime.now().strftime("%Y%m%d")
        df = self.pro.daily_basic(
            trade_date=trade_date,
            fields="ts_code,trade_date,close,turnover_rate,pe,pe_ttm,pb,ps,ps_ttm,total_mv,circ_mv,dv_ratio",
        )
        if df.empty:
            return pd.DataFrame(), pd.DataFrame()
        snapshots = pd.DataFrame(
            {
                "symbol": df["ts_code"].map(local_symbol),
                "snapshot_date": df["trade_date"].map(parse_date),
                "price": df.get("close"),
                "pct_change": None,
                "volume": None,
                "amount": None,
                "market_cap": df.get("total_mv") * 10_000.0,
                "circulating_market_cap": df.get("circ_mv") * 10_000.0,
                "turnover_rate": df.get("turnover_rate"),
                "pe_ttm": df.get("pe_ttm"),
                "pb": df.get("pb"),
                "source": self.name,
            }
        )
        valuations = pd.DataFrame(
            {
                "symbol": df["ts_code"].map(local_symbol),
                "trade_date": df["trade_date"].map(parse_date),
                "pe_ttm": df.get("pe_ttm"),
                "pe_static": df.get("pe"),
                "pb": df.get("pb"),
                "peg": None,
                "ps": df.get("ps_ttm") if "ps_ttm" in df else df.get("ps"),
                "market_cap": df.get("total_mv") * 10_000.0,
                "circulating_market_cap": df.get("circ_mv") * 10_000.0,
                "dividend_yield": df.get("dv_ratio"),
                "source": self.name,
            }
        )
        return snapshots, valuations

    def fetch_valuations(
        self,
        symbols: Sequence[str],
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        snapshots, valuations = self.fetch_market_snapshot()
        if valuations.empty:
            return valuations
        wanted = {local_symbol(symbol) for symbol in symbols}
        return valuations[valuations["symbol"].isin(wanted)].copy()

    def fetch_financial_indicators(self, symbols: Sequence[str]) -> pd.DataFrame:
        frames = []
        fields = (
            "ts_code,ann_date,end_date,eps,roe,netprofit_margin,"
            "q_sales_yoy,q_profit_yoy,debt_to_assets"
        )
        for symbol in symbols:
            df = self.pro.fina_indicator(ts_code=ts_code(symbol), fields=fields)
            if df.empty:
                continue
            frame = pd.DataFrame(
                {
                    "symbol": df["ts_code"].map(local_symbol),
                    "report_date": df["end_date"].map(parse_date),
                    "ann_date": df["ann_date"].map(parse_date),
                    "roe": df.get("roe"),
                    "net_profit_margin": df.get("netprofit_margin"),
                    "revenue_growth": df.get("q_sales_yoy"),
                    "net_profit_growth": df.get("q_profit_yoy"),
                    "debt_to_assets": df.get("debt_to_assets"),
                    "eps": df.get("eps"),
                    "source": self.name,
                }
            )
            frames.append(frame)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True).dropna(subset=["symbol", "report_date"])
