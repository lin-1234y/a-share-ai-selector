from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .normalization import local_symbol
from .storage import StockDatabase


@dataclass(frozen=True)
class StockInsight:
    symbol: str
    stock: dict[str, object]
    financial: dict[str, object] | None
    valuation: dict[str, object] | None
    business: list[dict[str, object]]
    news: list[dict[str, object]]
    notices: list[dict[str, object]]
    industry: list[dict[str, object]]
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "stock": self.stock,
            "financial": self.financial,
            "valuation": self.valuation,
            "business": self.business,
            "news": self.news,
            "notices": self.notices,
            "industry": self.industry,
            "errors": list(self.errors),
        }


def fetch_stock_insight(db: StockDatabase, symbol: str, limit: int = 8) -> StockInsight:
    local = local_symbol(symbol)
    stocks = db.read_table("stocks", "symbol = ?", (local,))
    stock = _record(stocks.iloc[0].to_dict()) if not stocks.empty else {"symbol": local}
    financial = _latest_record(db.read_table("financial_indicators", "symbol = ?", (local,)), "report_date")
    valuation = _latest_record(db.read_table("valuations", "symbol = ?", (local,)), "trade_date")
    errors: list[str] = []
    business: list[dict[str, object]] = []
    news: list[dict[str, object]] = []
    notices: list[dict[str, object]] = []
    industry: list[dict[str, object]] = _local_industry_rows(stock)

    try:
        import akshare as ak
    except Exception as exc:
        return StockInsight(local, stock, financial, valuation, [], [], [], [], (f"AKShare不可用: {exc}",))

    try:
        business = _business_rows(ak, local, limit)
    except Exception as exc:
        errors.append(f"主营业务获取失败: {exc}")
    try:
        news = _news_rows(ak, local, limit)
    except Exception as exc:
        errors.append(f"新闻获取失败: {exc}")
    try:
        notices = _notice_rows(ak, local, limit)
    except Exception as exc:
        errors.append(f"公告获取失败: {exc}")
    try:
        industry.extend(_industry_rows(ak, local, limit))
    except Exception as exc:
        errors.append(f"行业概念获取失败: {exc}")

    return StockInsight(local, stock, financial, valuation, business, news, notices, industry, tuple(errors))


def summarize_without_ai(insight: StockInsight) -> str:
    name = insight.stock.get("name") or insight.symbol
    pieces = [f"{name}（{insight.symbol}）基础画像："]
    if insight.financial:
        pieces.append(
            "财务："
            f"ROE { _fmt_pct(insight.financial.get('roe')) }，"
            f"净利率 { _fmt_pct(insight.financial.get('net_profit_margin')) }，"
            f"净利润增速 { _fmt_pct(insight.financial.get('net_profit_growth')) }。"
        )
    if insight.valuation:
        pieces.append(
            "估值："
            f"PE(TTM) {_fmt_num(insight.valuation.get('pe_ttm'))}，"
            f"PB {_fmt_num(insight.valuation.get('pb'))}。"
        )
    if insight.business:
        top_business = "；".join(_first_text(row) for row in insight.business[:3] if _first_text(row))
        if top_business:
            pieces.append(f"主营/产品：{top_business}。")
    if insight.news:
        pieces.append(f"最新新闻：{_first_text(insight.news[0])}。")
    if insight.notices:
        pieces.append(f"最新公告：{_first_text(insight.notices[0])}。")
    if insight.industry:
        pieces.append(f"行业/概念：{'、'.join(_first_text(row) for row in insight.industry[:5] if _first_text(row))}。")
    if insight.errors:
        pieces.append(f"部分外部数据缺失：{'；'.join(insight.errors[:2])}。")
    return "\n".join(piece for piece in pieces if piece)


def _business_rows(ak: Any, symbol: str, limit: int) -> list[dict[str, object]]:
    if not hasattr(ak, "stock_zygc_em"):
        return []
    df = ak.stock_zygc_em(symbol=symbol)
    return _simple_records(df, limit)


def _news_rows(ak: Any, symbol: str, limit: int) -> list[dict[str, object]]:
    if hasattr(ak, "stock_news_em"):
        df = ak.stock_news_em(symbol=symbol)
        return _simple_records(df, limit)
    return []


def _notice_rows(ak: Any, symbol: str, limit: int) -> list[dict[str, object]]:
    if hasattr(ak, "stock_individual_notice_report"):
        try:
            df = ak.stock_individual_notice_report(symbol=symbol)
            return _simple_records(df, limit)
        except TypeError:
            pass
    if hasattr(ak, "stock_notice_report"):
        df = ak.stock_notice_report(symbol=symbol)
        return _simple_records(df, limit)
    return []


def _industry_rows(ak: Any, symbol: str, limit: int) -> list[dict[str, object]]:
    if not hasattr(ak, "stock_industry_category_cninfo"):
        return []
    try:
        df = ak.stock_industry_category_cninfo(symbol=symbol)
    except TypeError:
        return []
    return _simple_records(df, limit)


def _local_industry_rows(stock: dict[str, object]) -> list[dict[str, object]]:
    board = stock.get("board")
    if not board:
        return []
    names = {"main": "沪深主板", "chinext": "创业板", "star": "科创板", "beijing": "北交所"}
    return [{"type": "board", "name": names.get(str(board), str(board))}]


def _latest_record(df: pd.DataFrame, date_column: str) -> dict[str, object] | None:
    if df.empty:
        return None
    return _record(df.sort_values(date_column).iloc[-1].to_dict())


def _simple_records(df: pd.DataFrame, limit: int) -> list[dict[str, object]]:
    if df is None or df.empty:
        return []
    return [_record(row) for row in df.head(limit).to_dict(orient="records")]


def _record(row: dict[str, object]) -> dict[str, object]:
    return {str(key): None if pd.isna(value) else value for key, value in row.items()}


def _first_text(row: dict[str, object]) -> str:
    for key in ("新闻标题", "标题", "公告标题", "名称", "主营构成", "项目", "业务名称", "分类方向"):
        value = row.get(key)
        if value:
            return str(value)
    for value in row.values():
        if value:
            return str(value)
    return ""


def _fmt_pct(value: object) -> str:
    try:
        return f"{float(value):.1%}"
    except (TypeError, ValueError):
        return "-"


def _fmt_num(value: object) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "-"
