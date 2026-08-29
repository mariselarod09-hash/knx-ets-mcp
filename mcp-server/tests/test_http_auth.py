"""Tests for the optional HTTP bearer-token auth on the client-facing transport."""

from __future__ import annotations

from knx_ets_mcp.server import _build_http_auth


def test_no_auth_without_token(monkeypatch) -> None:
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("MCP_TRANSPORT", "http")
    assert _build_http_auth() is None


def test_no_auth_for_stdio_even_with_token(monkeypatch) -> None:
    # Auth is an HTTP concept; stdio must never require a token.
    monkeypatch.setenv("MCP_AUTH_TOKEN", "secret")
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    assert _build_http_auth() is None


def test_auth_verifier_for_http_with_token(monkeypatch) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", "secret")
    monkeypatch.setenv("MCP_TRANSPORT", "http")
    verifier = _build_http_auth()
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
    assert isinstance(verifier, StaticTokenVerifier)


def test_auth_uses_http_default_transport(monkeypatch) -> None:
    # Transport defaults to http when MCP_TRANSPORT is unset, so a token enables auth.
    monkeypatch.setenv("MCP_AUTH_TOKEN", "secret")
    monkeypatch.delenv("MCP_TRANSPORT", raising=False)
    assert _build_http_auth() is not None
