"""Demo token mint — POST /demo/token.

A convenience for demo environments only: it mints a REAL OpenEMR OAuth2
access token via the password grant so a grader/nurse never has to run the
manual token dance. It is:

  * OFF by default — the route 404s unless DEMO_TOKEN_ENABLED is truthy, so it
    can never be reached in a normal deployment.
  * fail-closed on misconfiguration (missing client/creds -> 503, not a crash).
  * leak-free — the client secret and OpenEMR's raw error body never appear in
    the response.
"""
from __future__ import annotations

import httpx
import pytest

from app.demo_token import (
    DemoTokenConfig,
    DemoTokenMinter,
    get_demo_token_minter,
)


def _config(**overrides) -> DemoTokenConfig:
    base = dict(
        token_url="https://openemr.test/oauth2/default/token",
        client_id="client-abc",
        client_secret="secret-xyz",
        username="admin",
        password="test-password",
        scope="openid api:fhir user/Patient.read",
        clinician="nurse-maria",
        patient="a2390997-1e8c-4c41-99f5-676ad433d365",
    )
    base.update(overrides)
    return DemoTokenConfig(**base)


def _wire(client_app, minter: DemoTokenMinter) -> None:
    client_app.dependency_overrides[get_demo_token_minter] = lambda: minter


def test_disabled_by_default_returns_404(client, app):
    _wire(app, DemoTokenMinter(_config()))
    assert client.post("/demo/token").status_code == 404
    app.dependency_overrides.clear()


def test_enabled_but_unconfigured_returns_503(client, app, monkeypatch):
    monkeypatch.setenv("DEMO_TOKEN_ENABLED", "1")
    _wire(app, DemoTokenMinter(_config(client_id="", client_secret="")))
    assert client.post("/demo/token").status_code == 503
    app.dependency_overrides.clear()


def test_mints_token_via_password_grant(client, app, monkeypatch):
    monkeypatch.setenv("DEMO_TOKEN_ENABLED", "true")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"access_token": "tok-123", "expires_in": 3600})

    minter = DemoTokenMinter(_config(), transport=httpx.MockTransport(handler))
    _wire(app, minter)

    response = client.post("/demo/token")
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] == "tok-123"
    assert body["expires_in"] == 3600
    assert body["clinician"] == "nurse-maria"
    assert body["patient"] == "a2390997-1e8c-4c41-99f5-676ad433d365"

    # It really used the OpenEMR password grant.
    assert seen["url"] == "https://openemr.test/oauth2/default/token"
    assert "grant_type=password" in seen["body"]
    assert "scope=" in seen["body"]
    app.dependency_overrides.clear()


def test_never_leaks_client_secret(client, app, monkeypatch):
    monkeypatch.setenv("DEMO_TOKEN_ENABLED", "1")
    minter = DemoTokenMinter(
        _config(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"access_token": "tok-123"})
        ),
    )
    _wire(app, minter)
    assert "secret-xyz" not in client.post("/demo/token").text
    app.dependency_overrides.clear()


def test_openemr_rejection_maps_to_502(client, app, monkeypatch):
    monkeypatch.setenv("DEMO_TOKEN_ENABLED", "1")
    minter = DemoTokenMinter(
        _config(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(400, text="invalid_client: SQL blah PHI")
        ),
    )
    _wire(app, minter)
    response = client.post("/demo/token")
    assert response.status_code == 502
    # OpenEMR's raw body (which can carry SQL/PHI) must not be echoed back.
    assert "SQL" not in response.text
    app.dependency_overrides.clear()


def test_config_from_env_derives_token_url():
    config = DemoTokenConfig.from_env(
        {
            "OPENEMR_BASE_URL": "https://openemr-early-sub.up.railway.app/",
            "DEMO_OAUTH_CLIENT_ID": "cid",
            "DEMO_OAUTH_CLIENT_SECRET": "csec",
            "DEMO_OAUTH_PASSWORD": "pw",
        }
    )
    assert config.token_url == (
        "https://openemr-early-sub.up.railway.app/oauth2/default/token"
    )
    assert config.configured is True
