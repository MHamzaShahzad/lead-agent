"""First-run setup page: redirect, save to a temp .env, secret masking, read-only connection test."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.main import app

SECRETS = dict(WA_CLOUD_TOKEN="EAAB-super-secret-token-1234", API_KEY="k" * 20 + "WXYZ", DASHBOARD_PASSWORD="")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path / "b"))
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(config, "OLLAMA_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(config, "QUEUE_WORKER", False)
    monkeypatch.setattr(config, "SETUP_DONE", False)
    monkeypatch.delenv("MY_BUSINESS", raising=False)
    for k, v in SECRETS.items():
        monkeypatch.setattr(config, k, v)
    for k in ("SETUP_DONE", "DASHBOARD_PASSWORD", "WA_CLOUD_TOKEN", "API_KEY", "MY_BUSINESS", "WHATSAPP_PROVIDER", "DEFAULT_COUNTRY_CODE"):
        monkeypatch.setitem(__import__("os").environ, k, __import__("os").environ.get(k, ""))
    db.init()
    return tmp_path / ".env"


def form(**kw):
    d = dict(my_business="We sell widgets", brand_name="Acme", country_code="+44", provider="waha", send_start="09:00", send_end="18:00",
             daily_limit="10", gap_min="30", gap_max="60")
    d.update(kw)
    return d


def test_redirects_to_setup_until_done(env):
    c = TestClient(app, follow_redirects=False)
    r = c.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/setup"
    assert c.get("/setup").status_code == 200
    r = c.post("/setup", data=form())
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert c.get("/").status_code == 200                      # no more redirect


def test_existing_install_not_redirected(env, monkeypatch):
    monkeypatch.setenv("MY_BUSINESS", "already configured")
    assert TestClient(app, follow_redirects=False).get("/").status_code == 200


def test_save_writes_env_and_applies(env):
    c = TestClient(app, follow_redirects=False)
    c.post("/setup", data=form(password="hunter22", api_key="a" * 32, places_key="gp-key"))
    text = env.read_text()
    for line in ("MY_BUSINESS=We sell widgets", "BRAND_NAME=Acme", "DEFAULT_COUNTRY_CODE=44", "SETUP_DONE=1", "DASHBOARD_PASSWORD=hunter22",
                 "API_KEY=" + "a" * 32, "GOOGLE_PLACES_API_KEY=gp-key", "DAILY_SEND_LIMIT=10", "SEND_GAP_MAX=60"):
        assert line in text
    assert config.DEFAULT_COUNTRY_CODE == "44" and config.DAILY_SEND_LIMIT == 10 and config.SETUP_DONE


def test_bad_input_not_saved(env):
    c = TestClient(app, follow_redirects=False)
    assert "Not saved" in c.post("/setup", data=form(password="abc")).text
    assert "Not saved" in c.post("/setup", data=form(provider="cloud")).text      # no token yet
    assert not env.exists() or "SETUP_DONE" not in env.read_text()


def test_secrets_masked_in_html(env, monkeypatch):
    monkeypatch.setattr(config, "SETUP_DONE", True)
    html = TestClient(app).get("/setup").text
    assert SECRETS["WA_CLOUD_TOKEN"] not in html and SECRETS["API_KEY"] not in html
    assert "••••1234" in html and "••••WXYZ" in html


def test_blank_secret_keeps_saved_value(env):
    c = TestClient(app, follow_redirects=False)
    c.post("/setup", data=form(provider="cloud", cloud_phone_id="123"))
    assert config.WA_CLOUD_TOKEN == SECRETS["WA_CLOUD_TOKEN"]


def test_connection_test_is_read_only(env, monkeypatch):
    seen = []

    def fake_get(url, **kw):
        seen.append(("GET", url))
        return httpx.Response(200, json={"display_phone_number": "+1 555"})

    def no_post(*a, **kw):
        raise AssertionError("setup test must never POST (send)")
    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", no_post)
    r = TestClient(app).post("/setup/test", data={"provider": "cloud", "cloud_token": "t", "cloud_phone_id": "99"})
    assert r.json()["ok"] and seen == [("GET", "https://graph.facebook.com/v21.0/99")]
    TestClient(app).post("/setup/test", data={"provider": "waha"})
