"""Tests run offline: no API keys, no Supabase, demo-mode AI, limits off.

Set before the app is imported: python-dotenv never overrides variables that already exist,
so these blank values keep a developer's local .env from leaking into tests.
"""
import os
import tempfile

for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "GEMINI_API_KEY", "BRIGHTDATA_API_KEY", "APIFY_API_TOKEN",
            "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SCREENPROOF_ACCESS_CODE"):
    os.environ[var] = ""
os.environ.update(
    SCREENPROOF_DEMO="1",
    SCREENPROOF_DAILY_LIMIT="0",
    SCREENPROOF_PREVIEW_LIMIT="0",
    SCREENPROOF_CHAT_LIMIT="0",
    SCREENPROOF_CACHE=tempfile.mkdtemp(prefix="screenproof-test-"),
)
