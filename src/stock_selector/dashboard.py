from __future__ import annotations

import json
import mimetypes
import socket
import threading
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd

from .ai import parse_natural_query, summarize_stock_insight
from .config import DEFAULT_DB_PATH, DEFAULT_EXPORT_DIR, ScreenConfig
from .fundamentals import fetch_stock_insight
from .providers import build_provider
from .providers.baostock_provider import BaostockProvider
from .queries import evaluate_spec, parse_ask
from .similarity import SimilarityRequest, similar_kline
from .storage import StockDatabase
from .strategy import screen_stocks
from .universe import UniverseMarketProgress, market_data_status, update_universe_market


@dataclass(frozen=True)
class DashboardConfig:
    db_path: Path = DEFAULT_DB_PATH
    export_dir: Path = DEFAULT_EXPORT_DIR
    host: str = "0.0.0.0"
    port: int = 8765
    open_browser: bool = False


_UNIVERSE_UPDATE_LOCK = threading.RLock()
_UNIVERSE_UPDATE_PROGRESS = UniverseMarketProgress(
    running=False,
    total_symbols=0,
    completed_symbols=0,
    skipped_symbols=0,
    failed_symbols=0,
    current_batch=0,
    total_batches=0,
    quote_rows=0,
    latest_trade_date=None,
    started_at=None,
    updated_at=None,
    finished_at=None,
    errors=(),
)
_UNIVERSE_UPDATE_CANCEL = threading.Event()


def serve_dashboard(config: DashboardConfig) -> None:
    db = StockDatabase(config.db_path)
    db.initialize()
    handler = _handler_factory(db, config.export_dir)
    server = ThreadingHTTPServer((config.host, config.port), handler)
    local_url = f"http://127.0.0.1:{server.server_port}"
    print(f"Dashboard running at {local_url}")
    for lan_url in _lan_urls(server.server_port):
        print(f"LAN access URL: {lan_url}")
    if config.open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(local_url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()


def build_summary(db: StockDatabase) -> dict[str, object]:
    stocks = db.read_table("stocks")
    quotes = db.read_table("daily_quotes")
    finance = db.read_table("financial_indicators")
    valuations = db.read_table("valuations")
    latest_date = _latest_value(quotes, "trade_date")
    universe_status = market_data_status(db)
    return {
        "stock_count": int(len(stocks)),
        "active_count": int(len(stocks[(stocks.get("is_st", 0) == 0)]) if not stocks.empty else 0),
        "quote_count": int(len(quotes)),
        "universe_count": universe_status.universe_count,
        "quoted_universe_count": universe_status.quoted_symbol_count,
        "missing_universe_count": universe_status.missing_symbol_count,
        "finance_count": int(len(finance)),
        "valuation_count": int(len(valuations)),
        "latest_trade_date": latest_date,
        "latest_universe_trade_date": universe_status.latest_trade_date,
        "latest_report_date": _latest_value(finance, "report_date"),
        "latest_valuation_date": _latest_value(valuations, "trade_date"),
        "board_counts": _value_counts(stocks, "board"),
    }


def _lan_urls(port: int) -> list[str]:
    addresses: set[str] = set()
    try:
        host_name = socket.gethostname()
        for item in socket.getaddrinfo(host_name, None, socket.AF_INET):
            address = item[4][0]
            if not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass
    return [f"http://{address}:{port}" for address in sorted(addresses)]


def build_screen(db: StockDatabase, run_date: str, top: int) -> dict[str, object]:
    output = screen_stocks(db, run_date, ScreenConfig())
    rows = output.included.head(top)
    return {
        "run_date": run_date,
        "top": top,
        "rows": _records(rows),
        "excluded_count": int(len(output.excluded)),
    }


def build_ask(db: StockDatabase, query: str, run_date: str, top: int) -> dict[str, object]:
    spec = parse_ask(query)
    rows = evaluate_spec(db, spec, run_date).head(top)
    status = market_data_status(db)
    warning = None
    if spec.period_return_window and (status.missing_symbol_count > 0 or status.quoted_symbol_count < status.universe_count):
        warning = (
            f"本地行情库尚未覆盖全部股票：已覆盖{status.quoted_symbol_count}只，"
            f"缺少{status.missing_symbol_count}只。请先更新全市场行情。"
        )
    return {
        "query": query,
        "run_date": run_date,
        "top": top,
        "rows": _records(rows),
        "warning": warning,
        "market_status": _clean_record(status.__dict__),
    }


def start_dashboard_universe_market_update(db: StockDatabase, start: str, end: str, batch_size: int = 20) -> dict[str, object]:
    with _UNIVERSE_UPDATE_LOCK:
        if _UNIVERSE_UPDATE_PROGRESS.running:
            return {"started": False, "message": "全市场行情更新正在运行。", "progress": _progress_dict()}
        _UNIVERSE_UPDATE_CANCEL.clear()

    def run() -> None:
        def callback(progress: UniverseMarketProgress) -> None:
            global _UNIVERSE_UPDATE_PROGRESS
            with _UNIVERSE_UPDATE_LOCK:
                _UNIVERSE_UPDATE_PROGRESS = progress

        try:
            update_universe_market(
                db,
                BaostockProvider(),
                start,
                end,
                "qfq",
                batch_size=batch_size,
                progress_callback=callback,
                should_cancel=_UNIVERSE_UPDATE_CANCEL.is_set,
            )
        except Exception as exc:
            with _UNIVERSE_UPDATE_LOCK:
                current = _UNIVERSE_UPDATE_PROGRESS
                globals()["_UNIVERSE_UPDATE_PROGRESS"] = UniverseMarketProgress(
                    running=False,
                    total_symbols=current.total_symbols,
                    completed_symbols=current.completed_symbols,
                    skipped_symbols=current.skipped_symbols,
                    failed_symbols=current.failed_symbols,
                    current_batch=current.current_batch,
                    total_batches=current.total_batches,
                    quote_rows=current.quote_rows,
                    latest_trade_date=current.latest_trade_date,
                    started_at=current.started_at,
                    updated_at=datetime.now().isoformat(timespec="seconds"),
                    finished_at=datetime.now().isoformat(timespec="seconds"),
                    errors=tuple(list(current.errors) + [str(exc)]),
                )

    thread = threading.Thread(target=run, name="universe-market-update", daemon=True)
    thread.start()
    return {"started": True, "message": "全市场行情更新已启动。", "progress": _progress_dict()}


def cancel_dashboard_universe_market_update() -> dict[str, object]:
    with _UNIVERSE_UPDATE_LOCK:
        if not _UNIVERSE_UPDATE_PROGRESS.running:
            return {"cancelled": False, "message": "当前没有正在运行的全市场行情更新。", "progress": _progress_dict()}
        _UNIVERSE_UPDATE_CANCEL.set()
        return {"cancelled": True, "message": "已请求停止更新，当前股票处理完后会停下。", "progress": _progress_dict()}


def build_universe_update_status() -> dict[str, object]:
    return _progress_dict()


def build_similar_kline(db: StockDatabase, symbol: str, run_date: str, window: str, top: int) -> dict[str, object]:
    resolved = _resolve_symbol(db, symbol)
    if not resolved:
        return {"rows": [], "message": f"没有找到参考股票：{symbol}", "reference_symbol": symbol}
    return similar_kline(db, SimilarityRequest(reference_symbol=resolved, run_date=run_date, window=window, top=top))


def build_ai_parse(query: str) -> dict[str, object]:
    return parse_natural_query(query).to_dict()


def build_stock_insight(db: StockDatabase, symbol: str) -> dict[str, object]:
    resolved = _resolve_symbol(db, symbol) or _normalize_symbol(symbol)
    insight = fetch_stock_insight(db, resolved)
    payload = insight.to_dict()
    payload["ai_summary"] = summarize_stock_insight(insight)
    return payload


def build_stock_profile(
    db: StockDatabase,
    symbol: str,
    run_date: str | None = None,
    auto_update: bool = False,
) -> dict[str, object]:
    raw_symbol = symbol.strip()
    symbol = _normalize_symbol(raw_symbol)
    stocks = db.read_table("stocks", "symbol = ? OR ts_code = ? OR name = ?", (symbol, raw_symbol.upper(), raw_symbol))
    if stocks.empty:
        return {"found": False, "symbol": raw_symbol}
    symbol = str(stocks.iloc[0]["symbol"])
    update_status = None
    quote_where = "symbol = ?"
    params: tuple[object, ...] = (symbol,)
    if run_date:
        quote_where += " AND trade_date <= ?"
        params = (symbol, run_date)
    quotes = db.read_table("daily_quotes", quote_where, params).sort_values("trade_date")
    finance = db.read_table("financial_indicators", "symbol = ?", (symbol,)).sort_values("report_date")
    valuations = db.read_table("valuations", "symbol = ?", (symbol,)).sort_values("trade_date")
    if auto_update and _needs_stock_update(quotes, finance, valuations, run_date):
        update_status = _update_missing_stock_data(db, symbol, run_date)
        quotes = db.read_table("daily_quotes", quote_where, params).sort_values("trade_date")
        finance = db.read_table("financial_indicators", "symbol = ?", (symbol,)).sort_values("report_date")
        valuations = db.read_table("valuations", "symbol = ?", (symbol,)).sort_values("trade_date")
    latest_quote = _last_record(quotes)
    latest_finance = _last_record(finance)
    latest_valuation = _last_record(valuations)
    closes = pd.to_numeric(quotes.get("close", pd.Series(dtype=float)), errors="coerce").dropna()
    amounts = pd.to_numeric(quotes.get("amount", pd.Series(dtype=float)), errors="coerce").dropna()
    returns = {
        "return_20": _period_return(closes, 20),
        "return_60": _period_return(closes, 60),
        "return_120": _period_return(closes, 120),
        "drawdown_from_high": _drawdown(closes),
        "avg_amount_20": _float_or_none(amounts.tail(20).mean()) if not amounts.empty else None,
    }
    chart = quotes.tail(160)[["trade_date", "close", "amount"]].copy() if not quotes.empty else pd.DataFrame()
    return {
        "found": True,
        "stock": _clean_record(stocks.iloc[0].to_dict()),
        "latest_quote": latest_quote,
        "latest_finance": latest_finance,
        "latest_valuation": latest_valuation,
        "features": returns,
        "chart": _records(chart),
        "update_status": update_status,
    }


def _needs_stock_update(
    quotes: pd.DataFrame,
    finance: pd.DataFrame,
    valuations: pd.DataFrame,
    run_date: str | None,
) -> bool:
    if quotes.empty or finance.empty or valuations.empty:
        return True
    if not run_date or "trade_date" not in quotes:
        return False
    latest_trade_date = str(quotes["trade_date"].dropna().max())
    return latest_trade_date < run_date


def _update_missing_stock_data(db: StockDatabase, symbol: str, run_date: str | None) -> dict[str, object]:
    end = run_date or datetime.now().strftime("%Y%m%d")
    start = _history_start(end)
    status: dict[str, object] = {
        "attempted": True,
        "quote_rows": 0,
        "valuation_rows": 0,
        "finance_rows": 0,
        "errors": [],
    }
    provider = build_provider(db=db)
    try:
        quotes = provider.fetch_daily_quotes([symbol], start, end, "qfq")
        status["quote_rows"] = db.upsert_dataframe("daily_quotes", quotes, keys=("symbol", "trade_date", "adjust"))
    except Exception as exc:
        db.record_error("dashboard", "fetch_daily_quotes", exc, symbol=symbol)
        status["errors"].append(f"行情: {exc}")
    try:
        valuations = provider.fetch_valuations([symbol], start, end)
        status["valuation_rows"] = db.upsert_dataframe("valuations", valuations, keys=("symbol", "trade_date"))
    except Exception as exc:
        db.record_error("dashboard", "fetch_valuations", exc, symbol=symbol)
        status["errors"].append(f"估值: {exc}")
    try:
        finance = provider.fetch_financial_indicators([symbol])
        status["finance_rows"] = db.upsert_dataframe("financial_indicators", finance, keys=("symbol", "report_date"))
    except Exception as exc:
        db.record_error("dashboard", "fetch_financial_indicators", exc, symbol=symbol)
        status["errors"].append(f"财务: {exc}")
    return status


def _history_start(end: str) -> str:
    try:
        end_date = datetime.strptime(end, "%Y%m%d")
    except ValueError:
        end_date = datetime.now()
    return (end_date - timedelta(days=365 * 3)).strftime("%Y%m%d")


def export_screen(db: StockDatabase, run_date: str, top: int, export_dir: Path) -> dict[str, object]:
    output = screen_stocks(db, run_date, ScreenConfig())
    rows = output.included.head(top).copy()
    db.write_screen_results(run_date, rows)
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / f"dashboard_screen_{run_date}_top{top}.csv"
    rows.to_csv(path, index=False, encoding="utf-8-sig")
    return {"path": str(path), "row_count": int(len(rows))}


def export_stock_quotes(db: StockDatabase, symbol: str, export_dir: Path) -> dict[str, object]:
    resolved = _resolve_symbol(db, symbol)
    if not resolved:
        return {"path": None, "row_count": 0, "message": f"没有找到股票：{symbol}"}
    stocks = db.read_table("stocks", "symbol = ?", (resolved,))
    quotes = db.read_table("daily_quotes", "symbol = ?", (resolved,)).sort_values("trade_date")
    if not stocks.empty:
        quotes.insert(1, "name", str(stocks.iloc[0].get("name") or ""))
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / f"stock_quotes_{resolved}.csv"
    quotes.to_csv(path, index=False, encoding="utf-8-sig")
    return {"path": str(path), "row_count": int(len(quotes)), "symbol": resolved}


def _handler_factory(db: StockDatabase, export_dir: Path) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/":
                    self._send_text(INDEX_HTML, "text/html; charset=utf-8")
                elif parsed.path == "/app.js":
                    self._send_text(APP_JS, "application/javascript; charset=utf-8")
                elif parsed.path == "/styles.css":
                    self._send_text(STYLES_CSS, "text/css; charset=utf-8")
                elif parsed.path == "/api/summary":
                    self._send_json(build_summary(db))
                elif parsed.path == "/api/screen":
                    query = parse_qs(parsed.query)
                    self._send_json(build_screen(db, _date_param(query), _int_param(query, "top", 50)))
                elif parsed.path == "/api/ask":
                    query = parse_qs(parsed.query)
                    self._send_json(build_ask(db, _str_param(query, "query", ""), _date_param(query), _int_param(query, "top", 50)))
                elif parsed.path == "/api/update-universe-market":
                    query = parse_qs(parsed.query)
                    end = _date_param(query)
                    start = _str_param(query, "start", _history_start(end))
                    self._send_json(start_dashboard_universe_market_update(db, start, end, _int_param(query, "batch_size", 20)))
                elif parsed.path == "/api/update-universe-market-status":
                    self._send_json(build_universe_update_status())
                elif parsed.path == "/api/cancel-universe-market":
                    self._send_json(cancel_dashboard_universe_market_update())
                elif parsed.path == "/api/ai/parse-query":
                    query = parse_qs(parsed.query)
                    self._send_json(build_ai_parse(_str_param(query, "query", "")))
                elif parsed.path == "/api/similar-kline":
                    query = parse_qs(parsed.query)
                    self._send_json(
                        build_similar_kline(
                            db,
                            _str_param(query, "symbol", ""),
                            _date_param(query),
                            _str_param(query, "window", "1m"),
                            _int_param(query, "top", 30),
                        )
                    )
                elif parsed.path == "/api/stock/insight":
                    query = parse_qs(parsed.query)
                    self._send_json(build_stock_insight(db, _str_param(query, "symbol", "")))
                elif parsed.path == "/api/stock":
                    query = parse_qs(parsed.query)
                    self._send_json(
                        build_stock_profile(
                            db,
                            _str_param(query, "symbol", ""),
                            _str_param(query, "date", ""),
                            auto_update=_bool_param(query, "auto_update", True),
                        )
                    )
                elif parsed.path == "/api/export/screen":
                    query = parse_qs(parsed.query)
                    self._send_json(export_screen(db, _date_param(query), _int_param(query, "top", 50), export_dir))
                elif parsed.path == "/api/export/stock-quotes":
                    query = parse_qs(parsed.query)
                    self._send_json(export_stock_quotes(db, _str_param(query, "symbol", ""), export_dir))
                else:
                    self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _send_text(self, content: str, content_type: str | None = None) -> None:
            encoded = content.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type or mimetypes.types_map.get(".txt", "text/plain"))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return DashboardHandler


def _date_param(query: dict[str, list[str]]) -> str:
    value = _str_param(query, "date", "")
    if value:
        return value
    return datetime.now().strftime("%Y%m%d")


def _normalize_symbol(value: str) -> str:
    text = value.strip().upper()
    if "." in text:
        text = text.split(".", 1)[0]
    if text.startswith(("SH", "SZ", "BJ")):
        text = text[2:]
    digits = "".join(char for char in text if char.isdigit())
    return digits or text


def _resolve_symbol(db: StockDatabase, value: str) -> str | None:
    raw = value.strip()
    if not raw:
        return None
    symbol = _normalize_symbol(raw)
    stocks = db.read_table("stocks", "symbol = ? OR ts_code = ? OR name = ?", (symbol, raw.upper(), raw))
    if stocks.empty:
        return symbol if symbol.isdigit() and len(symbol) == 6 else None
    return str(stocks.iloc[0]["symbol"])


def _str_param(query: dict[str, list[str]], key: str, default: str) -> str:
    values = query.get(key)
    return values[0].strip() if values and values[0].strip() else default


def _int_param(query: dict[str, list[str]], key: str, default: int) -> int:
    try:
        return max(1, int(_str_param(query, key, str(default))))
    except ValueError:
        return default


def _bool_param(query: dict[str, list[str]], key: str, default: bool) -> bool:
    value = _str_param(query, key, str(default)).lower()
    return value not in {"0", "false", "no", "off"}


def _progress_dict() -> dict[str, object]:
    with _UNIVERSE_UPDATE_LOCK:
        return _clean_record(_UNIVERSE_UPDATE_PROGRESS.__dict__)


def _period_return(closes: pd.Series, window: int) -> float | None:
    if len(closes) <= window:
        return None
    base = closes.iloc[-window - 1]
    if not base:
        return None
    return _float_or_none(closes.iloc[-1] / base - 1)


def _drawdown(closes: pd.Series) -> float | None:
    if closes.empty:
        return None
    high = closes.max()
    latest = closes.iloc[-1]
    if not high:
        return None
    return _float_or_none(latest / high - 1)


def _latest_value(df: pd.DataFrame, column: str) -> object | None:
    if df.empty or column not in df:
        return None
    value = df[column].dropna().max()
    return None if pd.isna(value) else value


def _last_record(df: pd.DataFrame) -> dict[str, object] | None:
    if df.empty:
        return None
    return _clean_record(df.iloc[-1].to_dict())


def _value_counts(df: pd.DataFrame, column: str) -> dict[str, int]:
    if df.empty or column not in df:
        return {}
    return {str(key): int(value) for key, value in df[column].value_counts().items()}


def _records(df: pd.DataFrame) -> list[dict[str, object]]:
    return [_clean_record(row) for row in df.to_dict(orient="records")]


def _clean_record(row: dict[str, object]) -> dict[str, object]:
    return {key: _json_value(value) for key, value in row.items()}


def _json_value(value: object) -> object:
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _float_or_none(value: object) -> float | None:
    if pd.isna(value):
        return None
    return float(value)


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>A股量化分析系统</title>
  <link rel="stylesheet" href="/styles.css">
</head>
<body>
  <aside class="sidebar">
    <div class="brand">
      <div class="brand-mark">Q</div>
      <div>
        <h1>A股量化分析系统</h1>
        <p>本地数据 · 多因子评分 · 条件问诊</p>
      </div>
    </div>
    <nav>
      <button class="nav active" data-view="overview">总览</button>
      <button class="nav" data-view="screen">选股</button>
      <button class="nav" data-view="ask">问诊</button>
      <button class="nav" data-view="similar">相似K线</button>
      <button class="nav" data-view="stock">个股</button>
    </nav>
  </aside>
  <main>
    <header class="topbar">
      <div>
        <h2 id="view-title">总览</h2>
        <p id="view-subtitle">数据库状态与最近交易数据</p>
      </div>
      <div class="date-box">
        <label for="run-date">分析日期</label>
        <input id="run-date" inputmode="numeric" placeholder="20260531">
      </div>
    </header>

    <section id="overview" class="view active">
      <div class="metric-grid" id="metrics"></div>
      <div class="panel">
        <div class="panel-head">
          <h3>市场覆盖</h3>
          <div class="actions">
            <button id="update-universe">更新全市场行情</button>
            <button id="cancel-universe">停止更新</button>
            <button id="refresh-summary">刷新</button>
          </div>
        </div>
        <div id="board-counts" class="chips"></div>
        <p id="market-status" class="hint"></p>
      </div>
    </section>

    <section id="screen" class="view">
      <div class="toolbar">
        <label>数量 <input id="screen-top" type="number" min="1" value="50"></label>
        <button id="run-screen">运行评分</button>
        <button id="export-screen">导出CSV</button>
      </div>
      <div class="table-wrap"><table id="screen-table"></table></div>
    </section>

    <section id="ask" class="view">
      <div class="toolbar wide">
        <input id="ask-query" value="找和300750最近半年走势最像的股票">
        <button id="parse-ai">AI理解</button>
        <button id="run-ask">分析</button>
      </div>
      <p id="ask-status" class="hint"></p>
      <div class="table-wrap"><table id="ask-table"></table></div>
    </section>

    <section id="similar" class="view">
      <div class="toolbar">
        <input id="similar-symbol" value="300750" placeholder="参考股票，例如 300750 / 宁德时代">
        <select id="similar-window">
          <option value="1m">1个月</option>
          <option value="half_year" selected>半年</option>
          <option value="1y">1年</option>
        </select>
        <button id="run-similar">搜索相似K线</button>
      </div>
      <p id="similar-status" class="hint">使用本地行情库计算走势形状相似度。先更新全市场行情，结果会更完整。</p>
      <div class="table-wrap"><table id="similar-table"></table></div>
    </section>

    <section id="stock" class="view">
      <div class="toolbar">
        <input id="stock-symbol" value="300750" placeholder="例如 300750 / 300750.SZ / 宁德时代">
        <button id="run-stock">查看个股</button>
        <button id="export-stock-quotes">导出行情CSV</button>
      </div>
      <p id="stock-status" class="hint">默认示例：300750 宁德时代。首次查询未缓存股票时会自动联网补数据，可能需要几秒到几十秒。</p>
      <div id="stock-profile" class="stock-grid"></div>
      <canvas id="price-chart" height="220"></canvas>
      <div class="panel insight-panel">
        <div class="panel-head">
          <h3>AI基本面画像</h3>
          <button id="run-insight">分析财报/公告/新闻</button>
        </div>
        <p id="insight-status" class="hint">实时拉取公告、新闻、主营业务和行业信息；未配置 OPENAI_API_KEY 时使用本地摘要。</p>
        <div id="insight-result" class="insight"></div>
      </div>
    </section>
  </main>
  <script src="/app.js"></script>
</body>
</html>
"""


APP_JS = r"""const state = { summary: null, lastUniverseDone: 0, lastUniverseUpdatedAt: null };

const titles = {
  overview: ["总览", "数据库状态与最近交易数据"],
  screen: ["选股", "质量、估值、动量三因子综合评分"],
  ask: ["问诊", "用中文条件筛选股票池"],
  similar: ["相似K线", "按参考股票走势寻找形态相近的个股"],
  stock: ["个股", "查看价格、估值、财务与趋势特征"],
};

function qs(id) { return document.getElementById(id); }
function dateValue() {
  return qs("run-date").value.trim() || state.summary?.latest_trade_date || todayYmd();
}
function todayYmd() {
  const d = new Date();
  return `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}`;
}
async function api(path) {
  const res = await fetch(path);
  const payload = await res.json();
  if (!res.ok || payload.error) throw new Error(payload.error || "请求失败");
  return payload;
}
function fmt(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  if (typeof v === "number") return Math.abs(v) < 1 ? `${(v * 100).toFixed(1)}%` : v.toLocaleString("zh-CN", { maximumFractionDigits: digits });
  return v;
}
function score(v) {
  return v === null || v === undefined ? "-" : Number(v).toFixed(1);
}
function setStatus(message, type = "") {
  let el = qs("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    document.body.appendChild(el);
  }
  el.className = type;
  el.textContent = message;
  setTimeout(() => { el.textContent = ""; el.className = ""; }, 2800);
}

function switchView(view) {
  document.querySelectorAll(".view").forEach(el => el.classList.toggle("active", el.id === view));
  document.querySelectorAll(".nav").forEach(el => el.classList.toggle("active", el.dataset.view === view));
  qs("view-title").textContent = titles[view][0];
  qs("view-subtitle").textContent = titles[view][1];
}

async function loadSummary() {
  state.summary = await api("/api/summary");
  qs("run-date").value = state.summary.latest_trade_date || todayYmd();
  const metrics = [
    ["股票数", state.summary.stock_count],
    ["可用股票", state.summary.active_count],
    ["行情记录", state.summary.quote_count],
    ["行情覆盖", `${state.summary.quoted_universe_count}/${state.summary.universe_count}`],
    ["缺行情", state.summary.missing_universe_count],
    ["财务记录", state.summary.finance_count],
    ["最新交易日", state.summary.latest_trade_date],
    ["全市场最新", state.summary.latest_universe_trade_date],
  ];
  qs("metrics").innerHTML = metrics.map(([label, value]) => `<article><span>${label}</span><strong>${fmt(value, 0)}</strong></article>`).join("");
  const boards = state.summary.board_counts || {};
  qs("board-counts").innerHTML = Object.keys(boards).length
    ? Object.entries(boards).map(([k, v]) => `<span>${k}<b>${v}</b></span>`).join("")
    : "<p class='empty'>还没有股票基础数据。</p>";
  qs("market-status").textContent = `沪深主板+创业板行情覆盖 ${state.summary.quoted_universe_count || 0}/${state.summary.universe_count || 0}，缺少 ${state.summary.missing_universe_count || 0} 只。`;
}

async function runScreen() {
  const top = qs("screen-top").value || 50;
  const data = await api(`/api/screen?date=${encodeURIComponent(dateValue())}&top=${encodeURIComponent(top)}`);
  renderTable("screen-table", data.rows, [
    ["rank", "排名"], ["symbol", "代码"], ["name", "名称"], ["total_score", "总分", score],
    ["quality_score", "质量", score], ["valuation_score", "估值", score], ["momentum_score", "动量", score],
    ["close", "收盘"], ["roe", "ROE"], ["pe_ttm", "PE"], ["pb", "PB"], ["return_60", "60日收益"],
  ]);
}

async function runAsk() {
  const query = qs("ask-query").value.trim();
  const data = await api(`/api/ask?date=${encodeURIComponent(dateValue())}&top=80&query=${encodeURIComponent(query)}`);
  qs("ask-status").textContent = data.warning || `已按 ${data.run_date} 分析，返回 ${data.rows.length} 条结果。`;
  renderTable("ask-table", data.rows, [
    ["rank", "排名"], ["symbol", "代码"], ["name", "名称"], ["close", "收盘"],
    ["period_start_close", "20日前收盘"], ["period_return", "近20日跌幅"],
    ["latest_trade_date", "最新交易日"], ["drawdown_from_high", "高点回撤"], ["distance_to_weekly_ma60", "距60周线"],
    ["eps", "EPS"], ["pe_ttm", "PE"], ["pb", "PB"], ["score", "分数", score], ["reason", "原因"],
  ]);
}

async function parseAiQuery() {
  const query = qs("ask-query").value.trim();
  const parsed = await api(`/api/ai/parse-query?query=${encodeURIComponent(query)}`);
  qs("ask-status").textContent = `${parsed.ai_enabled ? "AI" : "规则"}理解：${parsed.explanation || parsed.task}`;
  if (parsed.task === "similar_kline" && parsed.reference_symbol) {
    qs("similar-symbol").value = parsed.reference_symbol;
    qs("similar-window").value = parsed.window || "1m";
    switchView("similar");
    await runSimilar();
  }
}

async function runSimilar() {
  const symbol = qs("similar-symbol").value.trim();
  const window = qs("similar-window").value;
  if (!symbol) {
    qs("similar-status").textContent = "先输入参考股票。";
    return;
  }
  qs("similar-status").textContent = `正在寻找 ${symbol} 的相似K线 ...`;
  const data = await api(`/api/similar-kline?symbol=${encodeURIComponent(symbol)}&window=${encodeURIComponent(window)}&date=${encodeURIComponent(dateValue())}&top=50`);
  qs("similar-status").textContent = data.message || `参考 ${data.reference_symbol}，窗口 ${data.window_days} 个交易日，返回 ${data.rows.length} 条结果。`;
  renderTable("similar-table", data.rows, [
    ["rank", "排名"], ["symbol", "代码"], ["name", "名称"], ["similarity", "相似度", score],
    ["match_start", "起始日"], ["match_end", "结束日"], ["period_return", "区间收益"],
    ["max_drawdown", "最大回撤"], ["amount_change", "成交额变化"], ["reason", "原因"],
  ]);
}

async function updateUniverseMarket() {
  const end = dateValue();
  const start = startYmd(end, 420);
  qs("market-status").textContent = `正在启动沪深主板+创业板行情库更新，时间范围 ${start}-${end} ...`;
  state.lastUniverseDone = 0;
  state.lastUniverseUpdatedAt = null;
  await api(`/api/update-universe-market?start=${encodeURIComponent(start)}&date=${encodeURIComponent(end)}&batch_size=20`);
  pollUniverseMarket();
}

async function cancelUniverseMarket() {
  const data = await api("/api/cancel-universe-market");
  qs("market-status").textContent = data.message || "已请求停止更新。";
  await pollUniverseMarket();
}

async function pollUniverseMarket() {
  const data = await api("/api/update-universe-market-status");
  const total = data.total_symbols || 0;
  const done = (data.completed_symbols || 0) + (data.skipped_symbols || 0) + (data.failed_symbols || 0);
  const pct = total ? `${Math.round(done / total * 100)}%` : "0%";
  const errors = data.errors?.length ? ` 最近错误：${data.errors[data.errors.length - 1]}` : "";
  const stuck = data.running && state.lastUniverseDone === done && state.lastUniverseUpdatedAt === data.updated_at && done > 0
    ? " 如果这里几分钟都不变，说明数据源正在等待响应；系统会在单只股票失败后继续。"
    : "";
  state.lastUniverseDone = done;
  state.lastUniverseUpdatedAt = data.updated_at;
  qs("market-status").textContent =
    `全市场行情更新${data.running ? "进行中" : "已停止"}：${done}/${total} (${pct})，` +
    `批次 ${data.current_batch || 0}/${data.total_batches || 0}，` +
    `新增/更新行情 ${data.quote_rows || 0} 行，失败 ${data.failed_symbols || 0} 只，` +
    `最新交易日 ${data.latest_trade_date || "-"}。${errors}${stuck}`;
  if (data.running) {
    setTimeout(() => pollUniverseMarket().catch(e => setStatus(e.message, "error")), 2500);
  } else {
    await loadSummary();
  }
}

function startYmd(end, days) {
  const d = new Date(`${end.slice(0, 4)}-${end.slice(4, 6)}-${end.slice(6, 8)}T00:00:00`);
  d.setDate(d.getDate() - days);
  return `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}`;
}

async function runStock() {
  const symbol = qs("stock-symbol").value.trim();
  if (!symbol) {
    qs("stock-status").textContent = "先输入股票代码或名称。";
    return;
  }
  qs("stock-status").textContent = `正在查询 ${symbol}。如果本地没数据，系统会自动补行情、财务和估值 ...`;
  const data = await api(`/api/stock?symbol=${encodeURIComponent(symbol)}&date=${encodeURIComponent(dateValue())}`);
  if (!data.found) {
    qs("stock-profile").innerHTML = "<p class='empty'>没有找到这只股票，请先更新基础数据。</p>";
    qs("stock-status").textContent = `没有找到 ${symbol}。可以试试 300750、600519、000001。`;
    drawChart([]);
    return;
  }
  const stock = data.stock;
  const quote = data.latest_quote || {};
  const fin = data.latest_finance || {};
  const val = data.latest_valuation || {};
  const features = data.features || {};
  qs("stock-profile").innerHTML = [
    ["名称", `${stock.name || "-"} ${stock.symbol || ""}`],
    ["收盘", fmt(quote.close)],
    ["20日收益", fmt(features.return_20)],
    ["60日收益", fmt(features.return_60)],
    ["高点回撤", fmt(features.drawdown_from_high)],
    ["20日均额", fmt(features.avg_amount_20, 0)],
    ["ROE", fmt(fin.roe)],
    ["净利率", fmt(fin.net_profit_margin)],
    ["PE", fmt(val.pe_ttm)],
    ["PB", fmt(val.pb)],
  ].map(([label, value]) => `<article><span>${label}</span><strong>${value}</strong></article>`).join("");
  const status = data.update_status;
  const updateText = status?.attempted
    ? ` 自动补数据：行情${status.quote_rows || 0}条，估值${status.valuation_rows || 0}条，财务${status.finance_rows || 0}条。`
    : "";
  const errorText = status?.errors?.length ? ` 部分数据源失败：${status.errors.slice(0, 1).join("；")}` : "";
  qs("stock-status").textContent = `已加载 ${stock.name || stock.symbol}，行情点 ${data.chart?.length || 0} 个。${updateText}${errorText}`;
  drawChart(data.chart || []);
}

async function runInsight() {
  const symbol = qs("stock-symbol").value.trim();
  if (!symbol) {
    qs("insight-status").textContent = "先输入股票代码或名称。";
    return;
  }
  qs("insight-status").textContent = `正在拉取 ${symbol} 的公告、新闻、主营业务和财务摘要 ...`;
  const data = await api(`/api/stock/insight?symbol=${encodeURIComponent(symbol)}`);
  const summary = data.ai_summary || {};
  const sections = [
    ["AI摘要", summary.summary || ""],
    ["优势", listText(summary.strengths)],
    ["风险", listText(summary.risks)],
    ["关注点", listText(summary.watch_items)],
    ["主营业务", compactRows(data.business, 5)],
    ["最新新闻", compactRows(data.news, 5)],
    ["最新公告", compactRows(data.notices, 5)],
    ["行业/概念", compactRows(data.industry, 5)],
  ];
  qs("insight-result").innerHTML = sections
    .filter(([, body]) => body)
    .map(([title, body]) => `<article><h4>${title}</h4><p>${body}</p></article>`)
    .join("") || "<p class='empty'>暂时没有拉到可展示的基本面数据。</p>";
  const errors = data.errors?.length ? ` 部分数据缺失：${data.errors.slice(0, 1).join("；")}` : "";
  qs("insight-status").textContent = `${summary.ai_enabled ? "AI已启用" : "AI未启用，使用本地摘要"}。${errors}`;
}

function listText(items) {
  return Array.isArray(items) ? items.filter(Boolean).join("；") : "";
}

function compactRows(rows, limit) {
  if (!Array.isArray(rows) || !rows.length) return "";
  return rows.slice(0, limit).map(row => {
    const vals = Object.values(row).filter(v => v !== null && v !== undefined && String(v).trim() !== "");
    return vals.slice(0, 3).join(" / ");
  }).join("；");
}

function renderTable(id, rows, columns) {
  const table = qs(id);
  if (!rows.length) {
    table.innerHTML = "<tbody><tr><td class='empty'>没有匹配结果。先更新数据，或调整日期/条件。</td></tr></tbody>";
    return;
  }
  const head = `<thead><tr>${columns.map(c => `<th>${c[1]}</th>`).join("")}</tr></thead>`;
  const body = `<tbody>${rows.map(row => `<tr>${columns.map(c => {
    const val = c[2] ? c[2](row[c[0]]) : fmt(row[c[0]]);
    return `<td>${val}</td>`;
  }).join("")}</tr>`).join("")}</tbody>`;
  table.innerHTML = head + body;
}

function drawChart(rows) {
  const canvas = qs("price-chart");
  const ctx = canvas.getContext("2d");
  const width = canvas.clientWidth;
  canvas.width = width * devicePixelRatio;
  canvas.height = 220 * devicePixelRatio;
  ctx.scale(devicePixelRatio, devicePixelRatio);
  ctx.clearRect(0, 0, width, 220);
  if (!rows.length) return;
  const values = rows.map(r => Number(r.close)).filter(v => Number.isFinite(v));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = 18;
  ctx.strokeStyle = "#d3d9e3";
  ctx.beginPath();
  ctx.moveTo(pad, pad);
  ctx.lineTo(pad, 200);
  ctx.lineTo(width - pad, 200);
  ctx.stroke();
  ctx.strokeStyle = "#1f8a70";
  ctx.lineWidth = 2;
  ctx.beginPath();
  values.forEach((v, i) => {
    const x = pad + (i / Math.max(values.length - 1, 1)) * (width - pad * 2);
    const y = 200 - ((v - min) / Math.max(max - min, 0.01)) * 170;
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.fillStyle = "#596579";
  ctx.fillText(`最高 ${max.toFixed(2)}  最低 ${min.toFixed(2)}`, pad, 14);
}

document.querySelectorAll(".nav").forEach(btn => btn.addEventListener("click", () => switchView(btn.dataset.view)));
qs("refresh-summary").addEventListener("click", () => loadSummary().catch(e => setStatus(e.message, "error")));
qs("update-universe").addEventListener("click", () => updateUniverseMarket().catch(e => setStatus(e.message, "error")));
qs("cancel-universe").addEventListener("click", () => cancelUniverseMarket().catch(e => setStatus(e.message, "error")));
qs("run-screen").addEventListener("click", () => runScreen().catch(e => setStatus(e.message, "error")));
qs("run-ask").addEventListener("click", () => runAsk().catch(e => setStatus(e.message, "error")));
qs("parse-ai").addEventListener("click", () => parseAiQuery().catch(e => setStatus(e.message, "error")));
qs("run-similar").addEventListener("click", () => runSimilar().catch(e => setStatus(e.message, "error")));
qs("run-stock").addEventListener("click", () => runStock().catch(e => setStatus(e.message, "error")));
qs("run-insight").addEventListener("click", () => runInsight().catch(e => setStatus(e.message, "error")));
qs("export-stock-quotes").addEventListener("click", async () => {
  try {
    const symbol = qs("stock-symbol").value.trim();
    const data = await api(`/api/export/stock-quotes?symbol=${encodeURIComponent(symbol)}`);
    setStatus(data.path ? `已导出 ${data.row_count} 行到 ${data.path}` : data.message);
  } catch (e) { setStatus(e.message, "error"); }
});
qs("stock-symbol").addEventListener("keydown", e => {
  if (e.key === "Enter") runStock().catch(err => setStatus(err.message, "error"));
});
qs("export-screen").addEventListener("click", async () => {
  try {
    const top = qs("screen-top").value || 50;
    const data = await api(`/api/export/screen?date=${encodeURIComponent(dateValue())}&top=${encodeURIComponent(top)}`);
    setStatus(`已导出 ${data.row_count} 行到 ${data.path}`);
  } catch (e) { setStatus(e.message, "error"); }
});

loadSummary().then(runScreen).then(runStock).catch(e => setStatus(e.message, "error"));
"""


STYLES_CSS = """*{box-sizing:border-box}body{margin:0;background:#f4f6f8;color:#18202b;font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;display:grid;grid-template-columns:260px 1fr;min-height:100vh}.sidebar{background:#111820;color:#fff;padding:24px 18px}.brand{display:flex;gap:12px;align-items:center;margin-bottom:32px}.brand-mark{width:42px;height:42px;border-radius:8px;background:#1f8a70;display:grid;place-items:center;font-weight:800}.brand h1{font-size:18px;margin:0}.brand p{margin:4px 0 0;color:#aeb8c5;font-size:12px}.nav{width:100%;border:0;background:transparent;color:#c9d2de;text-align:left;padding:12px 14px;border-radius:7px;margin:3px 0;cursor:pointer;font-size:15px}.nav.active,.nav:hover{background:#22303d;color:#fff}main{padding:24px;min-width:0}.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:22px}.topbar h2{margin:0;font-size:28px}.topbar p{margin:5px 0 0;color:#667285}.date-box{display:grid;gap:6px;color:#667285;font-size:12px}.date-box input,.toolbar input,.toolbar select{height:38px;border:1px solid #ccd4df;border-radius:7px;padding:0 12px;background:#fff}.view{display:none}.view.active{display:block}.metric-grid,.stock-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:16px}.metric-grid article,.stock-grid article,.panel{background:#fff;border:1px solid #dfe5ec;border-radius:8px;padding:16px}.metric-grid span,.stock-grid span{display:block;color:#667285;font-size:13px}.metric-grid strong,.stock-grid strong{display:block;margin-top:8px;font-size:24px}.panel-head,.toolbar{display:flex;align-items:center;gap:12px;justify-content:space-between}.actions{display:flex;gap:8px;flex-wrap:wrap}.toolbar{justify-content:flex-start;margin-bottom:12px}.toolbar.wide input{min-width:min(680px,100%);flex:1}.toolbar label{display:flex;gap:8px;align-items:center;color:#667285}.toolbar input[type=number]{width:90px}.hint{margin:0 0 12px;color:#667285;font-size:13px}button{height:38px;border:1px solid #bdc7d4;background:#fff;border-radius:7px;padding:0 14px;cursor:pointer}button:hover{background:#eef3f7}.panel h3{margin:0}.chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}.chips span{background:#eef3f7;border-radius:999px;padding:7px 10px}.chips b{margin-left:8px}.table-wrap{background:#fff;border:1px solid #dfe5ec;border-radius:8px;overflow:auto}table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:10px 12px;border-bottom:1px solid #edf1f5;text-align:left;white-space:nowrap}th{background:#f8fafc;color:#596579;font-weight:600}tr:hover td{background:#fbfcfd}.empty{color:#667285;padding:18px}canvas{width:100%;background:#fff;border:1px solid #dfe5ec;border-radius:8px;margin-top:12px}.insight-panel{margin-top:12px}.insight{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.insight article{border:1px solid #edf1f5;border-radius:8px;padding:12px;background:#fbfcfd}.insight h4{margin:0 0 6px;font-size:14px}.insight p{margin:0;color:#394557;line-height:1.55;font-size:13px}#toast{position:fixed;right:20px;bottom:20px;max-width:560px;background:#17202b;color:#fff;padding:12px 14px;border-radius:7px;box-shadow:0 8px 22px #0002}#toast:empty{display:none}#toast.error{background:#9f2d2d}@media(max-width:900px){body{grid-template-columns:1fr}.sidebar{position:static}.topbar{align-items:flex-start;gap:14px;flex-direction:column}.metric-grid,.stock-grid,.insight{grid-template-columns:1fr}.toolbar{flex-wrap:wrap}.panel-head{align-items:flex-start;flex-direction:column}}"""
