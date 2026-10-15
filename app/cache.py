"""Small on-disk JSON cache for fetched postings and Claude extractions."""
import os
import time
from pathlib import Path

from pydantic import BaseModel

CACHE_DIR = Path(os.getenv("HIREREADY_CACHE", Path(__file__).resolve().parent.parent / ".cache"))


def get(kind: str, key: str, schema: type[BaseModel], max_age_s: float | None = None):
    path = CACHE_DIR / kind / f"{key}.json"
    try:
        if max_age_s is not None and time.time() - path.stat().st_mtime > max_age_s:
            return None
        return schema.model_validate_json(path.read_text())
    except (OSError, ValueError):
        return None


def put(kind: str, key: str, value: BaseModel):
    path = CACHE_DIR / kind / f"{key}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value.model_dump_json(indent=1))
    except OSError:
        pass
