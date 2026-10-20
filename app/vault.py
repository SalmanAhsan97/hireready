"""Owner-only fact vault: verified facts, achievement figures with their verification status, and claims that
were removed or corrected (e.g. no "4x ROAS" on Social Media Manager variants; Canadian PR stated once).

Read only when the signed-in owner generates an application kit or a chat edit (ai.owner_request()), to flag
figures and claims in the generated text that the vault marks unverified or removed. Visitors' requests never
read it, and an empty vault (or Supabase down) adds no warnings.
"""
import logging
import re
import time

from pydantic import ValidationError

from . import cache
from .library import matches
from .models import ImportVaultItem

log = logging.getLogger("screenproof.vault")

MEMO_S = 300
_memo: tuple[float, list[ImportVaultItem]] | None = None
FLAG = ("unverified", "removed")


async def entries() -> list[ImportVaultItem]:
    global _memo
    if _memo and time.time() - _memo[0] < MEMO_S:
        return _memo[1]
    out = []
    for r in await cache.data_rows("vault", limit=2000):
        try:
            out.append(ImportVaultItem.model_validate(r))
        except ValidationError:
            continue
    _memo = (time.time(), out)
    return out


def _applies(e: ImportVaultItem, role: str) -> bool:
    return not e.applies_to.strip() or matches(role, e.applies_to)


def _pattern(phrase: str) -> re.Pattern | None:
    phrase = " ".join(phrase.split())
    if not phrase:
        return None
    body = r"\s+".join(re.escape(w) for w in phrase.split())
    return re.compile(rf"(?<![\w.]){body}(?![\w])", re.I)


PR = re.compile(r"\bPR\b|permanent residen(?:t|cy)", re.I)


def _mentions(claim: str, text: str) -> int:
    """How often a claim is stated: the phrase itself, or for permanent residency any PR wording."""
    if PR.search(claim):
        return len(PR.findall(text))
    pat = _pattern(claim)
    return len(pat.findall(text)) if pat else 0


def flagged_figures(text: str, vault: list[ImportVaultItem], role: str = "") -> list[str]:
    """Figures in `text` that the vault marks unverified or removed for this role (even if a resume has them)."""
    out = []
    for e in vault:
        if e.figure.strip() and e.status in FLAG and _applies(e, role):
            pat = _pattern(e.figure)
            if pat and pat.search(text):
                out.append(e.figure.strip())
    return sorted(set(out))


def claim_warnings(text: str, vault: list[ImportVaultItem], role: str = "") -> list[str]:
    out = []
    for e in vault:
        if not _applies(e, role):
            continue
        scope = f" for {e.applies_to}" if e.applies_to.strip() else ""
        if e.kind == "limit" and e.max_mentions is not None:
            n = _mentions(e.claim, text)
            if n > e.max_mentions:
                out.append(f"Fact vault: state “{e.claim}” at most {e.max_mentions} time(s){scope}; it appears {n} times.")
        elif e.kind in ("removed", "corrected") and e.status in FLAG and not e.figure.strip() and _mentions(e.claim, text):
            fix = f" Use: {e.correction}" if e.correction.strip() else ""
            out.append(f"Fact vault: “{e.claim}” was {'removed' if e.kind == 'removed' else 'corrected'}{scope}.{fix}")
    return out


def warnings(label: str, text: str, vault: list[ImportVaultItem], role: str = "") -> list[str]:
    """Everything the vault flags in one piece of generated text, worded for the kit/chat warning list."""
    if not vault:
        return []
    out = []
    figures = flagged_figures(text, vault, role)
    if figures:
        out.append(f"Check the {label}: your fact vault marks these figures unverified or removed: " + ", ".join(figures))
    out += claim_warnings(text, vault, role)
    return out
