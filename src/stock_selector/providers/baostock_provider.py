from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
import socket
from typing import Iterator, Sequence

import pandas as pd

from ..normalization import baostock_code, board_for_symbol, exchange_for_symbol, is_st_name, local_symbol, parse_date, to_float, ts_code
from .base import DataProvider


class BaostockProvider(DataProvider):
    name = "baostock"

    def available(self) -> bool:
        try:
            import baostock  # noqa: F401
        except Exception:
            return False
        return True

    def fetch_stock_basic(self) -> pd.DataFrame:
        import baostock as bs

        with _baostock_session(bs):
            raw = _query_recent_frame(bs.query_all_stock)
        if raw.empty:
            return pd.DataFrame()
        rows = []
        for _, row in raw.iterrows():
            try:
                symbol = local_symbol(row.get("code"))
            except ValueError:
                continue
            name = str(row.get("code_name") or row.get("name") or "")
            rows.append(
                {
                    "symbol": symbol,
                    "ts_code": ts_code(symbol),
                    "name": name,
                    "exchange": exchange_for_symbol(symbol),
                    "board": board_for_symbol(symbol),
                    "list_date": None,
                    "is_st": int(is_st_name(name)),
                    "source": self.name,
                }
            )
        return pd.DataFrame(rows)

    def fetch_daily_quotes(self, symbols: Sequence[str], start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
        import baostock as bs

        fields = (
            "date,code,open,high,low,close,preclose,volume,amount,adjustflag,"
            "turn,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST"
        )
        frames = []
        with _baostock_session(bs):
            for symbol in symbols:
                try:
                    code = baostock_code(symbol)
                except ValueError:
                    continue
                rs = bs.query_history_k_data_plus(
                    code,
                    fields,
                    start_date=_date_with_dash(start),
                    end_date=_date_with_dash(end),
                    frequency="d",
                    adjustflag=_adjust_flag(adjust),
                )
                raw = _result_to_frame(rs)
                if raw.empty:
                    continue
                frames.append(_normalize_history(raw, self.name, adjust))
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True).dropna(subset=["symbol", "trade_date"])

    def fetch_market_snapshot(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        return pd.DataFrame(), pd.DataFrame()

    def fetch_valuations(
        self,
        symbols: Sequence[str],
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        import baostock as bs

        start_date = start or (datetime.now() - timedelta(days=370)).strftime("%Y%m%d")
        end_date = end or datetime.now().strftime("%Y%m%d")
        fields = "date,code,close,peTTM,pbMRQ,psTTM,pcfNcfTTM"
        frames = []
        with _baostock_session(bs):
            for symbol in symbols:
                try:
                    code = baostock_code(symbol)
                except ValueError:
                    continue
                rs = bs.query_history_k_data_plus(
                    code,
                    fields,
                    start_date=_date_with_dash(start_date),
                    end_date=_date_with_dash(end_date),
                    frequency="d",
                    adjustflag="3",
                )
                raw = _result_to_frame(rs)
                if raw.empty:
                    continue
                frames.append(
                    pd.DataFrame(
                        {
                            "symbol": raw["code"].map(local_symbol),
                            "trade_date": raw["date"].map(parse_date),
                            "pe_ttm": raw.get("peTTM").map(to_float),
                            "pe_static": None,
                            "pb": raw.get("pbMRQ").map(to_float),
                            "peg": None,
                            "ps": raw.get("psTTM").map(to_float),
                            "market_cap": None,
                            "circulating_market_cap": None,
                            "dividend_yield": None,
                            "source": self.name,
                        }
                    )
                )
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True).dropna(subset=["symbol", "trade_date"])

    def fetch_financial_indicators(self, symbols: Sequence[str]) -> pd.DataFrame:
        import baostock as bs

        frames = []
        current_year = datetime.now().year
        years = range(current_year - 5, current_year + 1)
        with _baostock_session(bs):
            for symbol in symbols:
                try:
                    code = baostock_code(symbol)
                except ValueError:
                    continue
                frames.extend(_fetch_financial_for_code(bs, code, years, self.name))
        if not frames:
            return pd.DataFrame()
        return pd.DataFrame(frames).dropna(subset=["symbol", "report_date"])


@contextmanager
def _baostock_session(bs: object) -> Iterator[None]:
    previous_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(25)
    try:
        login = bs.login()
        if getattr(login, "error_code", "0") != "0":
            raise RuntimeError(f"Baostock login failed: {login.error_msg}")
        try:
            yield
        finally:
            bs.logout()
    finally:
        socket.setdefaulttimeout(previous_timeout)


def _query_recent_frame(query_func) -> pd.DataFrame:
    today = datetime.now()
    for offset in range(0, 10):
        day = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        result = query_func(day)
        frame = _result_to_frame(result)
        if not frame.empty:
            return frame
    return pd.DataFrame()


def _result_to_frame(result: object) -> pd.DataFrame:
    rows = []
    while result is not None and result.next():
        rows.append(result.get_row_data())
    fields = list(getattr(result, "fields", []) or [])
    return pd.DataFrame(rows, columns=fields)


def _normalize_history(raw: pd.DataFrame, source: str, adjust: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": raw["code"].map(local_symbol),
            "trade_date": raw["date"].map(parse_date),
            "open": raw.get("open").map(to_float),
            "high": raw.get("high").map(to_float),
            "low": raw.get("low").map(to_float),
            "close": raw.get("close").map(to_float),
            "pre_close": raw.get("preclose").map(to_float),
            "change": raw.get("close").map(to_float) - raw.get("preclose").map(to_float),
            "pct_change": raw.get("pctChg").map(to_float),
            "volume": raw.get("volume").map(to_float),
            "amount": raw.get("amount").map(to_float),
            "adjust": adjust,
            "source": source,
        }
    )


def _fetch_financial_for_code(bs: object, code: str, years: range, source: str) -> list[dict[str, object]]:
    rows = []
    for year in years:
        for quarter in (1, 2, 3, 4):
            profit = _first_row(bs.query_profit_data(code=code, year=year, quarter=quarter))
            growth = _first_row(bs.query_growth_data(code=code, year=year, quarter=quarter))
            balance = _first_row(bs.query_balance_data(code=code, year=year, quarter=quarter))
            if not profit and not growth and not balance:
                continue
            report_date = _quarter_end(year, quarter)
            rows.append(
                {
                    "symbol": local_symbol(code),
                    "report_date": report_date,
                    "ann_date": parse_date(profit.get("pubDate") if profit else None),
                    "roe": to_float(profit.get("roeAvg") if profit else None),
                    "net_profit_margin": to_float(profit.get("npMargin") if profit else None),
                    "revenue_growth": None,
                    "net_profit_growth": to_float(growth.get("YOYNI") if growth else None),
                    "debt_to_assets": to_float(balance.get("assetLiabilityRatio") if balance else None),
                    "eps": to_float(profit.get("epsTTM") if profit else None),
                    "source": source,
                }
            )
    return rows


def _first_row(result: object) -> dict[str, object]:
    frame = _result_to_frame(result)
    if frame.empty:
        return {}
    return frame.iloc[0].to_dict()


def _quarter_end(year: int, quarter: int) -> str:
    dates = {1: "0331", 2: "0630", 3: "0930", 4: "1231"}
    return f"{year}{dates[quarter]}"


def _date_with_dash(value: str) -> str:
    text = str(value).replace("-", "")
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def _adjust_flag(adjust: str) -> str:
    return {"hfq": "1", "qfq": "2", "": "3"}.get(adjust, "3")
