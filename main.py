import concurrent.futures
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, Thread
from typing import Dict, List, Optional, Set

import requests

from ulauncher.api.client.Extension import Extension
from ulauncher.api.client.EventListener import EventListener
from ulauncher.api.shared.event import KeywordQueryEvent, ItemEnterEvent, PreferencesEvent
from ulauncher.api.shared.item.ExtensionResultItem import ExtensionResultItem
from ulauncher.api.shared.action.CopyToClipboardAction import CopyToClipboardAction
from ulauncher.api.shared.action.DoNothingAction import DoNothingAction
from ulauncher.api.shared.action.ExtensionCustomAction import ExtensionCustomAction
from ulauncher.api.shared.action.OpenUrlAction import OpenUrlAction
from ulauncher.api.shared.action.RenderResultListAction import RenderResultListAction
from ulauncher.api.shared.action.RunScriptAction import RunScriptAction

STEAM_SEARCH_API  = "https://store.steampowered.com/search/results"
PROTONDB_API      = "https://www.protondb.com/api/v1/reports/summaries/{}.json"
STEAM_OWNED_API   = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
STEAM_CAPSULE_URL = "https://cdn.akamaized.net/steam/apps/{}/capsule_sm_120.jpg"

CACHE_DIR   = Path.home() / ".cache" / "ulauncher-protondb"
RATINGS_DB  = CACHE_DIR / "ratings.db"
IMAGES_DIR  = CACHE_DIR / "images"
STEAM_APPS  = Path.home() / ".local/share/Steam/steamapps"

TIER_EMOJI = {
    "platinum": "💎",
    "gold":     "🥇",
    "silver":   "🥈",
    "bronze":   "🥉",
    "borked":   "💀",
    "pending":  "❓",
}

TIER_RANK = {"platinum": 0, "gold": 1, "silver": 2, "bronze": 3, "borked": 4, "pending": 5}

MIN_TIER_RANK = {"any": 5, "bronze": 3, "silver": 2, "gold": 1, "platinum": 0}


@dataclass
class Game:
    app_id: str
    name: str
    tier: str = "pending"
    report_count: int = 0
    installed: bool = False
    owned: bool = False


class RatingsCache:
    def __init__(self):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._conn = sqlite3.connect(str(RATINGS_DB), check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS ratings (
                app_id       TEXT PRIMARY KEY,
                tier         TEXT,
                report_count INTEGER,
                cached_at    INTEGER
            )
        """)
        self._conn.commit()

    def get(self, app_id: str, ttl_hours: int) -> Optional[Dict]:
        cutoff = int(time.time()) - ttl_hours * 3600
        with self._lock:
            row = self._conn.execute(
                "SELECT tier, report_count FROM ratings WHERE app_id = ? AND cached_at > ?",
                (app_id, cutoff),
            ).fetchone()
        return {"tier": row[0], "report_count": row[1]} if row else None

    def set(self, app_id: str, tier: str, report_count: int):
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO ratings (app_id, tier, report_count, cached_at) VALUES (?, ?, ?, ?)",
                (app_id, tier, report_count, int(time.time())),
            )
            self._conn.commit()

    def image_path(self, app_id: str) -> Optional[str]:
        p = IMAGES_DIR / f"{app_id}.jpg"
        return str(p) if p.exists() else None


def get_installed_app_ids() -> Set[str]:
    installed = set()
    dirs_to_scan = []

    vdf = STEAM_APPS / "libraryfolders.vdf"
    if vdf.exists():
        try:
            for match in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(errors="ignore")):
                d = Path(match.group(1)) / "steamapps"
                if d.is_dir():
                    dirs_to_scan.append(d)
        except Exception:
            pass

    if not dirs_to_scan and STEAM_APPS.is_dir():
        dirs_to_scan = [STEAM_APPS]

    for d in dirs_to_scan:
        try:
            for name in os.listdir(d):
                m = re.match(r"appmanifest_(\d+)\.acf", name)
                if m:
                    installed.add(m.group(1))
        except Exception:
            pass

    return installed


def get_owned_app_ids(api_key: str, steam_id: str) -> Set[str]:
    resp = requests.get(
        STEAM_OWNED_API,
        params={"key": api_key, "steamid": steam_id, "format": "json"},
        timeout=10,
    )
    resp.raise_for_status()
    games = resp.json().get("response", {}).get("games", [])
    return {str(g["appid"]) for g in games}


def fetch_protondb_rating(app_id: str) -> tuple:
    try:
        resp = requests.get(PROTONDB_API.format(app_id), timeout=5)
        if resp.status_code == 404:
            return "pending", 0
        resp.raise_for_status()
        data = resp.json()
        return data.get("tier", "pending"), data.get("total", 0)
    except Exception:
        return "pending", 0


def fetch_capsule_image(app_id: str) -> Optional[str]:
    path = IMAGES_DIR / f"{app_id}.jpg"
    if path.exists():
        return str(path)
    try:
        resp = requests.get(STEAM_CAPSULE_URL.format(app_id), timeout=5)
        resp.raise_for_status()
        path.write_bytes(resp.content)
        return str(path)
    except Exception:
        return None


def search_steam(query: str, max_results: int) -> List[Dict]:
    resp = requests.get(
        STEAM_SEARCH_API,
        params={
            "term": query,
            "category1": 998,  # games only (excludes DLC, soundtracks, demos)
            "supportedlang": "english",
            "ndl": 1,
            "force_infinite": 1,
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
        timeout=5,
    )
    resp.raise_for_status()
    app_ids = re.findall(r'data-ds-appid="(\d+)"', resp.text)
    names   = re.findall(r'<span class="title">([^<]+)</span>', resp.text)
    return [{"app_id": aid, "name": name} for aid, name in zip(app_ids[:max_results], names[:max_results])]


def get_rating(app_id: str, cache: RatingsCache, ttl_hours: int) -> tuple:
    cached = cache.get(app_id, ttl_hours)
    if cached:
        return cached["tier"], cached["report_count"]
    tier, count = fetch_protondb_rating(app_id)
    try:
        cache.set(app_id, tier, count)
    except Exception:
        pass
    return tier, count


def meets_min_rating(tier: str, min_rating: str) -> bool:
    return TIER_RANK.get(tier, 5) <= MIN_TIER_RANK.get(min_rating, 5)


def format_description(game: Game) -> str:
    emoji = TIER_EMOJI.get(game.tier, "❓")
    tier_label = game.tier.capitalize() if game.tier else "Pending"
    reports = f"{game.report_count} report{'s' if game.report_count != 1 else ''}"

    if game.installed:
        status = "  ·  ✓ Installed"
    elif game.owned:
        status = "  ·  In library"
    else:
        status = ""

    return f"{emoji} {tier_label}  ·  {reports}{status}"


def prewarm(extension: "ProtonExtension"):
    prefs = extension.preferences
    ttl = int(prefs.get("cache_ttl_hours", "24"))

    installed = get_installed_app_ids()
    extension.installed_ids = installed

    owned: Set[str] = set()
    api_key = prefs.get("steam_api_key", "").strip()
    steam_id = prefs.get("steam_id", "").strip()
    if api_key and steam_id:
        try:
            owned = get_owned_app_ids(api_key, steam_id)
        except Exception:
            pass
    extension.owned_ids = owned

    to_fetch = [aid for aid in (installed | owned) if not extension.cache.get(aid, ttl)]

    def fetch_one(app_id: str):
        tier, count = fetch_protondb_rating(app_id)
        extension.cache.set(app_id, tier, count)
        fetch_capsule_image(app_id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        for app_id in to_fetch:
            ex.submit(fetch_one, app_id)


class ProtonExtension(Extension):
    def __init__(self):
        super().__init__()
        self.preferences: Dict = {}
        self.cache = RatingsCache()
        self.installed_ids: Set[str] = set()
        self.owned_ids: Set[str] = set()
        self.subscribe(PreferencesEvent, PreferencesEventListener())
        self.subscribe(KeywordQueryEvent, KeywordQueryEventListener())
        self.subscribe(ItemEnterEvent, ItemEnterEventListener())


class PreferencesEventListener(EventListener):
    def on_event(self, event, extension):
        extension.preferences = event.preferences
        Thread(target=prewarm, args=(extension,), daemon=True).start()


class KeywordQueryEventListener(EventListener):
    def on_event(self, event, extension):
        query = event.get_argument()

        if not query:
            return RenderResultListAction([
                ExtensionResultItem(
                    icon="images/icon.png",
                    name="Search ProtonDB game compatibility",
                    description="Type a game name to check its Linux compatibility rating",
                    on_enter=DoNothingAction(),
                )
            ])

        prefs = extension.preferences
        max_results = int(prefs.get("max_results", "8"))
        ttl = int(prefs.get("cache_ttl_hours", "24"))
        min_rating = prefs.get("min_rating", "any")

        try:
            search_results = search_steam(query, max_results)
        except Exception:
            return RenderResultListAction([
                ExtensionResultItem(
                    icon="images/icon.png",
                    name="Search failed",
                    description="Could not reach Steam. Check your connection.",
                    on_enter=DoNothingAction(),
                )
            ])

        if not search_results:
            return RenderResultListAction([
                ExtensionResultItem(
                    icon="images/icon.png",
                    name=f'No results for "{query}"',
                    description="Try a different search term",
                    on_enter=DoNothingAction(),
                )
            ])

        app_ids = [r["app_id"] for r in search_results]

        # Fetch ratings and images in parallel; requests have internal timeouts so this is bounded
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(app_ids) * 2) as ex:
            rating_futs = {aid: ex.submit(get_rating, aid, extension.cache, ttl) for aid in app_ids}
            image_futs  = {aid: ex.submit(fetch_capsule_image, aid) for aid in app_ids}

        items = []
        for r in search_results:
            aid = r["app_id"]

            try:
                tier, count = rating_futs[aid].result()
            except Exception:
                tier, count = "pending", 0

            if not meets_min_rating(tier, min_rating):
                continue

            icon = extension.cache.image_path(aid) or "images/icon.png"

            game = Game(
                app_id=aid,
                name=r["name"],
                tier=tier,
                report_count=count,
                installed=aid in extension.installed_ids,
                owned=aid in extension.owned_ids,
            )

            items.append(ExtensionResultItem(
                icon=icon,
                name=game.name,
                description=format_description(game),
                on_enter=ExtensionCustomAction(
                    {"action": "show_actions", "game": {
                        "app_id": game.app_id,
                        "name": game.name,
                        "installed": game.installed,
                        "owned": game.owned,
                    }},
                    keep_app_open=True,
                ),
            ))

        if not items:
            return RenderResultListAction([
                ExtensionResultItem(
                    icon="images/icon.png",
                    name=f'No results for "{query}"',
                    description="Try adjusting your minimum rating filter",
                    on_enter=DoNothingAction(),
                )
            ])

        return RenderResultListAction(items)


class ItemEnterEventListener(EventListener):
    def on_event(self, event, extension):
        data = event.get_data()
        if data.get("action") != "show_actions":
            return DoNothingAction()

        game = data["game"]
        app_id   = game["app_id"]
        installed = game["installed"]
        owned     = game["owned"]

        protondb_url = f"https://www.protondb.com/app/{app_id}"
        steam_url    = f"https://store.steampowered.com/app/{app_id}"

        items = []

        if installed:
            items.append(ExtensionResultItem(
                icon="images/launch.png",
                name="Launch game",
                description="Open via Steam",
                on_enter=RunScriptAction(f"xdg-open 'steam://run/{app_id}'"),
            ))
        elif owned:
            items.append(ExtensionResultItem(
                icon="images/install.png",
                name="Install game",
                description="Open Steam install dialog",
                on_enter=RunScriptAction(f"xdg-open 'steam://install/{app_id}'"),
            ))

        items += [
            ExtensionResultItem(
                icon="images/open.png",
                name="Open on ProtonDB",
                description=protondb_url,
                on_enter=OpenUrlAction(protondb_url),
            ),
            ExtensionResultItem(
                icon="images/open.png",
                name="Open on Steam",
                description=steam_url,
                on_enter=OpenUrlAction(steam_url),
            ),
            ExtensionResultItem(
                icon="images/copy.png",
                name="Copy App ID",
                description=app_id,
                on_enter=CopyToClipboardAction(app_id),
            ),
        ]

        return RenderResultListAction(items)


if __name__ == "__main__":
    ProtonExtension().run()
