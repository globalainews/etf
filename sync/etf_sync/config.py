from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class SyncConfig:
    db_path: Path
    env_id: str = ""
    api_key: str = ""
    timeout_seconds: float = 20.0
    max_retries: int = 3
    batch_size: int = 80
    history_days: int = 90
    correction_lookback_days: int = 10
    anomaly_window: int = 20
    anomaly_min_history: int = 20
    anomaly_ratio_threshold: float = 3.0
    anomaly_abs_pct_threshold: float = 1.0
    schema_version: str = "1.0"

    @classmethod
    def from_env(cls, db_path: str | Path | None = None) -> "SyncConfig":
        root = Path(__file__).resolve().parents[2]
        return cls(
            db_path=Path(db_path or os.getenv("ETF_DB_PATH", root / "etf.db")).resolve(),
            env_id=os.getenv("CLOUDBASE_ENV_ID", "").strip(),
            api_key=os.getenv("CLOUDBASE_API_KEY", "").strip(),
            timeout_seconds=float(os.getenv("CLOUDBASE_TIMEOUT_SECONDS", "20")),
            max_retries=int(os.getenv("CLOUDBASE_MAX_RETRIES", "3")),
            batch_size=int(os.getenv("CLOUDBASE_BATCH_SIZE", "80")),
        )

