"""Environment settings. Names are SCREENPROOF_*; the pre-rename HIREREADY_* names still work as a fallback,
so an existing deploy keeps running until its variables are renamed."""
import os


def env(name: str, default: str = "") -> str:
    return os.getenv(f"SCREENPROOF_{name}", os.getenv(f"HIREREADY_{name}", default))
