"""Testes de resolução do IP real e proteção contra spoof (F0-03)."""

from __future__ import annotations

import pytest
from starlette.requests import Request

from app.core.ip import get_client_ip, resolve_client_ip

TRUSTED = ["127.0.0.1/32", "10.0.0.0/8"]


def test_without_trusted_proxies_header_is_ignored() -> None:
    assert resolve_client_ip("203.0.113.5", "198.51.100.9", []) == "203.0.113.5"


def test_untrusted_peer_cannot_spoof_header() -> None:
    assert resolve_client_ip("203.0.113.5", "198.51.100.9", TRUSTED) == "203.0.113.5"


def test_trusted_peer_uses_forwarded_client() -> None:
    assert resolve_client_ip("127.0.0.1", "203.0.113.7", TRUSTED) == "203.0.113.7"


def test_chain_is_read_from_right_to_left() -> None:
    header = "198.51.100.7, 10.1.2.3"
    assert resolve_client_ip("127.0.0.1", header, TRUSTED) == "198.51.100.7"


def test_all_hops_trusted_returns_first_client() -> None:
    header = "10.1.2.3, 10.4.5.6"
    assert resolve_client_ip("127.0.0.1", header, TRUSTED) == "10.1.2.3"


def test_ipv6_is_normalized() -> None:
    assert resolve_client_ip("::1", "2001:0db8::1", ["::1/128"]) == "2001:db8::1"


def test_malformed_hop_fails_closed() -> None:
    header = "not-an-ip, 10.1.2.3"
    assert resolve_client_ip("127.0.0.1", header, TRUSTED) == "127.0.0.1"


def test_malformed_peer_fails_closed() -> None:
    assert resolve_client_ip("unknown", "203.0.113.7", TRUSTED) == "unknown"


def test_invalid_network_entries_are_ignored() -> None:
    assert resolve_client_ip("127.0.0.1", "203.0.113.7", ["not-a-cidr"]) == "127.0.0.1"


def test_empty_forwarded_for_ignored() -> None:
    assert resolve_client_ip("127.0.0.1", "", TRUSTED) == "127.0.0.1"
    assert resolve_client_ip("127.0.0.1", " , ", TRUSTED) == "127.0.0.1"


def _request(peer: str | None, forwarded: str | None = None) -> Request:
    headers = []
    if forwarded is not None:
        headers.append((b"x-forwarded-for", forwarded.encode("latin-1")))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": headers,
            "client": (peer, 1234) if peer is not None else None,
            "server": ("testserver", 80),
            "scheme": "http",
            "state": {},
        }
    )


def test_get_client_ip_uses_request_scope() -> None:
    assert get_client_ip(_request("127.0.0.1", "203.0.113.8"), TRUSTED) == "203.0.113.8"


def test_get_client_ip_without_client() -> None:
    assert get_client_ip(_request(None), TRUSTED) == "unknown"


@pytest.mark.parametrize("forwarded", [None, "", "203.0.113.8"])
def test_get_client_ip_untrusted_peer(forwarded: str | None) -> None:
    assert get_client_ip(_request("198.51.100.20", forwarded), TRUSTED) == "198.51.100.20"
