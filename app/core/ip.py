"""Resolução do IP do cliente, respeitando apenas proxies confiáveis (RD-06)."""

from __future__ import annotations

from collections.abc import Sequence
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network

from starlette.requests import Request


def _parse_ip(value: str) -> str | None:
    try:
        return str(ip_address(value.strip()))
    except ValueError:
        return None


IPNetwork = IPv4Network | IPv6Network


def _parse_networks(trusted: Sequence[str]) -> list[IPNetwork]:
    networks: list[IPNetwork] = []
    for item in trusted:
        try:
            networks.append(ip_network(item, strict=False))
        except ValueError:
            continue
    return networks


def _is_trusted(address: str, networks: Sequence[IPNetwork]) -> bool:
    parsed = ip_address(address)
    return any(parsed in network for network in networks)


def resolve_client_ip(
    peer: str,
    forwarded_for: str | None,
    trusted_proxies: Sequence[str],
) -> str:
    """Retorna o IP do cliente a partir da conexão e de `X-Forwarded-For`.

    O cabeçalho só é considerado quando a conexão vem de um proxy confiável.
    A lista é percorrida da direita para a esquerda; o primeiro IP não confiável
    é o cliente. Qualquer valor malformado faz a função recair no IP da conexão
    (*fail closed*), evitando spoof.
    """
    peer_ip = _parse_ip(peer)
    if peer_ip is None:
        return peer

    networks = _parse_networks(trusted_proxies)
    if not forwarded_for or not networks or not _is_trusted(peer_ip, networks):
        return peer_ip

    hops = [hop.strip() for hop in forwarded_for.split(",") if hop.strip()]
    if not hops:
        return peer_ip

    for hop in reversed(hops):
        hop_ip = _parse_ip(hop)
        if hop_ip is None:
            return peer_ip
        if not _is_trusted(hop_ip, networks):
            return hop_ip

    first = _parse_ip(hops[0])
    return first if first is not None else peer_ip


def get_client_ip(request: Request, trusted_proxies: Sequence[str]) -> str:
    """IP do cliente para a requisição atual."""
    peer = request.client.host if request.client else "unknown"
    return resolve_client_ip(peer, request.headers.get("x-forwarded-for"), trusted_proxies)
