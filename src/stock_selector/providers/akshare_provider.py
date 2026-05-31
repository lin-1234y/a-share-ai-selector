from __future__ import annotations

from datetime import datetime
from typing import Sequence

import pandas as pd

from ..normalization import board_for_symbol, exchange_for_symbol, is_st_name, local_symbol, parse_date, to_float, ts_code
from .base import DataProvider


class AkShareProvider(DataProvider):
    name = "akshare"

    def available(self) -> bool:
        try:
            import akshare  # noqa: F401
        except Exception:
            return False
        return True

    def fetch_stock_basic(self) -> pd.DataFrame:
        import akshare as ak

        try:
            spot = ak.stock_zh_a_spot_em()
        except Exception:
            spot = ak.stock_info_a_code_name().rename(columns={"code": "代码", "name": "名称"})
        rows = []
        for _, row in spot.iterrows():
            try:
                symbol = local_symbol(row.get("代码"))
            except ValueError:
                continue
            name = str(row.get("名称") or "")
            rows.append(
                {
                    "symbol": symbol,
                    "ts_code": ts_code(symbol),
                    "name": name,
                    "exchange": exchange_for_symbol(symbol),
                    "board": board_for_symbol(symbol),
                    "list_date": parse_date(row.get("上市时间")),
                    "is_st": int(is_st_name(name)),
                    "source": self.name,
                }
            )
        return pd.DataFrame(rows)

    def fetch_daily_quotes(self, symbols: Sequence[str], start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
        import akshare as ak

        frames = []
        for symbol in symbols:
            local = local_symbol(symbol)
            try:
                df = ak.stock_zh_a_hist(
                    symbol=local,
                    period="daily",
                    start_date=start,
                    end_date=end,
                    adjust=adjust,
                )
                frame = _normalize_hist_frame(df, local, adjust, self.name)
            except Exception:
                exchange_prefix = exchange_for_symbol(local).lower()
                df = ak.stock_zh_a_daily(
                    symbol=f"{exchange_prefix}{local}",
                    start_date=start,
                    end_date=end,
                    adjust=adjust,
                )
                frame = _normalize_daily_frame(df, local, adjust, self.name)
            if df.empty:
                continue
            frames.append(frame)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True).dropna(subset=["symbol", "trade_date"])

    def fetch_market_snapshot(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        import akshare as ak

        spot = ak.stock_zh_a_spot_em()
        snapshot_date = datetime.now().strftime("%Y%m%d")
        snapshots = []
        valuations = []
        for _, row in spot.iterrows():
            try:
                symbol = local_symbol(row.get("代码"))
            except ValueError:
                continue
            pe_ttm = to_float(_first(row, "市盈率-动态", "市盈率(TTM)", "市盈率"))
            pb = to_float(row.get("市净率"))
            market_cap = to_float(row.get("总市值"))
            circulating_market_cap = to_float(row.get("流通市值"))
            snapshots.append(
                {
                    "symbol": symbol,
                    "snapshot_date": snapshot_date,
                    "price": to_float(row.get("最新价")),
                    "pct_change": to_float(row.get("涨跌幅")),
                    "volume": to_float(row.get("成交量")),
                    "amount": to_float(row.get("成交额")),
                    "market_cap": market_cap,
                    "circulating_market_cap": circulating_market_cap,
                    "turnover_rate": to_float(row.get("换手率")),
                    "pe_ttm": pe_ttm,
                    "pb": pb,
                    "source": self.name,
                }
            )
            valuations.append(
                {
                    "symbol": symbol,
                    "trade_date": snapshot_date,
                    "pe_ttm": pe_ttm,
                    "pe_static": to_float(row.get("市盈率-静")),
                    "pb": pb,
                    "peg": None,
                    "ps": None,
                    "market_cap": market_cap,
                    "circulating_market_cap": circulating_market_cap,
                    "dividend_yield": None,
                    "source": self.name,
                }
            )
        return pd.DataFrame(snapshots), pd.DataFrame(valuations)

    def fetch_valuations(
        self,
        symbols: Sequence[str],
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        import akshare as ak

        frames = []
        for symbol in symbols:
            local = local_symbol(symbol)
            df = ak.stock_value_em(symbol=local)
            if df.empty:
                continue
            frames.append(
                pd.DataFrame(
                    {
                        "symbol": local,
                        "trade_date": df["数据日期"].map(parse_date),
                        "pe_ttm": df.get("PE(TTM)").map(to_float),
                        "pe_static": df.get("PE(静)").map(to_float),
                        "pb": df.get("市净率").map(to_float),
                        "peg": df.get("PEG值").map(to_float),
                        "ps": df.get("市销率").map(to_float),
                        "market_cap": df.get("总市值").map(to_float),
                        "circulating_market_cap": df.get("流通市值").map(to_float),
                        "dividend_yield": None,
                        "source": self.name,
                    }
                )
            )
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True).dropna(subset=["symbol", "trade_date"])

    def fetch_financial_indicators(self, symbols: Sequence[str]) -> pd.DataFrame:
        import akshare as ak

        frames = []
        for symbol in symbols:
            local = local_symbol(symbol)
            df = self._fetch_one_financial(ak, local)
            if df.empty:
                continue
            frames.append(df)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True).dropna(subset=["symbol", "report_date"])

    def _fetch_one_financial(self, ak: object, symbol: str) -> pd.DataFrame:
        if hasattr(ak, "stock_financial_analysis_indicator"):
            raw = ak.stock_financial_analysis_indicator(symbol=symbol)
            if raw.empty and hasattr(ak, "stock_financial_abstract"):
                raw = ak.stock_financial_abstract(symbol=symbol)
        elif hasattr(ak, "stock_financial_abstract"):
            raw = ak.stock_financial_abstract(symbol=symbol)
        else:
            return pd.DataFrame()
        if raw.empty:
            return pd.DataFrame()
        if {"指标", "选项"}.issubset(set(raw.columns)):
            return _normalize_financial_abstract(raw, symbol, self.name)

        rows = []
        for _, row in raw.iterrows():
            report_date = _first(row, "日期", "报告期", "截止日期", "REPORT_DATE")
            rows.append(
                {
                    "symbol": symbol,
                    "report_date": parse_date(report_date),
                    "ann_date": parse_date(_first(row, "公告日期", "披露日期")),
                    "roe": to_float(_first(row, "净资产收益率", "加权净资产收益率", "ROE")),
                    "net_profit_margin": to_float(_first(row, "销售净利率", "净利率")),
                    "revenue_growth": to_float(_first(row, "主营业务收入增长率", "营业收入同比增长率")),
                    "net_profit_growth": to_float(_first(row, "净利润增长率", "归属净利润同比增长率")),
                    "debt_to_assets": to_float(_first(row, "资产负债率")),
                    "eps": to_float(_first(row, "每股收益", "基本每股收益")),
                    "source": self.name,
                }
            )
        return pd.DataFrame(rows)


def _first(row: pd.Series, *columns: str) -> object:
    for column in columns:
        if column in row and pd.notna(row[column]):
            return row[column]
    return None


def _normalize_hist_frame(df: pd.DataFrame, symbol: str, adjust: str, source: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    frame = pd.DataFrame(
        {
            "symbol": symbol,
            "trade_date": df["日期"].map(parse_date),
            "open": df.get("开盘").map(to_float),
            "high": df.get("最高").map(to_float),
            "low": df.get("最低").map(to_float),
            "close": df.get("收盘").map(to_float),
            "pre_close": None,
            "change": df.get("涨跌额").map(to_float) if "涨跌额" in df else None,
            "pct_change": df.get("涨跌幅").map(to_float) if "涨跌幅" in df else None,
            "volume": df.get("成交量").map(to_float) if "成交量" in df else None,
            "amount": df.get("成交额").map(to_float) if "成交额" in df else None,
            "adjust": adjust,
            "source": source,
        }
    )
    frame["pre_close"] = frame["close"] - frame["change"].fillna(0)
    return frame


def _normalize_daily_frame(df: pd.DataFrame, symbol: str, adjust: str, source: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    frame = pd.DataFrame(
        {
            "symbol": symbol,
            "trade_date": df["date"].map(parse_date),
            "open": df.get("open").map(to_float),
            "high": df.get("high").map(to_float),
            "low": df.get("low").map(to_float),
            "close": df.get("close").map(to_float),
            "pre_close": None,
            "change": None,
            "pct_change": None,
            "volume": df.get("volume").map(to_float),
            "amount": df.get("amount").map(to_float),
            "adjust": adjust,
            "source": source,
        }
    )
    frame["pre_close"] = frame["close"].shift(1)
    frame["change"] = frame["close"] - frame["pre_close"]
    frame["pct_change"] = frame["change"] / frame["pre_close"] * 100
    return frame


def _normalize_financial_abstract(raw: pd.DataFrame, symbol: str, source: str) -> pd.DataFrame:
    report_dates = [column for column in raw.columns if str(column).isdigit() and len(str(column)) == 8]
    rows = []
    for report_date in report_dates:
        rows.append(
            {
                "symbol": symbol,
                "report_date": parse_date(report_date),
                "ann_date": None,
                "roe": _metric(raw, "净资产收益率(ROE)", report_date),
                "net_profit_margin": _metric(raw, "销售净利率", report_date),
                "revenue_growth": _metric(raw, "营业总收入增长率", report_date),
                "net_profit_growth": _metric(raw, "归属母公司净利润增长率", report_date),
                "debt_to_assets": _metric(raw, "资产负债率", report_date),
                "eps": _metric(raw, "基本每股收益", report_date),
                "source": source,
            }
        )
    return pd.DataFrame(rows)


def _metric(raw: pd.DataFrame, name: str, report_date: str) -> float | None:
    matched = raw[raw["指标"] == name]
    if matched.empty:
        return None
    return to_float(matched.iloc[0].get(report_date))
