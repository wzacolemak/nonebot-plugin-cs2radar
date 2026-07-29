from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from nonebot import require

require("nonebot_plugin_htmlrender")
from nonebot_plugin_htmlrender import html_to_pic

from .security import inject_csp, safe_image_url

TEMPLATE_PATH = Path(__file__).parent / "templates"
WMPVP_REFERER = "https://www.wmpvp.com/"
env = Environment(
    loader=FileSystemLoader(TEMPLATE_PATH),
    autoescape=select_autoescape(enabled_extensions=("html", "xml"), default_for_string=True),
)
env.filters["safe_image_url"] = safe_image_url


async def _secure_html_to_pic(html: str, *, width: int) -> bytes:
    return await html_to_pic(
        html=inject_csp(html),
        viewport={"width": width, "height": 10},
        extra_http_headers={"Referer": WMPVP_REFERER},
    )


def _nested_value(source: Any, path: str) -> Any:
    current = source
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _pick_int(sources: list[Any], *aliases: str) -> int:
    for alias in aliases:
        for source in sources:
            value = _nested_value(source, alias)
            if value not in (None, ""):
                try:
                    return int(float(value))
                except Exception:
                    continue
    return 0


def _build_highlight_summary(*sources: Any) -> dict[str, Any]:
    source_list = [source for source in sources if isinstance(source, dict)]
    kills_2 = _pick_int(source_list, "k2", "doubleKill", "double_kill", "twoKill", "two_kill", "multiKill2", "2k", "kill2", "2kill", "double_kill_total", "2_kill", "kill_2")
    kills_3 = _pick_int(source_list, "k3", "tripleKill", "threeKill", "three_kill", "multiKill3", "3k", "kill3", "3kill", "triple_kill_total", "3_kill", "kill_3")
    kills_4 = _pick_int(source_list, "k4", "quadraKill", "fourKill", "four_kill", "multiKill4", "4k", "kill4", "4kill", "quadra_kill_total", "4_kill", "kill_4")
    kills_5 = _pick_int(source_list, "k5", "pentaKill", "fiveKill", "five_kill", "ace", "multiKill5", "5k", "kill5", "5kill", "penta_kill_total", "5_kill", "kill_5")
    first_kills = _pick_int(source_list, "firstKill", "firstKills", "first_kill", "entryKill", "entryKills", "firstBlood", "first_kill_total")
    clutch_1v1 = _pick_int(source_list, "vs1", "clutch1", "clutch_1", "clutch1v1", "oneVOne", "v1_total", "1v1", "end_1v1")
    clutch_1v2 = _pick_int(source_list, "vs2", "clutch2", "clutch_2", "clutch1v2", "oneVTwo", "v2_total", "1v2", "end_1v2")
    clutch_1v3 = _pick_int(source_list, "vs3", "clutch3", "clutch_3", "clutch1v3", "oneVThree", "v3_total", "1v3", "end_1v3")
    clutch_1v4 = _pick_int(source_list, "vs4", "clutch4", "clutch_4", "clutch1v4", "oneVFour", "v4_total", "1v4", "end_1v4")
    clutch_1v5 = _pick_int(source_list, "vs5", "clutch5", "clutch_5", "clutch1v5", "oneVFive", "v5_total", "1v5", "end_1v5")
    multi_kills = _pick_int(source_list, "multiKill", "multi_kill", "multiKills", "manyKill", "multi_kill_total")
    if multi_kills <= 0:
        multi_kills = kills_2 + kills_3 + kills_4 + kills_5
    clutch_wins = _pick_int(source_list, "clutchWin", "clutchWins", "endGameWin", "1vN_win_total", "1vN_win", "end_total")
    if clutch_wins <= 0:
        clutch_wins = clutch_1v1 + clutch_1v2 + clutch_1v3 + clutch_1v4 + clutch_1v5
    return {
        "first_kills": first_kills,
        "multi_kills": multi_kills,
        "clutch_wins": clutch_wins,
        "summary_cards": [
            {"label": "首杀", "value": first_kills},
            {"label": "多杀", "value": multi_kills},
            {"label": "残局", "value": clutch_wins},
            {"label": "2K/3K/4K/5K", "value": f"{kills_2}/{kills_3}/{kills_4}/{kills_5}"},
        ],
        "clutch_cards": [
            {"label": "1v1", "value": clutch_1v1},
            {"label": "1v2", "value": clutch_1v2},
            {"label": "1v3", "value": clutch_1v3},
            {"label": "1v4", "value": clutch_1v4},
            {"label": "1v5", "value": clutch_1v5},
        ],
    }


async def render_events_card(events: list[dict[str, Any]]) -> bytes:
    template = env.get_template("events.html")
    processed_events = []
    for event in events:
        title = event.get("title", "未知赛事")
        if title == "进行中" and event.get("status"):
            title = event.get("status")
            status = "进行中"
        else:
            status = event.get("status", "未开始")

        processed_events.append(
            {
                "title": title,
                "level": event.get("level", "A级"),
                "status": status,
                "time": event.get("time", ""),
                "location": event.get("location", ""),
                "prize": event.get("prize", ""),
                "teams": event.get("teams", ""),
            }
        )

    html_content = template.render(
        events=processed_events,
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    return await _secure_html_to_pic(html_content, width=800)


async def render_matches_card(matches: list[dict[str, Any]]) -> bytes:
    template = env.get_template("matches.html")
    html_content = template.render(
        matches=matches[:15],
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    return await _secure_html_to_pic(html_content, width=800)


async def render_results_card(results: list[dict[str, Any]]) -> bytes:
    template = env.get_template("match_results.html")
    html_content = template.render(
        results=results[:20],
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    return await _secure_html_to_pic(html_content, width=800)


async def render_stats_card(data: dict) -> bytes:
    template = env.get_template("stats.html")
    stats = data.get("stats", {})
    combat = _build_highlight_summary(stats, stats.get("career", {}), stats.get("best_season", {}), stats.get("home", {}))
    html_content = template.render(
        nickname=data.get("nickname", "Unknown"),
        avatar=data.get("avatar", ""),
        stats=stats,
        combat=combat,
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    return await _secure_html_to_pic(html_content, width=640)


async def render_player_detail(player_data: dict) -> bytes:
    template = env.get_template("player_detail.html")
    hltv_id = player_data.get("hltv_id")
    name = player_data.get("name", "player")
    hltv_link = f"https://www.hltv.org/player/{hltv_id}/{name}" if hltv_id else None

    html_content = template.render(
        player=player_data,
        hltv_link=hltv_link,
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    return await _secure_html_to_pic(html_content, width=800)


async def render_pw_stats_card(player_data: dict) -> bytes:
    template = env.get_template("pw_stats.html")
    combat = _build_highlight_summary(player_data.get("stats", {}))
    html_content = template.render(
        player=player_data,
        combat=combat,
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    return await _secure_html_to_pic(html_content, width=640)


async def render_match_detail_card(view_data: dict) -> bytes:
    template = env.get_template("match_detail.html")
    html_content = template.render(
        data=view_data,
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    return await _secure_html_to_pic(html_content, width=960)
