"""Login throttle and Host-header allow-list (defence against LAN brute-force and DNS rebinding)."""

import re

import pytest

from app import create_app
from app.services.security import LoginThrottle, host_allowed


@pytest.fixture
def app(tmp_path):
    from app.extensions import db
    from app.models import User

    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path / "d"), "WTF_CSRF_DISABLED": True})
    with app.app_context():
        u = User(username="demo", full_name="Demo", role="admin")
        u.set_password("password123")
        db.session.add(u)
        db.session.commit()
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def _try(c, pw):
    tok = re.search(r'name="_csrf" value="([^"]+)"', c.get("/login").get_data(as_text=True)).group(1)
    return c.post("/login", data={"username": "demo", "password": pw, "_csrf": tok})


# ---- login throttle -------------------------------------------------------

def test_throttle_locks_after_repeated_failures(app):
    c = app.test_client()
    for _ in range(5):
        assert _try(c, "wrong").status_code == 200  # wrong password, but still allowed to try
    r = _try(c, "wrong")
    assert r.status_code == 429  # sixth attempt is refused
    assert "Çok fazla" in r.get_data(as_text=True)
    # even the CORRECT password is refused while locked
    assert _try(c, "password123").status_code == 429


def test_success_before_threshold_resets(app):
    c = app.test_client()
    for _ in range(4):
        _try(c, "wrong")
    assert _try(c, "password123").status_code == 302  # correct login still works
    c.get("/logout")
    # counter cleared: a fresh run of wrong tries is allowed again, no immediate lock
    for _ in range(4):
        assert _try(c, "wrong").status_code == 200


def test_throttle_unit_escalates_and_forgives(monkeypatch):
    t = LoginThrottle(threshold=3, window=100, base_lock=10, max_lock=40)
    now = [1000.0]
    monkeypatch.setattr(t, "_now", lambda: now[0])
    for _ in range(3):
        t.record_failure("u:x")
    assert t.retry_after("u:x") == 11  # 10s lock (+1 rounding)
    now[0] += 11
    t.record_failure("u:x")            # 4th miss -> doubles to 20s
    assert 15 <= t.retry_after("u:x") <= 21
    now[0] += 200                      # a long quiet spell forgives everything
    assert t.retry_after("u:x") == 0
    t.record_success("u:x")


def test_throttle_keys_are_independent():
    t = LoginThrottle(threshold=2, base_lock=30)
    t.record_failure("u:alice", "ip:1.2.3.4")
    t.record_failure("u:alice", "ip:1.2.3.4")
    assert t.retry_after("u:alice")            # alice locked
    assert t.retry_after("ip:1.2.3.4")         # that device locked
    assert t.retry_after("u:bob") == 0         # a different account is unaffected


# ---- host allow-list ------------------------------------------------------

def test_host_allowed_accepts_local_and_private(app):
    with app.app_context():
        assert host_allowed("localhost")
        assert host_allowed("127.0.0.1:8443")
        assert host_allowed("192.168.1.20:8443")   # a private LAN address
        assert host_allowed("10.0.0.5")
        assert not host_allowed("evil.example")    # attacker domain (DNS rebinding)
        assert not host_allowed("8.8.8.8")         # a public IP literal
        assert not host_allowed("")


def test_rebinding_host_is_rejected_by_the_app(app):
    app.config["ENFORCE_HOST_ALLOWLIST"] = True
    c = app.test_client()
    assert c.get("/login", headers={"Host": "attacker.example"}).status_code == 400
    assert c.get("/login", headers={"Host": "192.168.1.50"}).status_code == 200  # legit LAN address
    assert c.get("/login", headers={"Host": "localhost"}).status_code == 200
