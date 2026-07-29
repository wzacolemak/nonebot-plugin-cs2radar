import json
import os

import pytest

from nonebot_plugin_cs2radar import renderer
from nonebot_plugin_cs2radar import llm, plugin_config
from nonebot_plugin_cs2radar.binding_store import BindingStore
from nonebot_plugin_cs2radar.config import Config
from nonebot_plugin_cs2radar.crawler import PWCrawler, _route_5e_requests
from nonebot_plugin_cs2radar.llm import LLMEvaluator
from nonebot_plugin_cs2radar.match_service import MatchService, PlayerStats
from nonebot_plugin_cs2radar.renderer import env
from nonebot_plugin_cs2radar.security import (
    CommandGuard,
    GuardRejected,
    inject_csp,
    safe_image_url,
)


def test_secure_defaults_do_not_reuse_global_llm_key() -> None:
    assert plugin_config.llm_enabled is False
    assert llm.api_key == ""
    assert plugin_config.cs2radar_pw_login_enabled is False
    assert plugin_config.cs2radar_pw_session_persist is False
    assert plugin_config.cs2radar_allow_query_others is False


def test_image_url_allowlist_rejects_local_and_lookalike_hosts() -> None:
    fallback = "https://static.5eplay.com/images/common/player_default.png"
    assert safe_image_url("http://127.0.0.1:3001/private", fallback) == fallback
    assert safe_image_url("https://static.5eplay.com.evil.example/x", fallback) == fallback
    assert safe_image_url("https://user:pass@static.5eplay.com/x", fallback) == fallback
    assert safe_image_url("https://static.5eplay.com/x", fallback) == "https://static.5eplay.com/x"


def test_templates_escape_html_and_filter_untrusted_avatar() -> None:
    html = env.get_template("stats.html").render(
        nickname='<script>document.body.dataset.pwned="yes"</script>',
        avatar='http://127.0.0.1:3001/private.png" onerror="alert(1)',
        stats={"career": {}, "best_season": {}, "home": {}, "role": {}},
        combat={"summary_cards": [], "clutch_cards": []},
        now="now",
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "127.0.0.1" not in html
    assert "onerror=" not in html


def test_llm_output_is_escaped() -> None:
    data = {
        "theme_a": "#fff",
        "theme_b": "#000",
        "platform_label": "5E",
        "map_name": "map",
        "match_type": "ranked",
        "start_at": "now",
        "duration_min": 30,
        "result_text": "win",
        "result_class": "good",
        "match_id": "1",
        "score_text": "13:10",
        "half_summary": "",
        "halves": [],
        "has_rounds": False,
        "has_overtime": False,
        "player": {"name": "p", "rating": "1", "adr": "1", "kill": 1, "death": 1, "hs": "1", "elo": "1", "rws": "1"},
        "player_highlights": {"summary_cards": [], "clutch_cards": []},
        "teammates": [],
        "opponents": [],
        "llm_title": "<img src=x onerror=alert(1)>",
        "llm_detail": "<script>alert(1)</script>",
    }
    html = env.get_template("match_detail.html").render(data=data, now="now")
    assert "<script>alert(1)</script>" not in html
    assert "<img src=x" not in html
    assert "&lt;script&gt;" in html


def test_csp_disables_script_and_network_connections() -> None:
    html = inject_csp(
        "<html><head></head><body>untrusted Content-Security-Policy text</body></html>"
    )
    assert "script-src 'none'" in html
    assert "connect-src 'none'" in html
    assert "127.0.0.1" not in html


@pytest.mark.asyncio
async def test_renderer_sets_wmpvp_referer(monkeypatch) -> None:
    captured = {}

    async def fake_html_to_pic(**kwargs):
        captured.update(kwargs)
        return b"image"

    monkeypatch.setattr(renderer, "html_to_pic", fake_html_to_pic)
    result = await renderer._secure_html_to_pic("<html></html>", width=640)

    assert result == b"image"
    assert captured["extra_http_headers"] == {
        "Referer": "https://www.wmpvp.com/"
    }


def test_pw_session_is_memory_only_by_default(tmp_path) -> None:
    crawler = PWCrawler(persist_session=False)
    crawler.session_file = tmp_path / "pw_session.json"
    crawler.set_session("secret-token", 76561198000000000)
    assert crawler.has_session()
    assert not crawler.session_file.exists()


def test_opt_in_pw_session_persistence_is_atomic(tmp_path) -> None:
    crawler = PWCrawler(persist_session=False)
    crawler.persist_session = True
    crawler.session_file = tmp_path / "pw_session.json"
    crawler.set_session("secret-token", 76561198000000000)
    assert json.loads(crawler.session_file.read_text(encoding="utf-8"))["token"] == "secret-token"
    assert not list(tmp_path.glob("*.tmp"))
    if os.name != "nt":
        assert crawler.session_file.stat().st_mode & 0o777 == 0o600


def test_llm_context_omits_platform_identifier() -> None:
    player = PlayerStats(
        name="player",
        uuid="private-platform-id",
        win=1,
        elo_change=1.0,
        rating=1.1,
        adr=80.0,
        rws=10.0,
        kill=20,
        death=10,
        headshot_rate=0.5,
    )
    assert "uuid" not in player.to_llm_dict()


def test_match_service_uses_memory_session_provider() -> None:
    service = MatchService(
        pw_session_provider=lambda: {
            "token": "memory-token",
            "my_steam_id": 76561198000000000,
            "appversion": "test-version",
        }
    )
    session = service._load_pw_session()
    assert session == {
        "token": "memory-token",
        "my_steam_id": 76561198000000000,
        "appversion": "test-version",
    }


def test_legacy_config_is_ignored_without_explicit_opt_in() -> None:
    secure = Config(cs_pro_llm_enabled=True, cs_pro_llm_api_key="legacy-key")
    assert secure.llm_enabled is False
    assert secure.llm_api_key == ""

    migration = Config(
        cs2radar_allow_legacy_config=True,
        cs_pro_llm_enabled=True,
        cs_pro_llm_api_key="legacy-key",
    )
    assert migration.llm_enabled is True
    assert migration.llm_api_key == "legacy-key"


def test_llm_api_url_requires_https_without_credentials() -> None:
    assert LLMEvaluator._validate_api_url("https://llm.example/v1/") == "https://llm.example/v1"
    with pytest.raises(ValueError):
        LLMEvaluator._validate_api_url("http://llm.example/v1")
    with pytest.raises(ValueError):
        LLMEvaluator._validate_api_url("https://user:pass@llm.example/v1")


def test_binding_database_permissions_are_private(tmp_path) -> None:
    db_path = tmp_path / "bindings.db"
    BindingStore(str(db_path))
    if os.name != "nt":
        assert db_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_command_guard_enforces_cooldown() -> None:
    guard = CommandGuard(max_concurrency=1, cooldown_seconds=30)
    async with guard.acquire("10000", "match"):
        pass
    with pytest.raises(GuardRejected):
        async with guard.acquire("10000", "match"):
            pass


@pytest.mark.asyncio
async def test_command_guard_prunes_expired_users() -> None:
    guard = CommandGuard(max_concurrency=1, cooldown_seconds=1)
    guard._last_used = {(str(index), "match"): 0.0 for index in range(1024)}
    async with guard.acquire("current", "match"):
        pass
    assert list(guard._last_used) == [("current", "match")]


class _FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url


class _FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = _FakeRequest(url)
        self.action = ""

    async def continue_(self) -> None:
        self.action = "continue"

    async def abort(self, reason: str) -> None:
        self.action = f"abort:{reason}"


@pytest.mark.asyncio
async def test_5e_playwright_network_allowlist() -> None:
    trusted = _FakeRoute("https://gate.5eplay.com/api/data")
    await _route_5e_requests(trusted)
    assert trusted.action == "continue"

    local = _FakeRoute("http://127.0.0.1:3001/private")
    await _route_5e_requests(local)
    assert local.action == "abort:blockedbyclient"

    lookalike = _FakeRoute("https://5eplay.com.evil.example/payload")
    await _route_5e_requests(lookalike)
    assert lookalike.action == "abort:blockedbyclient"
