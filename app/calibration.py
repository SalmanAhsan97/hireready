"""Optional calibration: how often applications in each fit band got a callback, from real outcomes.

Outcomes come from imported historical applications (scored with the normal scorer; only the score is kept)
and from outcomes the owner records on saved runs. The built-in bands (80 / 65 / 45) stay in effect until a
band has at least CALIBRATION_MIN resolved outcomes. Only then may a boundary move (at most 5 points, only
when both sides have enough outcomes and the move separates callback rates clearly better) and the decision
text cite the band's observed callback rate. Anything missing or failing means the built-in bands.
"""
import logging
import time
from dataclasses import dataclass, field

from . import cache
from .scoring import BAND_CUTS, standing_for
from .settings import env

log = logging.getLogger("screenproof.calibration")

MIN_OUTCOMES = max(1, int(env("CALIBRATION_MIN", "30")))
CALLBACK = {"interview", "offer"}
NO_CALLBACK = {"rejected", "no_reply"}  # "applied" is still pending, so it doesn't count either way
MAX_SHIFT = 5
MIN_GAIN = 0.05  # a moved boundary must separate callback rates by 5 points more than the built-in one
REFRESH_S = 3600
BANDS = ("top candidate", "competitive", "possible", "long shot")


@dataclass(frozen=True)
class Bands:
    cuts: tuple[int, int, int] = BAND_CUTS
    version: str = "builtin"
    rates: dict = field(default_factory=dict)  # standing -> (resolved outcomes, callback rate) for bands with enough data

    @property
    def builtin(self) -> bool:
        return self.version == "builtin"


BUILTIN = Bands()


def _resolved(pairs: list[tuple[int, str]]) -> list[tuple[int, bool]]:
    return [(s, o in CALLBACK) for s, o in pairs if o in CALLBACK | NO_CALLBACK]


def report(pairs: list[tuple[int, str]], cuts: tuple[int, int, int] = BAND_CUTS) -> list[dict]:
    """Per band: score range, resolved outcomes, callbacks, callback rate, pending, and whether it has enough."""
    hi = {"top candidate": 100, "competitive": cuts[0] - 1, "possible": cuts[1] - 1, "long shot": cuts[2] - 1}
    lo = {"top candidate": cuts[0], "competitive": cuts[1], "possible": cuts[2], "long shot": 0}
    rows = []
    for band in BANDS:
        mine = [(s, o) for s, o in pairs if standing_for(s, cuts) == band]
        res = _resolved(mine)
        calls = sum(c for _, c in res)
        rows.append({"band": band, "range": f"{lo[band]}–{hi[band]}", "outcomes": len(res), "callbacks": calls,
                     "rate": round(100 * calls / len(res)) if res else None,
                     "pending": sum(o == "applied" for _, o in mine), "enough": len(res) >= MIN_OUTCOMES})
    return rows


def _rate(items: list[tuple[int, bool]]) -> float:
    return sum(c for _, c in items) / len(items)


def fit(pairs: list[tuple[int, str]]) -> Bands:
    """Bands to use for these outcomes: built-in unless a band has at least MIN_OUTCOMES resolved outcomes."""
    rows = report(pairs)
    if not any(r["enough"] for r in rows):
        return BUILTIN
    res = _resolved(pairs)
    cuts = list(BAND_CUTS)
    for i in range(3):  # boundary between the band above cut i and the one below it
        upper = cuts[i - 1] if i else 101
        lower = cuts[i + 1] if i < 2 else 0

        def split(c: int):
            above = [x for x in res if c <= x[0] < upper]
            below = [x for x in res if lower <= x[0] < c]
            if len(above) < MIN_OUTCOMES or len(below) < MIN_OUTCOMES:
                return None
            return _rate(above) - _rate(below)

        base = split(cuts[i])
        best, best_gap = cuts[i], base
        for c in (cuts[i] - MAX_SHIFT, cuts[i] + MAX_SHIFT):
            if not (lower < c < upper):
                continue
            gap = split(c)
            if base is not None and gap is not None and gap > best_gap and gap - base >= MIN_GAIN:
                best, best_gap = c, gap
        cuts[i] = best
    cuts_t = (cuts[0], cuts[1], cuts[2])
    final = report(pairs, cuts_t)
    rates = {r["band"]: (r["outcomes"], r["rate"]) for r in final if r["enough"]}
    n = len(res)
    version = f"calibrated {'/'.join(map(str, cuts_t))} from {n} outcomes"
    return Bands(cuts=cuts_t, version=version, rates=rates)


async def outcome_pairs() -> list[tuple[int, str]]:
    """(score, outcome) from imported applications and from outcomes recorded on saved job-match runs."""
    pairs = []
    for r in await cache.data_rows("outcomes", select="score,outcome", limit=10000):
        if isinstance(r.get("score"), int) and r.get("outcome"):
            pairs.append((r["score"], r["outcome"]))
    try:
        history = await cache.list_history(limit=1000)
    except Exception as e:
        log.warning("Saved runs unavailable for calibration: %r", e)
        history = []
    for h in history:
        score, outcome = h.get("overall_score"), h.get("outcome")
        if h.get("mode") == "job" and outcome in cache.OUTCOMES and score is not None:
            try:
                pairs.append((int(score), outcome))
            except (TypeError, ValueError):
                pass
    return pairs


_active: tuple[float, Bands] | None = None


async def active(force: bool = False) -> Bands:
    """The bands in effect, refreshed hourly. Any failure keeps the built-in bands."""
    global _active
    if _active and not force and time.time() - _active[0] < REFRESH_S:
        return _active[1]
    try:
        bands = fit(await outcome_pairs())
    except Exception as e:
        log.warning("Calibration unavailable, using built-in bands: %r", e)
        bands = BUILTIN
    if _active is None or _active[1].version != bands.version:
        log.info("Fit bands in effect: %s", bands.version)
    _active = (time.time(), bands)
    return bands


def wording(standing: str, bands: Bands) -> str:
    """A sentence for the decision, only for a band with enough outcomes behind it."""
    if bands.builtin or standing not in bands.rates:
        return ""
    n, rate = bands.rates[standing]
    return f" Past applications in this band got a callback {rate}% of the time ({n} outcomes)."
