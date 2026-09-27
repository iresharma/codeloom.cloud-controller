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


def _stored_token(client, settings):
    from codeloom_cloud.db import SessionLocal
    from codeloom_cloud.models import User

    db = SessionLocal()
    try:
        user = db.query(User).one()
        return user.access_token_encrypted
    finally:
        db.close()
