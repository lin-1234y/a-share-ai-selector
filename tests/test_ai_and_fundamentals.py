from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_selector.ai import parse_natural_query, summarize_stock_insight
from stock_selector.fundamentals import StockInsight


class AiAndFundamentalsTests(unittest.TestCase):
    def test_fallback_parser_understands_similarity_query(self) -> None:
        old_key = os.environ.pop("OPENAI_API_KEY", None)
        try:
            parsed = parse_natural_query("找和300750最近半年走势最像的股票")
        finally:
            if old_key:
                os.environ["OPENAI_API_KEY"] = old_key

        self.assertFalse(parsed.ai_enabled)
        self.assertEqual(parsed.task, "similar_kline")
        self.assertEqual(parsed.reference_symbol, "300750")
        self.assertEqual(parsed.window, "half_year")

    def test_stock_insight_summary_falls_back_without_api_key(self) -> None:
        old_key = os.environ.pop("OPENAI_API_KEY", None)
        try:
            summary = summarize_stock_insight(
                StockInsight(
                    symbol="000001",
                    stock={"symbol": "000001", "name": "平安银行"},
                    financial={"roe": 0.1, "net_profit_margin": 0.2, "net_profit_growth": 0.3},
                    valuation={"pe_ttm": 5, "pb": 0.5},
                    business=[{"主营构成": "银行业务"}],
                    news=[{"新闻标题": "测试新闻"}],
                    notices=[{"公告标题": "测试公告"}],
                    industry=[{"name": "银行"}],
                    errors=(),
                )
            )
        finally:
            if old_key:
                os.environ["OPENAI_API_KEY"] = old_key

        self.assertFalse(summary["ai_enabled"])
        self.assertIn("平安银行", summary["summary"])
        self.assertIn("银行业务", summary["summary"])


if __name__ == "__main__":
    unittest.main()
