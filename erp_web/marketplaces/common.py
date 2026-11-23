from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(os.environ.get("ERP_APP_DIR", str(Path(__file__).resolve().parents[2]))).expanduser().resolve()
MARKETPLACE_CACHE_DIR = APP_DIR / "data" / "cache" / "marketplaces"

__all__ = ["APP_DIR", "MARKETPLACE_CACHE_DIR"]
