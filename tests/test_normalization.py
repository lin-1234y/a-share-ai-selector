from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_selector.normalization import board_for_symbol, exchange_for_symbol, local_symbol, ts_code


class NormalizationTests(unittest.TestCase):
    def test_symbol_normalization(self) -> None:
        self.assertEqual(local_symbol("600000.SH"), "600000")
        self.assertEqual(local_symbol("SZ000001"), "000001")
        self.assertEqual(ts_code("300750"), "300750.SZ")

    def test_exchange_and_board(self) -> None:
        self.assertEqual(exchange_for_symbol("600519"), "SH")
        self.assertEqual(exchange_for_symbol("000001"), "SZ")
        self.assertEqual(exchange_for_symbol("830799"), "BJ")
        self.assertEqual(board_for_symbol("600519"), "main")
        self.assertEqual(board_for_symbol("300750"), "chinext")
        self.assertEqual(board_for_symbol("688981"), "star")


if __name__ == "__main__":
    unittest.main()
