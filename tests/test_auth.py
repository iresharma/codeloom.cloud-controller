from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from codeloom_cloud.auth.github import GitHubAPI
from codeloom_cloud.crypto import decrypt_token, encrypt_token

from tests.helpers import auth_header, login


def test_me_requires_a_bearer_token(client):
    response = client.get("/me")
    assert response.status_code == 401


def test_login_redirects_and_callback_issues_a_session(client, github, settings):
    session_token, access = login(client, github)
    me = client.get("/me", headers=auth_header(session_token))
    assert me.status_code == 200
    body = me.json()
    assert body["login"] == "octocat"
    assert decrypt_token(_stored_token(client, settings), settings) == access


def test_callback_rejects_a_bad_state(client, github):
    github.add_user("gho_octocat", github_id=1, login="octocat")
    response = client.get(
        "/auth/github/callback",
        params={"code": "gho_octocat", "state": "not-a-token"},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_authorize_url_requests_repo_scope(settings):
    url = GitHubAPI(settings).authorize_url("state-1")
    query = parse_qs(urlparse(url).query)
    assert query["scope"] == ["read:user repo"]
    assert query["state"] == ["state-1"]
    assert query["client_id"] == ["cid"]


def test_token_round_trip(settings):
    encrypted = encrypt_token("gho_secret", settings)
    assert encrypted != "gho_secret"
    assert decrypt_token(encrypted, settings) == "gho_secret"


def test_oauth_body_keeps_a_refresh_token():
    from codeloom_cloud.auth.github import token_from_oauth_body

    grant = token_from_oauth_body(
        {"access_token": "gho_a", "refresh_token": "ghr_b", "expires_in": 28800, "token_type": "bearer"}
    )
    assert grant.access_token == "gho_a"
    assert grant.refresh_token == "ghr_b"
    assert grant.expires_in == 28800
    plain = token_from_oauth_body({"access_token": "gho_a", "scope": "repo"})
    assert plain.refresh_token is None
    assert plain.expires_in is None


def test_callback_stores_a_refresh_token(client, github, settings):
    from datetime import datetime, timezone

    from codeloom_cloud.auth.github import GitHubToken
    from codeloom_cloud.db import open_session
    from codeloom_cloud.models import User

    github.grants["gho_octocat"] = GitHubToken(
        access_token="gho_octocat",
        refresh_token="ghr_octocat",
        expires_in=28800,
    )
    login(client, github, access_token="gho_octocat")
    db = open_session()
    try:
        user = db.query(User).one()
        assert decrypt_token(user.refresh_token_encrypted, settings) == "ghr_octocat"
        expires = user.access_token_expires_at
        assert expires is not None
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        assert expires > datetime.now(timezone.utc)
    finally:
        db.close()


async def test_refresh_request_uses_the_refresh_grant(settings):
    import httpx

    from codeloom_cloud.auth.github import GitHubAPI

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={"access_token": "gho_new", "refresh_token": "ghr_new", "expires_in": 28800},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = GitHubAPI(settings, client=client)
    grant = await api.refresh_access_token("ghr_old")
    await client.aclose()
    assert grant.access_token == "gho_new"
    assert grant.refresh_token == "ghr_new"
    assert "grant_type=refresh_token" in seen["body"]
    assert "refresh_token=ghr_old" in seen["body"]


async def test_expired_github_token_is_refreshed(client, github, settings):
    from datetime import datetime, timedelta, timezone

    from codeloom_cloud.auth.credentials import ensure_access_token
    from codeloom_cloud.auth.github import GitHubToken
    from codeloom_cloud.db import open_session
    from codeloom_cloud.models import User

    _session_token, access = login(client, github)
    github.add_user("gho_new", github_id=1, login="octocat")
    github.refresh_grants["ghr_old"] = GitHubToken(
        access_token="gho_new",
        refresh_token="ghr_new",
        expires_in=28800,
    )
    db = open_session()
    try:
        user = db.query(User).one()
        user.refresh_token_encrypted = encrypt_token("ghr_old", settings)
        user.access_token_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
        token = await ensure_access_token(user, settings, github, db)
        assert token == "gho_new"
        assert decrypt_token(user.access_token_encrypted, settings) == "gho_new"
        assert decrypt_token(user.refresh_token_encrypted, settings) == "ghr_new"
        assert user.access_token_expires_at > datetime.now(timezone.utc)
    finally:
        db.close()


async def test_revoked_token_without_a_refresh_grant_asks_for_sign_in(client, github, settings):
    import pytest

    from codeloom_cloud.auth.credentials import ensure_access_token
    from codeloom_cloud.auth.github import GitHubAuthExpired
    from codeloom_cloud.db import open_session
    from codeloom_cloud.models import User

    _session_token, access = login(client, github)
    github.users.pop(access)
    db = open_session()
    try:
        user = db.query(User).one()
        with pytest.raises(GitHubAuthExpired, match="Sign in again"):
            await ensure_access_token(user, settings, github, db)
    finally:
        db.close()


def _stored_token(client, settings):
    from codeloom_cloud.db import open_session
    from codeloom_cloud.models import User

    db = open_session()
    try:
        user = db.query(User).one()
        return user.access_token_encrypted
    finally:
        db.close()
