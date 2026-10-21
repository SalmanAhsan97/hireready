"""DNS fallback for hosts some ISPs block by name (e.g. brightdata.com returns NXDOMAIN on some Pakistani ISPs).

Only when the normal lookup fails for one of FALLBACK_HOSTS, the address is asked from Cloudflare's DNS-over-HTTPS
(by IP, so it needs no lookup itself). TLS still verifies the certificate against the real host name, so this changes
how the address is found, not who we talk to. On a server with working DNS (Railway) it never triggers.
"""
import json
import logging
import socket
import ssl
import time
import urllib.request

import certifi

log = logging.getLogger("screenproof.dns")

FALLBACK_HOSTS = {"api.brightdata.com", "brightdata.com"}
DOH = "https://1.1.1.1/dns-query?name={}&type=A"
TTL_S = 3600

_original = socket.getaddrinfo
_cache: dict[str, tuple[float, list[str]]] = {}


def _doh(host: str) -> list[str]:
    hit = _cache.get(host)
    if hit and time.time() - hit[0] < TTL_S:
        return hit[1]
    req = urllib.request.Request(DOH.format(host), headers={"accept": "application/dns-json"})
    with urllib.request.urlopen(req, timeout=8, context=ssl.create_default_context(cafile=certifi.where())) as resp:
        answers = json.load(resp).get("Answer") or []
    ips = [a["data"] for a in answers if a.get("type") == 1]
    if ips:
        _cache[host] = (time.time(), ips)
    return ips


def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    try:
        return _original(host, port, family, type, proto, flags)
    except socket.gaierror:
        name = (host.decode("idna") if isinstance(host, bytes) else str(host or "")).lower().rstrip(".")  # anyio passes bytes
        if name not in FALLBACK_HOSTS:
            raise
        try:
            ips = _doh(name)
        except (OSError, ValueError) as e:
            log.warning("DNS-over-HTTPS fallback for %s failed: %r", host, e)
            raise socket.gaierror(socket.EAI_NONAME, f"{host} not found")
        if not ips:
            raise
        log.info("Resolved %s via DNS-over-HTTPS", host)
        out = []
        for ip in ips:
            out += _original(ip, port, socket.AF_INET, type, proto, flags | socket.AI_NUMERICHOST)
        return out


def _anyio_patch():
    """httpx's async connections resolve through anyio, which uses the event loop's own resolver (uvloop's is native
    and never calls socket.getaddrinfo), so the fallback goes there too. Guarded: if anyio changes, there's no fallback."""
    import asyncio
    try:
        from anyio._backends._asyncio import AsyncIOBackend
    except ImportError:
        return
    original = AsyncIOBackend.getaddrinfo

    async def fallback_getaddrinfo(cls, host, port, *, family=0, type=0, proto=0, flags=0):
        try:
            return await original(host, port, family=family, type=type, proto=proto, flags=flags)
        except socket.gaierror:
            return await asyncio.to_thread(getaddrinfo, host, port, family, type, proto, flags)

    AsyncIOBackend.getaddrinfo = classmethod(fallback_getaddrinfo)


def install():
    socket.getaddrinfo = getaddrinfo
    try:
        _anyio_patch()
    except Exception as e:  # never stop the app over a lookup fallback
        log.warning("DNS fallback for async clients not installed: %r", e)
