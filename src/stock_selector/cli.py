from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Sequence

import pandas as pd

from .config import DEFAULT_DB_PATH, DEFAULT_EXPORT_DIR, ScreenConfig
from .dashboard import DashboardConfig, serve_dashboard
from .providers import build_provider
from .queries import run_ask
from .storage import StockDatabase
from .strategy import screen_stocks
from .universe import update_universe_market


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "serve":
        serve_dashboard(
            DashboardConfig(
                db_path=args.db,
                export_dir=args.export_dir,
                host=args.host,
                port=args.port,
                open_browser=args.open,
            )
        )
        return

    db = StockDatabase(args.db)
    db.initialize()
    provider = build_provider(db=db)

    if args.command == "update-basic":
        count = update_basic(db, provider)
        print(f"Updated {count} stock records.")
    elif args.command == "update-market":
        symbols = _symbols_from_args(args.symbols) or db.active_symbols()
        count = update_market(db, provider, symbols, args.start, args.end, args.adjust)
        print(f"Updated {count} daily quote records.")
    elif args.command == "update-universe-market":
        result = update_universe_market(db, provider, args.start, args.end, args.adjust)
        if result.errors:
            print("; ".join(result.errors))
        print(
            "Updated "
            f"{result.quote_rows} quote rows and {result.valuation_rows} valuation rows "
            f"for {result.symbol_count} universe symbols. Latest trade date: {result.latest_trade_date}"
        )
    elif args.command == "update-finance":
        symbols = _symbols_from_args(args.symbols) or db.active_symbols()
        count = update_finance(db, provider, symbols)
        print(f"Updated {count} financial indicator records.")
    elif args.command == "screen":
        path = run_screen(db, args.date, args.top, args.export_dir)
        print(f"Exported screen results to {path}")
    elif args.command == "ask":
        path = run_ask(db, args.query, args.date, args.top, args.export_dir)
        print(f"Exported ask results to {path}")
    elif args.command == "update-all":
        end = args.end or datetime.now().strftime("%Y%m%d")
        start = args.start or (datetime.now() - timedelta(days=240)).strftime("%Y%m%d")
        update_basic(db, provider)
        symbols = _symbols_from_args(args.symbols) or db.active_symbols()
        quote_count = update_market(db, provider, symbols, start, end, args.adjust)
        finance_count = update_finance(db, provider, symbols)
        path = run_screen(db, end, args.top, args.export_dir)
        print(f"Updated {quote_count} quotes, {finance_count} financial rows, exported {path}")
    else:
        parser.print_help()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A-share data collector and stock screener")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite database path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("update-basic", help="Update stock universe and basic information")

    market = subparsers.add_parser("update-market", help="Update daily market quotes")
    market.add_argument("--start", required=True, help="Start date, e.g. 20250101")
    market.add_argument("--end", required=True, help="End date, e.g. 20250531")
    market.add_argument("--adjust", default="qfq", choices=["", "qfq", "hfq"], help="Adjustment mode for AKShare")
    market.add_argument("--symbols", default="", help="Comma-separated stock symbols; defaults to active universe")

    universe_market = subparsers.add_parser(
        "update-universe-market",
        help="Update daily quotes for the main-board and ChiNext universe",
    )
    universe_market.add_argument("--start", required=True, help="Start date, e.g. 20250101")
    universe_market.add_argument("--end", required=True, help="End date, e.g. 20260531")
    universe_market.add_argument("--adjust", default="qfq", choices=["", "qfq", "hfq"], help="Adjustment mode for AKShare")

    finance = subparsers.add_parser("update-finance", help="Update financial indicators")
    finance.add_argument("--symbols", default="", help="Comma-separated stock symbols; defaults to active universe")

    screen = subparsers.add_parser("screen", help="Run scoring strategy and export candidates")
    screen.add_argument("--date", required=True, help="Screen date, e.g. 20250531")
    screen.add_argument("--top", type=int, default=50, help="Number of candidates to export")
    screen.add_argument("--export-dir", type=Path, default=DEFAULT_EXPORT_DIR, help="CSV export directory")

    ask = subparsers.add_parser("ask", help="Run a simple Chinese natural-language stock query")
    ask.add_argument("query", help="Query text, e.g. 历史高点回撤30%以上的非ST非亏损股")
    ask.add_argument("--date", required=True, help="Query date, e.g. 20260531")
    ask.add_argument("--top", type=int, default=50, help="Number of results to export")
    ask.add_argument("--export-dir", type=Path, default=DEFAULT_EXPORT_DIR, help="CSV export directory")

    all_cmd = subparsers.add_parser("update-all", help="Run basic, market, finance, and screen in sequence")
    all_cmd.add_argument("--start", default="", help="Start date; defaults to 240 calendar days before today")
    all_cmd.add_argument("--end", default="", help="End date; defaults to today")
    all_cmd.add_argument("--top", type=int, default=50, help="Number of candidates to export")
    all_cmd.add_argument("--adjust", default="qfq", choices=["", "qfq", "hfq"], help="Adjustment mode for AKShare")
    all_cmd.add_argument("--symbols", default="", help="Comma-separated stock symbols; defaults to active universe")
    all_cmd.add_argument("--export-dir", type=Path, default=DEFAULT_EXPORT_DIR, help="CSV export directory")

    serve = subparsers.add_parser("serve", help="Start the local web dashboard")
    serve.add_argument("--host", default="0.0.0.0", help="Dashboard host; 0.0.0.0 allows phone access on the same Wi-Fi")
    serve.add_argument("--port", type=int, default=8765, help="Dashboard port")
    serve.add_argument("--export-dir", type=Path, default=DEFAULT_EXPORT_DIR, help="CSV export directory")
    serve.add_argument("--open", action="store_true", help="Open the dashboard in the default browser")
    return parser


def update_basic(db: StockDatabase, provider) -> int:
    stocks = provider.fetch_stock_basic()
    if stocks.empty:
        return 0
    return db.upsert_dataframe("stocks", stocks, keys=("symbol",))


def update_market(
    db: StockDatabase,
    provider,
    symbols: Sequence[str],
    start: str,
    end: str,
    adjust: str = "qfq",
) -> int:
    if not symbols:
        return 0
    quotes = provider.fetch_daily_quotes(symbols, start, end, adjust)
    quote_count = db.upsert_dataframe("daily_quotes", quotes, keys=("symbol", "trade_date", "adjust"))
    try:
        valuations = provider.fetch_valuations(symbols, start, end)
    except Exception as valuation_exc:
        db.record_error("composite", "fetch_valuations", valuation_exc)
    else:
        db.upsert_dataframe("valuations", valuations, keys=("symbol", "trade_date"))
    return quote_count


def update_finance(db: StockDatabase, provider, symbols: Sequence[str]) -> int:
    if not symbols:
        return 0
    finance = provider.fetch_financial_indicators(symbols)
    return db.upsert_dataframe("financial_indicators", finance, keys=("symbol", "report_date"))


def run_screen(db: StockDatabase, run_date: str, top: int, export_dir: Path) -> Path:
    output = screen_stocks(db, run_date, ScreenConfig())
    included = output.included.head(top).copy()
    db.write_screen_results(run_date, included)
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / f"screen_{run_date}_top{top}.csv"
    included.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def _symbols_from_args(value: str | Sequence[str]) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        values = [value]
    else:
        values = list(value)
    symbols: list[str] = []
    for item in values:
        symbols.extend(part.strip() for part in str(item).split(",") if part.strip())
    return symbols
