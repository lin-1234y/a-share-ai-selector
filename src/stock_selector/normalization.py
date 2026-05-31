from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any


_CODE_RE = re.compile(r"(\d{6})")


def local_symbol(value: Any) -> str:
    match = _CODE_RE.search(str(value or ""))
    if not match:
        raise ValueError(f"Invalid A-share symbol: {value!r}")
    return match.group(1)


def exchange_for_symbol(symbol: str) -> str:
    code = local_symbol(symbol)
    if code.startswith(("600", "601", "603", "605", "688", "689")):
        return "SH"
    if code.startswith(("000", "001", "002", "003", "300", "301")):
        return "SZ"
    if code.startswith(("4", "8", "9")):
        return "BJ"
    return "UNKNOWN"


def ts_code(symbol: str) -> str:
    code = local_symbol(symbol)
    exchange = exchange_for_symbol(code)
    if exchange == "UNKNOWN":
        return code
    return f"{code}.{exchange}"


def baostock_code(symbol: str) -> str:
    code = local_symbol(symbol)
    exchange = exchange_for_symbol(code).lower()
    if exchange not in {"sh", "sz"}:
        raise ValueError(f"Baostock does not support this symbol in the default universe: {symbol!r}")
    return f"{exchange}.{code}"


def board_for_symbol(symbol: str) -> str:
    code = local_symbol(symbol)
    if code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return "main"
    if code.startswith(("300", "301")):
        return "chinext"
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("4", "8", "9")):
        return "beijing"
    return "unknown"


def parse_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return None
    text = text.replace("-", "").replace("/", "")
    if len(text) >= 8 and text[:8].isdigit():
        return text[:8]
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(str(value), fmt).strftime("%Y%m%d")
        except ValueError:
            pass
    return None


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def is_st_name(name: Any) -> bool:
    text = str(name or "").upper()
    return "ST" in text or "退" in text
