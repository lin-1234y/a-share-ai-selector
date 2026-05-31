from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "stock.db"
DEFAULT_EXPORT_DIR = PROJECT_ROOT / "exports"


@dataclass(frozen=True)
class ScreenConfig:
    min_listing_days: int = 180
    min_avg_amount_20: float = 50_000_000.0
    quality_weight: float = 0.40
    valuation_weight: float = 0.30
    momentum_weight: float = 0.30
    allowed_boards: tuple[str, ...] = ("main", "chinext")
