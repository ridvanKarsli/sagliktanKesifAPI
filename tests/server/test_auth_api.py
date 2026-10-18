from __future__ import annotations


def test_login_and_me(client, admin_headers):
    r = client.get("/api/v1/auth/me", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["role"] == "admin"


def test_wrong_password(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@test.com", "password": "nope-nope-nope"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthenticated"
    assert r.headers["WWW-Authenticate"] == "Bearer"


def test_refresh(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@test.com", "password": "secretpass123"})
    pair = r.json()
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert r.status_code == 200 and r.json()["access_token"]
    # erişim belirteci yenileme için kullanılamaz
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": pair["access_token"]})
    assert r.status_code == 401


def test_viewer_cannot_mutate(client, viewer_headers):
    r = client.post("/api/v1/diseases", headers=viewer_headers, json={"name_en": "Gout"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"
    r = client.get("/api/v1/users", headers=viewer_headers)
    assert r.status_code == 403


def test_user_management(client, admin_headers):
    r = client.post("/api/v1/users", headers=admin_headers, json={"email": "a@test.com", "password": "analystpass1", "role": "analyst"})
    assert r.status_code == 201
    uid = r.json()["id"]
    r = client.post("/api/v1/users", headers=admin_headers, json={"email": "a@test.com", "password": "analystpass1", "role": "analyst"})
    assert r.status_code == 409
    r = client.patch(f"/api/v1/users/{uid}", headers=admin_headers, json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    r = client.post("/api/v1/auth/login", json={"email": "a@test.com", "password": "analystpass1"})
    assert r.status_code == 401
    r = client.post("/api/v1/users", headers=admin_headers, json={"email": "bad", "password": "x", "role": "viewer"})
    assert r.status_code == 422


def test_change_password(client, admin_headers):
    r = client.post("/api/v1/auth/password", headers=admin_headers, json={"current_password": "wrong-wrong", "new_password": "newsecretpass1"})
    assert r.status_code == 401
    r = client.post("/api/v1/auth/password", headers=admin_headers, json={"current_password": "secretpass123", "new_password": "newsecretpass1"})
    assert r.status_code == 200
    assert client.post("/api/v1/auth/login", json={"email": "admin@test.com", "password": "newsecretpass1"}).status_code == 200


def test_health_is_public(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["db"] is True


def test_request_id_header(client):
    r = client.get("/api/v1/health", headers={"X-Request-ID": "abc123"})
    assert r.headers["X-Request-ID"] == "abc123"
