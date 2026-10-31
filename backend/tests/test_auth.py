"""Unified login, role-based routing & access, master admin role management, forgot password."""
import re

from tests.conftest import ADMIN_EMAIL, ADMIN_PASS, OPERATOR_EMAIL, OPERATOR_PASS, PASSENGER_EMAIL, PASSENGER_PASS, login


def test_everything_except_health_and_auth_requires_sign_in(client):
    assert client.get("/api/health").status_code == 200
    for path in ("/api/routes", "/api/stops", "/api/search?q=a", "/api/buses", "/api/operator/reports",
                 "/api/admin/users", "/api/auth/me"):
        assert client.get(path).status_code == 401, path


def test_register_creates_passenger_and_routes_to_passenger_view(client):
    res = client.post("/api/auth/register", json={"email": " Aysel@Example.com ", "password": PASSENGER_PASS})
    assert res.status_code == 201
    body = res.json()
    assert (body["email"], body["role"], body["home"]) == (PASSENGER_EMAIL, "passenger", "/passenger.html")
    assert "httponly" in res.headers["set-cookie"].lower()
    assert client.get("/api/auth/me").json()["role"] == "passenger"
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_one_login_screen_routes_each_role_to_its_dashboard(new_client, passenger, operator):
    assert login(new_client(), PASSENGER_EMAIL, PASSENGER_PASS)["home"] == "/passenger.html"
    assert login(new_client(), OPERATOR_EMAIL, OPERATOR_PASS)["home"] == "/operator.html"
    admin = login(new_client(), ADMIN_EMAIL, ADMIN_PASS)
    assert (admin["role"], admin["home"]) == ("admin", "/operator.html")


def test_validation_duplicates_wrong_password_and_throttling(client):
    assert client.post("/api/auth/register", json={"email": "nope", "password": PASSENGER_PASS}).status_code == 422
    assert client.post("/api/auth/register", json={"email": "a@b.az", "password": "short"}).status_code == 422
    client.post("/api/auth/register", json={"email": "a@b.az", "password": PASSENGER_PASS})
    assert client.post("/api/auth/register", json={"email": "a@b.az", "password": PASSENGER_PASS}).status_code == 409
    codes = [client.post("/api/auth/login", json={"email": "a@b.az", "password": "wrong"}).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429


def test_role_based_access(passenger, operator, admin, report_payload):
    # passenger: no operator or admin areas
    for path in ("/api/operator/reports", "/api/operator/density", "/api/operator/stream", "/api/admin/users"):
        assert passenger.get(path).status_code == 403, path
    # operator: operations yes, passenger reporting and user management no
    assert operator.get("/api/operator/reports").status_code == 200
    assert operator.post("/api/passenger/reports", json=report_payload()).status_code == 403
    assert operator.get("/api/admin/users").status_code == 403
    # master admin: user management and operations
    assert admin.get("/api/admin/users").status_code == 200
    assert admin.get("/api/operator/density").status_code == 200
    assert admin.post("/api/passenger/reports", json=report_payload()).status_code == 403


def test_master_admin_lists_users_and_assigns_operator_role(new_client, admin, passenger):
    users = admin.get("/api/admin/users").json()
    assert {u["email"]: u["role"] for u in users} == {ADMIN_EMAIL: "admin", PASSENGER_EMAIL: "passenger"}
    assert [u["email"] for u in admin.get("/api/admin/users", params={"q": "AYSEL"}).json()] == [PASSENGER_EMAIL]

    user_id = next(u["id"] for u in users if u["email"] == PASSENGER_EMAIL)
    res = admin.patch(f"/api/admin/users/{user_id}", json={"role": "operator"})
    assert res.status_code == 200 and res.json()["role"] == "operator"

    # the promoted user is signed out, and the next login opens the operator dashboard
    assert passenger.get("/api/auth/me").status_code == 401
    assert login(passenger, PASSENGER_EMAIL, PASSENGER_PASS)["home"] == "/operator.html"
    assert passenger.get("/api/operator/reports").status_code == 200

    # and back to passenger
    admin.patch(f"/api/admin/users/{user_id}", json={"role": "passenger"})
    assert login(passenger, PASSENGER_EMAIL, PASSENGER_PASS)["home"] == "/passenger.html"


def test_master_admin_guard_rails(admin, passenger):
    admin_id = next(u["id"] for u in admin.get("/api/admin/users").json() if u["role"] == "admin")
    user_id = next(u["id"] for u in admin.get("/api/admin/users").json() if u["role"] == "passenger")
    assert admin.patch(f"/api/admin/users/{admin_id}", json={"role": "operator"}).status_code == 409
    assert admin.patch(f"/api/admin/users/{user_id}", json={"role": "admin"}).status_code == 422
    assert admin.patch("/api/admin/users/9999", json={"role": "operator"}).status_code == 404
    assert admin.patch(f"/api/admin/users/{user_id}", json={"role": "superuser"}).status_code == 422


def _code(mailer, email):
    mail = [m for m in mailer.outbox if m.to == email][-1]
    return re.search(r"\b(\d{6})\b", mail.body).group(1)


def test_forgot_password_flow(new_client, passenger, mailer):
    c = new_client()
    res = c.post("/api/auth/forgot-password", json={"email": PASSENGER_EMAIL})
    assert res.status_code == 202
    code = _code(mailer, PASSENGER_EMAIL)

    wrong = "000000" if code != "000000" else "111111"
    body = {"email": PASSENGER_EMAIL, "code": wrong, "new_password": "brand-new-pass"}
    assert c.post("/api/auth/reset-password", json=body).status_code == 400
    assert c.post("/api/auth/reset-password", json={**body, "code": code}).status_code == 204

    assert passenger.get("/api/auth/me").status_code == 401, "existing sessions are revoked"
    assert c.post("/api/auth/login", json={"email": PASSENGER_EMAIL, "password": PASSENGER_PASS}).status_code == 401
    assert login(c, PASSENGER_EMAIL, "brand-new-pass")["role"] == "passenger"
    assert c.post("/api/auth/reset-password", json={**body, "code": code}).status_code == 400, "codes are single use"


def test_forgot_password_does_not_reveal_accounts(client, mailer):
    known = client.post("/api/auth/register", json={"email": "known@example.com", "password": PASSENGER_PASS})
    assert known.status_code == 201
    a = client.post("/api/auth/forgot-password", json={"email": "known@example.com"})
    b = client.post("/api/auth/forgot-password", json={"email": "unknown@example.com"})
    assert (a.status_code, a.json()) == (b.status_code, b.json())
    assert [m.to for m in mailer.outbox] == ["known@example.com"]


def test_reset_codes_expire_lock_out_and_are_rate_limited(client, passenger, mailer, clock):
    client.post("/api/auth/forgot-password", json={"email": PASSENGER_EMAIL})
    client.post("/api/auth/forgot-password", json={"email": PASSENGER_EMAIL})
    assert len(mailer.outbox) == 1, "at most one code per minute"

    code = _code(mailer, PASSENGER_EMAIL)
    clock.advance(minutes=16)
    body = {"email": PASSENGER_EMAIL, "code": code, "new_password": "brand-new-pass"}
    assert client.post("/api/auth/reset-password", json=body).status_code == 400, "expired after 15 min"

    client.post("/api/auth/forgot-password", json={"email": PASSENGER_EMAIL})
    code = _code(mailer, PASSENGER_EMAIL)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        client.post("/api/auth/reset-password", json={**body, "code": wrong})
    assert client.post("/api/auth/reset-password", json={**body, "code": code}).status_code == 400, "locked after 5 tries"
