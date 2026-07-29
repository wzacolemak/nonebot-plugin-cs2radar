from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from .binding_store import BindingStore, UserBinding

PLATFORM_ALIASES = {
    "5e": "5e",
    "fivee": "5e",
    "pw": "pw",
    "wanmei": "pw",
    "perfectworld": "pw",
    "mm": "mm",
    "official": "mm",
}

FIVE_E_T_CODES = {"1", "4"}
FIVE_E_CT_CODES = {"2", "5"}


@dataclass
class CombatHighlights:
    first_kills: int = 0
    multi_kills: int = 0
    clutch_wins: int = 0
    kills_2: int = 0
    kills_3: int = 0
    kills_4: int = 0
    kills_5: int = 0
    clutch_1v1: int = 0
    clutch_1v2: int = 0
    clutch_1v3: int = 0
    clutch_1v4: int = 0
    clutch_1v5: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "first_kills": self.first_kills,
            "multi_kills": self.multi_kills,
            "clutch_wins": self.clutch_wins,
            "kills_2": self.kills_2,
            "kills_3": self.kills_3,
            "kills_4": self.kills_4,
            "kills_5": self.kills_5,
            "clutch_1v1": self.clutch_1v1,
            "clutch_1v2": self.clutch_1v2,
            "clutch_1v3": self.clutch_1v3,
            "clutch_1v4": self.clutch_1v4,
            "clutch_1v5": self.clutch_1v5,
        }


@dataclass
class PlayerStats:
    name: str
    uuid: str
    win: int
    elo_change: float
    rating: float
    adr: float
    rws: float
    kill: int
    death: int
    headshot_rate: float
    highlights: CombatHighlights = field(default_factory=CombatHighlights)

    def to_llm_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "win": self.win,
            "elo_change": self.elo_change,
            "rating": self.rating,
            "adr": self.adr,
            "rws": self.rws,
            "kill": self.kill,
            "death": self.death,
            "headshot_rate": self.headshot_rate,
            "highlights": self.highlights.to_dict(),
        }


@dataclass
class RoundResult:
    round_no: int
    segment_key: str
    segment_label: str
    result: str
    side: str | None = None
    score_after: str | None = None


@dataclass
class SegmentResult:
    key: str
    label: str
    our_score: int
    enemy_score: int
    rounds: list[RoundResult] = field(default_factory=list)


@dataclass
class MatchResult:
    platform: str
    map_name: str
    match_type: str
    start_time: int
    duration_min: int
    result_text: str
    match_id: str
    player: PlayerStats
    teammates: list[PlayerStats]
    opponents: list[PlayerStats]
    score_our: int = 0
    score_enemy: int = 0
    halves: list[SegmentResult] = field(default_factory=list)
    rounds: list[RoundResult] = field(default_factory=list)
    has_overtime: bool = False

    def llm_context(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "map": self.map_name,
            "match_type": self.match_type,
            "start_time": self.start_time,
            "duration_min": self.duration_min,
            "result": self.result_text,
            "final_score": {
                "our": self.score_our,
                "enemy": self.score_enemy,
                "text": _format_score(self.score_our, self.score_enemy),
            },
            "half_scores": [
                {
                    "segment": segment.key,
                    "label": segment.label,
                    "our": segment.our_score,
                    "enemy": segment.enemy_score,
                    "text": _format_score(segment.our_score, segment.enemy_score),
                }
                for segment in self.halves
            ],
            "round_summary": [
                {
                    "segment": segment.key,
                    "label": segment.label,
                    "sequence": ",".join(round_item.result for round_item in segment.rounds),
                }
                for segment in self.halves
                if segment.rounds
            ],
            "has_overtime": self.has_overtime,
            "player": self.player.to_llm_dict(),
            "teammates": [x.to_llm_dict() for x in self.teammates],
            "opponents": [x.to_llm_dict() for x in self.opponents],
        }


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except Exception:
        return default


def _format_score(our_score: int, enemy_score: int) -> str:
    return f"{our_score}:{enemy_score}"


def _segment_key(round_no: int) -> str:
    if round_no <= 12:
        return "H1"
    if round_no <= 24:
        return "H2"
    return f"OT{((round_no - 25) // 6) + 1}"


def _segment_label(segment_key: str) -> str:
    if segment_key == "H1":
        return "上半场"
    if segment_key == "H2":
        return "下半场"
    if segment_key.startswith("OT"):
        suffix = segment_key[2:] or "1"
        return f"加时{suffix}"
    return segment_key


def _segment_order(segment_key: str) -> tuple[int, int]:
    if segment_key == "H1":
        return (0, 0)
    if segment_key == "H2":
        return (1, 0)
    if segment_key.startswith("OT"):
        return (2, _safe_int(segment_key[2:], 1))
    return (9, 0)


class MatchService:
    def __init__(
        self,
        timeout: int = 15,
        pw_session_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.timeout = timeout
        self._pw_default_appversion = "3.5.4.172"
        self._pw_session_provider = pw_session_provider

    @staticmethod
    def normalize_platform(raw: str | None) -> str | None:
        if not raw:
            return None
        return PLATFORM_ALIASES.get(raw.strip().lower())

    async def bind_player(self, store: BindingStore, qq_id: str, platform: str, player_name: str) -> UserBinding:
        if platform == "5e":
            domain, uuid, canonical = await self._bind_5e(player_name)
        elif platform == "pw":
            domain, uuid, canonical = await self._bind_pw(player_name)
        else:
            raise ValueError("绑定仅支持 5e 或 pw")

        store.upsert_binding(qq_id, platform, canonical, domain, uuid)
        bound = store.get_binding(qq_id, platform)
        if not bound:
            raise RuntimeError("绑定存储失败")
        return bound

    async def bind_5e_domain(self, store: BindingStore, qq_id: str, domain: str, canonical_name: str | None = None) -> UserBinding:
        uuid = await self._resolve_5e_uuid(domain)
        name = (canonical_name or domain).strip()
        store.upsert_binding(qq_id, "5e", name, domain.strip(), uuid)
        bound = store.get_binding(qq_id, "5e")
        if not bound:
            raise RuntimeError("5E绑定存储失败")
        return bound

    async def fetch_match(self, store: BindingStore, qq_id: str, platform: str | None, round_index: int) -> MatchResult:
        p = self.normalize_platform(platform) if platform else None
        if p is None:
            p = store.get_default_platform(qq_id)

        binding = store.get_binding(qq_id, p)
        if p == "mm" and not binding:
            binding = store.get_binding(qq_id, "pw")
        if not binding:
            raise ValueError(f"未绑定平台 {p}，请先使用 /bind")

        if p == "5e":
            match_id = await self._get_5e_match_id(binding.uuid, round_index)
            raw = await self._get_5e_match_detail(match_id)
            return self._parse_5e(raw, binding, match_id)

        # PW/MM: allow username-only binding, resolve IDs lazily before query.
        if not str(binding.uuid).strip():
            domain, uuid, canonical = await self._resolve_pw_identity(binding.player_name)
            store.upsert_binding(binding.qq_id, "pw", canonical, domain, uuid)
            if p == "mm":
                store.upsert_binding(binding.qq_id, "mm", canonical, domain, uuid)
            refreshed = store.get_binding(binding.qq_id, p) or store.get_binding(binding.qq_id, "pw")
            if not refreshed or not str(refreshed.uuid).strip():
                raise ValueError("未能根据完美用户名解析到SteamID，请检查用户名")
            binding = refreshed

        ds = 3 if p == "pw" else 1
        match_id, match_item = await self._get_pw_match_entry(binding.uuid, round_index, ds)
        raw = await self._get_pw_match_detail(match_id, ds, binding.uuid, match_item)
        return self._parse_pw_mm(raw, binding, p, match_id)

    async def fetch_official_match_by_steam_id(self, steam_id: str, round_index: int = 1) -> MatchResult:
        normalized = str(steam_id or "").strip()
        if not re.fullmatch(r"7656119\d{10}", normalized):
            raise ValueError("SteamID64 格式不正确，应为 7656119 开头的 17 位数字")
        if round_index < 1 or round_index > 20:
            raise ValueError("场次序号应在 1 到 20 之间")

        binding = UserBinding(
            qq_id="",
            platform="mm",
            player_name=normalized,
            domain="",
            uuid=normalized,
            updated_at=0,
        )
        match_id, match_item = await self._get_pw_match_entry(normalized, round_index, 1)
        raw = await self._get_pw_match_detail(match_id, 1, normalized, match_item)
        return self._parse_pw_mm(raw, binding, "mm", match_id)

    async def _bind_5e(self, player_name: str) -> tuple[str, str, str]:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"https://arena.5eplay.com/search?keywords={player_name}",
        }
        url = "https://arena.5eplay.com/api/search/player/1/16"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, params={"keywords": player_name}, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        users = data.get("data", {}).get("user", {}).get("list", [])
        if not users:
            raise ValueError("未找到5E玩家，请检查昵称")

        target = None
        key = player_name.strip().lower()
        for u in users:
            if str(u.get("username", "")).strip().lower() == key:
                target = u
                break
        if target is None:
            target = users[0]

        domain = str(target.get("domain") or "")
        canonical = str(target.get("username") or player_name)
        if not domain:
            raise ValueError("5E玩家域名解析失败")

        uuid = await self._resolve_5e_uuid(domain)
        return domain, uuid, canonical

    async def _resolve_5e_uuid(self, domain: str) -> str:
        id_url = "https://gate.5eplay.com/userinterface/http/v1/userinterface/idTransfer"
        payload = {"trans": {"domain": domain}}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(id_url, json=payload, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            id_data = resp.json()

        uuid = str(id_data.get("data", {}).get("uuid") or "")
        if not uuid:
            raise ValueError("5E UUID 获取失败")
        return uuid

    async def _bind_pw(self, player_name: str) -> tuple[str, str, str]:
        # Binding rule: only username is required. Resolve IDs best-effort.
        # If ID fields are missing in search result, binding still succeeds.
        try:
            domain, uuid, canonical = await self._resolve_pw_identity(player_name)
            return domain, uuid, canonical
        except Exception:
            return "", "", player_name.strip()

    async def _resolve_pw_identity(self, player_name: str) -> tuple[str, str, str]:
        session = self._load_pw_session()
        self._require_pw_session(session)
        url = "https://appengine.wmpvp.com/steamcn/app/search/user"
        headers = {
            "appversion": session["appversion"],
            "token": session["token"],
            "platform": "android",
            "Content-Type": "application/json",
        }
        payload = {"keyword": player_name, "page": 1}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != 1:
            raise ValueError(data.get("description") or "完美搜索接口返回异常")
        users = data.get("result", [])
        if not users:
            raise ValueError("未找到完美玩家，请检查用户名")

        target = None
        key = player_name.strip().lower()
        for u in users:
            nick = str(u.get("pvpNickName") or u.get("name") or "").strip().lower()
            if nick == key:
                target = u
                break
        if target is None:
            target = users[0]

        pvp_user_id = target.get("pvpUserId")
        steam_id = target.get("steamId")
        canonical = str(target.get("pvpNickName") or target.get("name") or player_name)

        domain = str(pvp_user_id).strip()
        uuid = str(steam_id).strip()
        if not domain or not uuid:
            raise ValueError("完美用户名已找到，但缺少 pvpUserId 或 steamId")

        return domain, uuid, canonical

    async def _get_5e_match_id(self, uuid: str, round_index: int) -> str:
        url = f"https://gate.5eplay.com/crane/http/api/data/player_match?uuid={uuid}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            data = resp.json()

        items = data.get("data", {}).get("match_data", [])
        if not items or round_index <= 0 or round_index > len(items):
            raise ValueError(f"未找到倒数第 {round_index} 把5E对局")
        match_id = str(items[round_index - 1].get("match_id") or "")
        if not match_id:
            raise ValueError("5E match_id 解析失败")
        return match_id

    async def _get_5e_match_detail(self, match_id: str) -> dict:
        url = f"https://gate.5eplay.com/crane/http/api/data/match/{match_id}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            data = resp.json()
        payload = data.get("data", {})
        if not payload:
            raise ValueError("5E对局详情为空")
        return payload

    async def _get_pw_match_entry(self, steam_id: str, round_index: int, data_source: int) -> tuple[str, dict[str, Any]]:
        session = self._load_pw_session()
        self._require_pw_session(session)
        url = "https://api.wmpvp.com/api/csgo/home/match/list"
        headers = self._pw_headers(session)
        payload = {
            "toSteamId": int(steam_id),
            "mySteamId": int(session["my_steam_id"]),
            "dataSource": data_source,
            "page": 1,
            "pageSize": 20,
            "csgoSeasonId": "recent",
            "pvpType": -1,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        if data.get("statusCode") != 0:
            raise ValueError(data.get("errorMessage") or "完美/官匹对局列表获取失败")
        items = data.get("data", {}).get("matchList", [])
        if not items or round_index <= 0 or round_index > len(items):
            raise ValueError(f"未找到倒数第 {round_index} 把对局")
        match_item = items[round_index - 1]
        match_id = str(match_item.get("matchId") or "")
        if not match_id:
            raise ValueError("match_id 解析失败")
        return match_id, match_item

    async def _get_pw_match_detail(
        self,
        match_id: str,
        data_source: int,
        steam_id: str,
        match_item: dict[str, Any] | None = None,
    ) -> dict:
        session = self._load_pw_session()
        self._require_pw_session(session)
        headers = self._pw_headers(session)

        candidates = [
            (
                "https://api.wmpvp.com/api/v1/csgo/match",
                {
                    "matchId": match_id,
                    "platform": "admin",
                    "dataSource": str(data_source),
                },
            ),
            (
                "https://api.wmpvp.com/api/csgo/home/match/detailStats",
                {
                    "matchId": match_id,
                    "toSteamId": int(steam_id),
                    "mySteamId": int(session["my_steam_id"]),
                    "dataSource": data_source,
                },
            ),
        ]

        last_error = "完美/官匹对局详情获取失败"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for url, payload in candidates:
                try:
                    resp = await client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    if data.get("statusCode") != 0:
                        last_error = str(data.get("errorMessage") or last_error)
                        continue
                    body = data.get("data", {})
                    if body:
                        return body
                except Exception as exc:
                    last_error = str(exc)
                    continue

        if match_item:
            return self._build_pw_match_detail_fallback(match_id, steam_id, match_item)
        raise ValueError(last_error or "完美/官匹对局详情为空")

    def _build_pw_match_detail_fallback(self, match_id: str, steam_id: str, item: dict[str, Any]) -> dict[str, Any]:
        score1 = _safe_int(item.get("score1"))
        score2 = _safe_int(item.get("score2"))
        team = _safe_int(item.get("team"))
        if team not in (1, 2):
            team = 1 if score1 >= score2 else 2

        win_team = _safe_int(item.get("winTeam"))
        if win_team not in (1, 2):
            if score1 > score2:
                win_team = 1
            elif score2 > score1:
                win_team = 2
            else:
                win_team = 0

        nick = str(item.get("steamNick") or item.get("steamName") or steam_id or "未知")
        player = {
            "playerId": str(item.get("playerId") or steam_id),
            "nickName": nick,
            "team": team,
            "pvpScoreChange": item.get("pvpScoreChange"),
            "pwRating": item.get("pwRating"),
            "rating": item.get("rating"),
            "adpr": item.get("adpr") or item.get("adr"),
            "rws": item.get("rws"),
            "kill": item.get("kill"),
            "death": item.get("death"),
            "headShotRatio": item.get("headShotRatio") or 0,
        }
        base = {
            "matchId": match_id,
            "startTime": item.get("startTime"),
            "endTime": item.get("endTime"),
            "duration": _safe_int(item.get("duration"), 30),
            "score1": score1,
            "score2": score2,
            "winTeam": win_team,
            "mode": item.get("mode"),
            "matchType": item.get("matchType"),
            "map": item.get("mapName") or "未知地图",
            "mapEn": item.get("mapName") or "未知地图",
            "_list_only": True,
        }
        return {"base": base, "players": [player]}

    def _parse_5e(self, data: dict, binding: UserBinding, match_id: str) -> MatchResult:
        base = data.get("main", {})
        g1 = data.get("group_1", [])
        g2 = data.get("group_2", [])
        target, team, opp = self._pick_target_5e(g1, g2, binding)
        if not target:
            raise ValueError("5E对局中未找到绑定玩家")

        target_group = 1 if team is g1 else 2
        player = self._extract_5e_player(target)
        teammates = [self._extract_5e_player(x) for x in team if x is not target]
        opponents = [self._extract_5e_player(x) for x in opp]

        start_time = _safe_int(base.get("start_time"))
        end_time = _safe_int(base.get("end_time"))
        duration = max(1, (end_time - start_time) // 60) if end_time and start_time else 30

        score_g1 = _safe_int(base.get("group1_all_score"))
        score_g2 = _safe_int(base.get("group2_all_score"))
        score_our = score_g1 if target_group == 1 else score_g2
        score_enemy = score_g2 if target_group == 1 else score_g1
        if score_our == score_enemy:
            result_text = "平局"
        else:
            result_text = "胜利" if score_our > score_enemy else "失败"

        rounds = self._build_5e_rounds(data, target_group)
        halves = self._build_5e_segments(base, target_group, rounds)

        return MatchResult(
            platform="5e",
            map_name=str(base.get("map_desc") or "未知地图"),
            match_type="5E排位",
            start_time=start_time or int(dt.datetime.now().timestamp()),
            duration_min=duration,
            result_text=result_text,
            match_id=match_id,
            player=player,
            teammates=teammates,
            opponents=opponents,
            score_our=score_our,
            score_enemy=score_enemy,
            halves=halves,
            rounds=rounds,
            has_overtime=any(segment.key.startswith("OT") for segment in halves),
        )

    def _pick_target_5e(self, g1: list, g2: list, binding: UserBinding):
        def hit(item: dict) -> bool:
            user = item.get("user_info", {}).get("user_data", {})
            name = str(user.get("username") or "")
            uid = str(user.get("uid") or "")
            uuid = str(user.get("uuid") or "")
            return name.lower() == binding.player_name.lower() or uid == binding.uuid or uuid == binding.uuid

        for player in g1:
            if hit(player):
                return player, g1, g2
        for player in g2:
            if hit(player):
                return player, g2, g1
        return None, None, None

    def _extract_5e_player(self, row: dict) -> PlayerStats:
        user = row.get("user_info", {}).get("user_data", {})
        fight = row.get("fight", {})
        sts = row.get("sts", {})
        kill = _safe_int(fight.get("kill"))
        hs = 0.0 if kill == 0 else float(fight.get("headshot") or 0) / max(kill, 1)
        return PlayerStats(
            name=str(user.get("username") or "未知"),
            uuid=str(user.get("uuid") or user.get("uid") or ""),
            win=_safe_int(fight.get("is_win")),
            elo_change=float(sts.get("change_elo") or 0),
            rating=float(fight.get("rating2") or 0),
            adr=float(fight.get("adr") or 0),
            rws=float(fight.get("rws") or 0),
            kill=kill,
            death=_safe_int(fight.get("death")),
            headshot_rate=hs,
            highlights=self._extract_highlights(row, fight, sts, user),
        )

    @staticmethod
    def _nested_value(source: Any, path: str) -> Any:
        current = source
        for part in path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current

    def _pick_stat(self, sources: tuple[Any, ...], *aliases: str) -> int:
        for alias in aliases:
            for source in sources:
                value = self._nested_value(source, alias)
                if value not in (None, ""):
                    return _safe_int(value)
        return 0

    def _extract_highlights(self, *sources: Any) -> CombatHighlights:
        kills_2 = self._pick_stat(sources, "k2", "doubleKill", "double_kill", "twoKill", "two_kill", "multiKill2", "2k", "kill2", "2kill", "double_kill_total", "2_kill", "kill_2")
        kills_3 = self._pick_stat(sources, "k3", "tripleKill", "threeKill", "three_kill", "multiKill3", "3k", "kill3", "3kill", "triple_kill_total", "3_kill", "kill_3")
        kills_4 = self._pick_stat(sources, "k4", "quadraKill", "fourKill", "four_kill", "multiKill4", "4k", "kill4", "4kill", "quadra_kill_total", "4_kill", "kill_4")
        kills_5 = self._pick_stat(sources, "k5", "pentaKill", "fiveKill", "five_kill", "ace", "multiKill5", "5k", "kill5", "5kill", "penta_kill_total", "5_kill", "kill_5")
        clutch_1v1 = self._pick_stat(sources, "vs1", "clutch1", "clutch_1", "clutch1v1", "oneVOne", "v1_total", "1v1", "end_1v1")
        clutch_1v2 = self._pick_stat(sources, "vs2", "clutch2", "clutch_2", "clutch1v2", "oneVTwo", "v2_total", "1v2", "end_1v2")
        clutch_1v3 = self._pick_stat(sources, "vs3", "clutch3", "clutch_3", "clutch1v3", "oneVThree", "v3_total", "1v3", "end_1v3")
        clutch_1v4 = self._pick_stat(sources, "vs4", "clutch4", "clutch_4", "clutch1v4", "oneVFour", "v4_total", "1v4", "end_1v4")
        clutch_1v5 = self._pick_stat(sources, "vs5", "clutch5", "clutch_5", "clutch1v5", "oneVFive", "v5_total", "1v5", "end_1v5")
        multi_total = self._pick_stat(sources, "multiKill", "multi_kill", "multiKills", "manyKill", "multi_kill_total")
        if multi_total <= 0:
            multi_total = kills_2 + kills_3 + kills_4 + kills_5
        clutch_total = self._pick_stat(sources, "clutchWin", "clutchWins", "endGameWin", "残局", "1vN_win_total", "1vN_win", "end_total")
        if clutch_total <= 0:
            clutch_total = clutch_1v1 + clutch_1v2 + clutch_1v3 + clutch_1v4 + clutch_1v5
        return CombatHighlights(
            first_kills=self._pick_stat(sources, "firstKill", "firstKills", "first_kill", "entryKill", "entryKills", "firstBlood", "first_kill_total"),
            multi_kills=multi_total,
            clutch_wins=clutch_total,
            kills_2=kills_2,
            kills_3=kills_3,
            kills_4=kills_4,
            kills_5=kills_5,
            clutch_1v1=clutch_1v1,
            clutch_1v2=clutch_1v2,
            clutch_1v3=clutch_1v3,
            clutch_1v4=clutch_1v4,
            clutch_1v5=clutch_1v5,
        )

    def _parse_pw_mm(self, data: dict, binding: UserBinding, platform: str, match_id: str) -> MatchResult:
        base = data.get("base") or {}
        players = data.get("players") or []
        target = self._pick_target_pw(players, binding)
        if not target:
            raise ValueError("对局中未找到绑定玩家")

        target_team = self._resolve_team(target, base)
        if target_team <= 0:
            raise ValueError("无法识别玩家队伍")

        player = self._extract_pw_player(target, base)
        teammates: list[PlayerStats] = []
        opponents: list[PlayerStats] = []
        for row in players:
            if row is target:
                continue
            player_stats = self._extract_pw_player(row, base)
            team = self._resolve_team(row, base)
            if team == target_team:
                teammates.append(player_stats)
            else:
                opponents.append(player_stats)

        start_ts = self._parse_time(base.get("startTime"))
        end_ts = self._parse_time(base.get("endTime"))
        if start_ts <= 0:
            start_ts = int(dt.datetime.now().timestamp())
        duration = max(1, (end_ts - start_ts) // 60) if end_ts > start_ts else _safe_int(base.get("duration"), 30)

        score_team1 = _safe_int(base.get("score1"))
        score_team2 = _safe_int(base.get("score2"))
        score_our = score_team1 if target_team == 1 else score_team2
        score_enemy = score_team2 if target_team == 1 else score_team1
        if score_our == score_enemy:
            result_text = "平局"
        else:
            result_text = "胜利" if score_our > score_enemy else "失败"

        map_name = str(base.get("map") or base.get("mapEn") or "未知地图")
        match_type = str(base.get("mode") or base.get("mode2") or base.get("matchType") or ("官匹" if platform == "mm" else "完美"))
        rounds = self._build_pw_rounds(base, target_team)
        halves = self._build_pw_segments(base, target_team, rounds)

        return MatchResult(
            platform=platform,
            map_name=map_name,
            match_type=match_type,
            start_time=start_ts,
            duration_min=duration,
            result_text=result_text,
            match_id=match_id,
            player=player,
            teammates=teammates,
            opponents=opponents,
            score_our=score_our,
            score_enemy=score_enemy,
            halves=halves,
            rounds=rounds,
            has_overtime=any(segment.key.startswith("OT") for segment in halves),
        )

    def _build_5e_rounds(self, data: dict, target_group: int) -> list[RoundResult]:
        main = data.get("main") or {}
        codes = data.get("round_sfui_type") or []
        if not isinstance(codes, list):
            return []

        our_score = 0
        enemy_score = 0
        rounds: list[RoundResult] = []
        for idx, code in enumerate(codes, start=1):
            segment_key = _segment_key(idx)
            segment_label = _segment_label(segment_key)
            winner_group = self._resolve_5e_round_winner_group(main, idx, str(code))
            if winner_group is None:
                result = "?"
                score_after = None
            else:
                won = winner_group == target_group
                if won:
                    our_score += 1
                    result = "W"
                else:
                    enemy_score += 1
                    result = "L"
                score_after = _format_score(our_score, enemy_score)
            rounds.append(
                RoundResult(
                    round_no=idx,
                    segment_key=segment_key,
                    segment_label=segment_label,
                    result=result,
                    side=self._resolve_5e_our_side(main, target_group, idx),
                    score_after=score_after,
                )
            )
        return rounds

    def _resolve_5e_round_winner_group(self, main: dict, round_no: int, code: str) -> int | None:
        winner_side = self._resolve_5e_winner_side(code)
        if not winner_side:
            return None
        group1_side = self._resolve_5e_group_side(main, 1, round_no)
        group2_side = self._resolve_5e_group_side(main, 2, round_no)
        if group1_side == winner_side:
            return 1
        if group2_side == winner_side:
            return 2
        return None

    def _resolve_5e_our_side(self, main: dict, target_group: int, round_no: int) -> str | None:
        return self._resolve_5e_group_side(main, target_group, round_no)

    def _resolve_5e_group_side(self, main: dict, group_no: int, round_no: int) -> str | None:
        if round_no <= 12:
            role = main.get(f"group{group_no}_fh_role")
            return self._map_5e_role_to_side(role)
        if round_no <= 24:
            role = main.get(f"group{group_no}_sh_role")
            return self._map_5e_role_to_side(role)
        return None

    @staticmethod
    def _map_5e_role_to_side(role: Any) -> str | None:
        value = _safe_int(role, -1)
        if value == 0:
            return "CT"
        if value == 1:
            return "T"
        return None

    @staticmethod
    def _resolve_5e_winner_side(code: str) -> str | None:
        if code in FIVE_E_T_CODES:
            return "T"
        if code in FIVE_E_CT_CODES:
            return "CT"
        return None

    def _build_5e_segments(self, main: dict, target_group: int, rounds: list[RoundResult]) -> list[SegmentResult]:
        by_key = {segment.key: segment for segment in self._segments_from_rounds(rounds)}
        group_prefix = f"group{target_group}"
        enemy_prefix = "group2" if target_group == 1 else "group1"
        segments = [
            SegmentResult(
                key="H1",
                label=_segment_label("H1"),
                our_score=_safe_int(main.get(f"{group_prefix}_fh_score")),
                enemy_score=_safe_int(main.get(f"{enemy_prefix}_fh_score")),
                rounds=by_key.get("H1", SegmentResult("H1", _segment_label("H1"), 0, 0)).rounds,
            ),
            SegmentResult(
                key="H2",
                label=_segment_label("H2"),
                our_score=_safe_int(main.get(f"{group_prefix}_sh_score")),
                enemy_score=_safe_int(main.get(f"{enemy_prefix}_sh_score")),
                rounds=by_key.get("H2", SegmentResult("H2", _segment_label("H2"), 0, 0)).rounds,
            ),
        ]
        for key, segment in sorted(by_key.items(), key=lambda item: _segment_order(item[0])):
            if key not in {"H1", "H2"}:
                segments.append(segment)
        return segments

    def _build_pw_rounds(self, base: dict, target_team: int) -> list[RoundResult]:
        team1_rounds = str(base.get("team1round") or "")
        team2_rounds = str(base.get("team2round") or "")
        if not team1_rounds or not team2_rounds:
            return []

        total = min(len(team1_rounds), len(team2_rounds))
        our_score = 0
        enemy_score = 0
        rounds: list[RoundResult] = []
        for idx in range(total):
            code1 = team1_rounds[idx]
            code2 = team2_rounds[idx]
            winner_team: int | None = None
            if code1 != "0" and code2 == "0":
                winner_team = 1
            elif code2 != "0" and code1 == "0":
                winner_team = 2
            elif code1 == "0" and code2 == "0":
                winner_team = None
            else:
                winner_team = 1 if _safe_int(code1) >= _safe_int(code2) else 2

            if winner_team is None:
                result = "?"
                score_after = None
            else:
                won = winner_team == target_team
                if won:
                    our_score += 1
                    result = "W"
                else:
                    enemy_score += 1
                    result = "L"
                score_after = _format_score(our_score, enemy_score)

            segment_key = _segment_key(idx + 1)
            rounds.append(RoundResult(idx + 1, segment_key, _segment_label(segment_key), result, None, score_after))
        return rounds

    def _build_pw_segments(self, base: dict, target_team: int, rounds: list[RoundResult]) -> list[SegmentResult]:
        by_key = {segment.key: segment for segment in self._segments_from_rounds(rounds)} if rounds else {}
        score1 = _safe_int(base.get("score1"))
        score2 = _safe_int(base.get("score2"))
        half1 = _safe_int(base.get("halfScore1"))
        half2 = _safe_int(base.get("halfScore2"))
        extra1 = _safe_int(base.get("extraScore1"))
        extra2 = _safe_int(base.get("extraScore2"))

        if base.get("_list_only"):
            if target_team == 1:
                full_our, full_enemy = score1, score2
            else:
                full_our, full_enemy = score2, score1
            return [SegmentResult("FULL", "全场", full_our, full_enemy, by_key.get("FULL", SegmentResult("FULL", "全场", 0, 0)).rounds)]

        if target_team == 1:
            h1_our, h1_enemy = half1, half2
            total_our, total_enemy = score1, score2
            extra_our, extra_enemy = extra1, extra2
        else:
            h1_our, h1_enemy = half2, half1
            total_our, total_enemy = score2, score1
            extra_our, extra_enemy = extra2, extra1

        regular_our = max(total_our - extra_our, 0)
        regular_enemy = max(total_enemy - extra_enemy, 0)
        h2_our = max(regular_our - h1_our, 0)
        h2_enemy = max(regular_enemy - h1_enemy, 0)
        segments = [
            SegmentResult("H1", _segment_label("H1"), h1_our, h1_enemy, by_key.get("H1", SegmentResult("H1", _segment_label("H1"), 0, 0)).rounds),
            SegmentResult("H2", _segment_label("H2"), h2_our, h2_enemy, by_key.get("H2", SegmentResult("H2", _segment_label("H2"), 0, 0)).rounds),
        ]
        ot_segments = [segment for key, segment in sorted(by_key.items(), key=lambda item: _segment_order(item[0])) if key not in {"H1", "H2"}]
        if ot_segments:
            segments.extend(ot_segments)
        elif extra_our or extra_enemy:
            segments.append(SegmentResult("OT1", _segment_label("OT1"), extra_our, extra_enemy))
        return segments

    def _segments_from_rounds(self, rounds: list[RoundResult]) -> list[SegmentResult]:
        buckets: dict[str, SegmentResult] = {}
        for round_item in rounds:
            bucket = buckets.get(round_item.segment_key)
            if bucket is None:
                bucket = SegmentResult(round_item.segment_key, round_item.segment_label, 0, 0, [])
                buckets[round_item.segment_key] = bucket
            bucket.rounds.append(round_item)
            if round_item.result == "W":
                bucket.our_score += 1
            elif round_item.result == "L":
                bucket.enemy_score += 1
        return [segment for _, segment in sorted(buckets.items(), key=lambda item: _segment_order(item[0]))]

    def _pick_target_pw(self, players: list[dict], binding: UserBinding) -> dict | None:
        name_key = binding.player_name.strip().lower()
        uuid_key = str(binding.uuid).strip()
        for p in players:
            pid = str(p.get("playerId") or "").strip()
            name = str(p.get("nickName") or "").strip().lower()
            if uuid_key and pid == uuid_key:
                return p
            if name_key and name == name_key:
                return p
        return None

    def _extract_pw_player(self, row: dict, base: dict) -> PlayerStats:
        team = self._resolve_team(row, base)
        win_team = int(base.get("winTeam") or 0)
        hs = float(row.get("headShotRatio") or 0.0)
        if hs > 1:
            hs /= 100
        return PlayerStats(
            name=str(row.get("nickName") or row.get("playerId") or "未知"),
            uuid=str(row.get("playerId") or ""),
            win=1 if team == win_team and win_team > 0 else 0,
            elo_change=float(row.get("pvpScoreChange") or 0),
            rating=float(row.get("pwRating") or row.get("rating") or 0),
            adr=float(row.get("adpr") or 0),
            rws=float(row.get("rws") or 0),
            kill=int(row.get("kill") or 0),
            death=int(row.get("death") or 0),
            headshot_rate=hs,
            highlights=self._extract_highlights(row, base),
        )

    def _resolve_team(self, row: dict, base: dict) -> int:
        team = int(row.get("team") or 0)
        if team in (1, 2):
            return team

        pid = str(row.get("playerId") or "")
        t1 = {x.strip() for x in str(base.get("team1Info") or "").split(",") if x.strip()}
        t2 = {x.strip() for x in str(base.get("team2Info") or "").split(",") if x.strip()}
        if pid in t1:
            return 1
        if pid in t2:
            return 2
        return 0

    @staticmethod
    def _parse_time(text: Any) -> int:
        if not text:
            return 0
        val = str(text)
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
            try:
                return int(dt.datetime.strptime(val, fmt).timestamp())
            except Exception:
                pass
        if re.fullmatch(r"\d+", val):
            n = int(val)
            if n > 10_000_000_000:
                n //= 1000
            return n
        return 0

    def _load_pw_session(self) -> dict[str, Any]:
        data = {
            "token": "",
            "my_steam_id": 0,
            "appversion": self._pw_default_appversion,
        }
        if self._pw_session_provider is None:
            return data
        try:
            raw = self._pw_session_provider()
            token = str(raw.get("token") or "").strip()
            sid = raw.get("my_steam_id")
            if token:
                data["token"] = token
            if sid is not None:
                data["my_steam_id"] = int(sid)
            appversion = str(raw.get("appversion") or "").strip()
            if appversion:
                data["appversion"] = appversion
        except Exception:
            pass
        return data

    @staticmethod
    def _require_pw_session(session: dict[str, Any]) -> None:
        if str(session.get("token") or "").strip() and int(session.get("my_steam_id") or 0) > 0:
            return
        raise ValueError("请先使用 /pwlogin 登录完美平台后再查询完美/官匹数据")

    @staticmethod
    def _pw_headers(session: dict[str, Any]) -> dict[str, str]:
        return {
            "Content-Type": "application/json;charset=UTF-8",
            "User-Agent": "okhttp/4.11.0",
            "appversion": str(session.get("appversion") or "3.5.4.172"),
            "platform": "android",
            "token": str(session.get("token") or ""),
        }


def parse_match_args(raw: str) -> tuple[str | None, int]:
    tokens = [x for x in re.split(r"\s+", (raw or "").strip()) if x]
    platform = None
    round_index = 1
    for t in tokens:
        p = MatchService.normalize_platform(t)
        if p:
            platform = p
            continue
        if t.isdigit():
            round_index = max(1, int(t))
    return platform, round_index


def parse_bind_args(raw: str, default_platform: str = "5e") -> tuple[str, str]:
    tokens = [x for x in re.split(r"\s+", (raw or "").strip()) if x]
    if not tokens:
        raise ValueError("参数为空")

    platform = None
    if tokens and MatchService.normalize_platform(tokens[0]) in ("5e", "pw", "mm"):
        platform = MatchService.normalize_platform(tokens[0])
        tokens = tokens[1:]
    elif tokens and MatchService.normalize_platform(tokens[-1]) in ("5e", "pw", "mm"):
        platform = MatchService.normalize_platform(tokens[-1])
        tokens = tokens[:-1]

    if platform is None:
        platform = default_platform
    if platform == "mm":
        raise ValueError("官匹(mm)复用 pw 绑定，请使用 /bind pw <用户名>")

    if not tokens:
        raise ValueError("缺少玩家名")
    return platform, " ".join(tokens).strip()
