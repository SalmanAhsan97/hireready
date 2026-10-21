import socket

import pytest

from app import dns_fallback


def test_blocked_host_resolves_via_doh(monkeypatch):
    def blocked(host, *a, **k):
        if host in ("api.brightdata.com", b"api.brightdata.com"):
            raise socket.gaierror(socket.EAI_NONAME, "blocked")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, a[0]))]
    monkeypatch.setattr(dns_fallback, "_original", blocked)
    monkeypatch.setattr(dns_fallback, "_doh", lambda host: ["203.0.113.7"])
    assert dns_fallback.getaddrinfo("api.brightdata.com", 443)[0][4] == ("203.0.113.7", 443)
    assert dns_fallback.getaddrinfo(b"api.brightdata.com", 443)[0][4] == ("203.0.113.7", 443)  # how anyio/httpx call it


def test_other_hosts_are_untouched(monkeypatch):
    def blocked(host, *a, **k):
        raise socket.gaierror(socket.EAI_NONAME, "nope")
    monkeypatch.setattr(dns_fallback, "_original", blocked)
    monkeypatch.setattr(dns_fallback, "_doh", lambda host: pytest.fail("DoH used for an unlisted host"))
    with pytest.raises(socket.gaierror):
        dns_fallback.getaddrinfo("example.com", 443)
