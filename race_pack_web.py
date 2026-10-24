#!/usr/bin/env python3
"""Local browser UI for MotoGP weekend results."""

from __future__ import annotations

import argparse
import html
import json
import os
import socket
import threading
import time
import urllib.request
import webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import race_pack as backend


SCRIPT_DIR = Path(__file__).resolve().parent
ASSETS_DIR = SCRIPT_DIR / "assets"
LOOPBACK_HOST = "127.0.0.1"
BIND_HOST = "0.0.0.0"
BUILD_ID = "2026-10-24-pwa-v1"
SERVER_ID = f"race-pack-web:{BUILD_ID}"
DEFAULT_PORT = 8790
PWA_MANIFEST = {
    "name": "Racing Community · MotoGP",
    "short_name": "Racing Community",
    "description": "MotoGP 分站赛果、场次分类、获胜车手和赛道天气。",
    "start_url": "/?source=pwa",
    "scope": "/",
    "display": "standalone",
    "orientation": "any",
    "background_color": "#F2F2F7",
    "theme_color": "#F2F2F7",
    "icons": [
        {
            "src": "/assets/icon-192.png",
            "sizes": "192x192",
            "type": "image/png",
            "purpose": "any",
        },
        {
            "src": "/assets/icon-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "any",
        },
    ],
}

SERVICE_WORKER_JS = r"""
const CACHE_NAME = 'racing-community-shell-__BUILD__';
const CORE_ASSETS = [
  '/assets/racing-community-avatar-320.jpg',
  '/assets/icon-192.png',
  '/assets/icon-512.png',
  '/manifest.webmanifest',
  '/offline.html'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(CORE_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then(response => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then(cache => cache.put(request, copy));
          }
          return response;
        })
        .catch(async () => {
          return (await caches.match(request)) ||
            (await caches.match('/')) ||
            (await caches.match('/offline.html'));
        })
    );
    return;
  }

  event.respondWith(
    caches.match(request).then(cached => {
      if (cached) return cached;
      return fetch(request).then(response => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(request, copy));
        }
        return response;
      });
    })
  );
});
"""

WEATHER_TRACKS = {
    "THA": {"place": "武里南", "circuit": "Chang International Circuit", "lat": 14.955, "lon": 103.085},
    "BRA": {"place": "戈亚尼亚", "circuit": "Goiânia International Circuit", "lat": -16.719, "lon": -49.250},
    "USA": {"place": "奥斯汀", "circuit": "Circuit of the Americas", "lat": 30.133, "lon": -97.641},
    "SPA": {"place": "赫雷斯", "circuit": "Circuito de Jerez", "lat": 36.708, "lon": -6.034},
    "FRA": {"place": "勒芒", "circuit": "Le Mans", "lat": 47.956, "lon": 0.207},
    "CAT": {"place": "巴塞罗那", "circuit": "Circuit de Barcelona-Catalunya", "lat": 41.570, "lon": 2.261},
    "ITA": {"place": "穆杰罗", "circuit": "Mugello", "lat": 43.998, "lon": 11.372},
    "HUN": {"place": "巴拉顿公园", "circuit": "Balaton Park", "lat": 46.901, "lon": 18.075},
    "CZE": {"place": "布尔诺", "circuit": "Brno", "lat": 49.203, "lon": 16.573},
    "NED": {"place": "阿森", "circuit": "TT Circuit Assen", "lat": 52.961, "lon": 6.523},
    "GER": {"place": "萨克森灵", "circuit": "Sachsenring", "lat": 50.789, "lon": 12.776},
    "GBR": {"place": "银石", "circuit": "Silverstone", "lat": 52.073, "lon": -1.015},
    "ARA": {"place": "阿拉贡", "circuit": "MotorLand Aragón", "lat": 41.101, "lon": -0.345},
    "RSM": {"place": "米萨诺", "circuit": "Misano World Circuit", "lat": 43.961, "lon": 12.684},
    "AUT": {"place": "施皮尔伯格", "circuit": "Red Bull Ring", "lat": 47.220, "lon": 14.765},
    "JPN": {"place": "茂木", "circuit": "Mobility Resort Motegi", "lat": 36.534, "lon": 140.227},
    "INA": {"place": "曼达利卡", "circuit": "Pertamina Mandalika Circuit", "lat": -8.896, "lon": 116.299},
    "AUS": {"place": "菲利普岛", "circuit": "Phillip Island", "lat": -38.505, "lon": 145.232},
    "MAL": {"place": "雪邦", "circuit": "Sepang", "lat": 2.761, "lon": 101.738},
    "QAT": {"place": "卢赛尔", "circuit": "Lusail", "lat": 25.489, "lon": 51.454},
    "POR": {"place": "波尔蒂芒", "circuit": "Algarve International Circuit", "lat": 37.230, "lon": -8.627},
    "VAL": {"place": "瓦伦西亚", "circuit": "Circuit Ricardo Tormo", "lat": 39.486, "lon": -0.628},
}

WEATHER_WEEKENDS = {
    "THA": ("2026-02-27", "2026-03-01"),
    "BRA": ("2026-03-20", "2026-03-22"),
    "USA": ("2026-03-27", "2026-03-29"),
    "SPA": ("2026-04-24", "2026-04-26"),
    "FRA": ("2026-05-08", "2026-05-10"),
    "CAT": ("2026-05-15", "2026-05-17"),
    "ITA": ("2026-05-29", "2026-05-31"),
    "HUN": ("2026-06-05", "2026-06-07"),
    "CZE": ("2026-06-19", "2026-06-21"),
    "NED": ("2026-06-26", "2026-06-28"),
    "GER": ("2026-07-10", "2026-07-12"),
    "GBR": ("2026-08-07", "2026-08-09"),
    "ARA": ("2026-08-28", "2026-08-30"),
    "RSM": ("2026-09-11", "2026-09-13"),
    "AUT": ("2026-09-18", "2026-09-20"),
    "JPN": ("2026-10-02", "2026-10-04"),
    "INA": ("2026-10-09", "2026-10-11"),
    "AUS": ("2026-10-23", "2026-10-25"),
    "MAL": ("2026-10-30", "2026-11-01"),
    "QAT": ("2026-11-06", "2026-11-08"),
    "POR": ("2026-11-20", "2026-11-22"),
    "VAL": ("2026-11-27", "2026-11-29"),
}

SESSION_TITLES = {
    "FP1": "自由练习 1",
    "FP2": "自由练习 2",
    "FP3": "自由练习 3",
    "FP4": "自由练习 4",
    "PR": "练习赛",
    "Q1": "排位赛 Q1",
    "Q2": "排位赛 Q2",
    "SPR": "冲刺赛",
    "RAC": "正赛",
    "WUP": "热身赛",
}

SESSION_SHORT = {
    "FP1": "自由练习 1",
    "FP2": "自由练习 2",
    "FP3": "自由练习 3",
    "FP4": "自由练习 4",
    "PR": "计时练习",
    "Q1": "排位赛 Q1",
    "Q2": "排位赛 Q2",
    "SPR": "冲刺赛",
    "RAC": "正赛",
    "WUP": "热身赛",
}

CONSTRUCTOR_COLORS = {
    "Ducati": "#d71920",
    "Aprilia": "#5f259f",
    "KTM": "#f26a00",
    "Honda": "#ef646b",
    "Yamaha": "#2264d1",
}

STATUS_OVERRIDES = {
    "OUTSTND": "未完赛",
}

RIDER_PHOTO_CATEGORY = "737ab122-76e1-4081-bedb-334caaa18c70"
_PHOTO_CACHE: dict[str, Any] = {"expires": 0.0, "by_id": {}, "by_name": {}}
_PHOTO_CACHE_LOCK = threading.Lock()


def esc(value: Any, empty: str = "—") -> str:
    if value is None:
        return empty
    text = str(value).strip()
    if not text:
        return empty
    return html.escape(text, quote=True)


def event_round_label(round_number: int) -> str:
    return f"R{round_number:02d}"


def constructor_name(row: dict[str, Any]) -> str:
    return str((row.get("constructor") or {}).get("name") or "")


def constructor_color(row: dict[str, Any]) -> str:
    return CONSTRUCTOR_COLORS.get(constructor_name(row), "#70e7c1")


def lan_ip_address() -> str:
    """Best-effort local IPv4 address for a phone on the same Wi-Fi network."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            return str(probe.getsockname()[0])
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return LOOPBACK_HOST


def fetch_rider_photo_map() -> dict[str, Any]:
    """Load official transparent rider portraits and cache them for six hours."""
    with _PHOTO_CACHE_LOCK:
        if _PHOTO_CACHE["expires"] > time.monotonic():
            return _PHOTO_CACHE

        endpoint = (
            "https://api.pulselive.motogp.com/motogp/v1/content/riders/photos?"
            + urlencode(
                {
                    "tag": "sport-data-image:rider:main",
                    "motogp_category": RIDER_PHOTO_CATEGORY,
                }
            )
        )
        request = urllib.request.Request(
            endpoint,
            headers={"User-Agent": "Mozilla/5.0 MotoGP-Race-Pack/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            # Portraits are optional decoration; result tables must still render.
            return _PHOTO_CACHE

        by_id: dict[str, str] = {}
        by_name: dict[str, str] = {}
        for rider_id, photo in payload.items():
            source = photo.get("onDemandUrl") or photo.get("imageUrl")
            if not source:
                continue
            sized_url = source + (
                "&" if "?" in source else "?"
            ) + urlencode({"width": 300, "height": 450})
            by_id[str(rider_id)] = sized_url
            title = str(photo.get("title") or "")
            if "|" in title:
                rider_name = title.split("|", 1)[1].strip()
                by_name[rider_name.casefold()] = sized_url

        _PHOTO_CACHE.update(
            {
                "expires": time.monotonic() + 6 * 60 * 60,
                "by_id": by_id,
                "by_name": by_name,
            }
        )
        return _PHOTO_CACHE


def rider_photo_url(
    row: dict[str, Any],
    photo_map: dict[str, Any],
    width: int = 300,
    height: int = 450,
) -> str:
    rider = row.get("rider") or {}
    photo_url = ""
    for key in (rider.get("riders_id"), rider.get("riders_api_uuid"), rider.get("id")):
        if key and str(key) in photo_map.get("by_id", {}):
            photo_url = str(photo_map["by_id"][str(key)])
            break
    if not photo_url:
        photo_url = str(
            photo_map.get("by_name", {}).get(rider_display_name(row).casefold(), "")
        )
    if photo_url and (width, height) != (300, 450):
        photo_url = photo_url.replace(
            f"width=300&height=450",
            f"width={width}&height={height}",
        )
    return photo_url


def rider_display_name(row: dict[str, Any]) -> str:
    rider = row.get("rider") or {}
    return str(rider.get("full_name") or rider.get("name") or "未知车手")


def rider_number(row: dict[str, Any]) -> str:
    number = (row.get("rider") or {}).get("number")
    return str(number) if number is not None else "—"


def team_display_name(row: dict[str, Any]) -> str:
    return str((row.get("team") or {}).get("name") or "—")


def display_time(row: dict[str, Any]) -> str:
    if row.get("time"):
        return str(row["time"])
    return str((row.get("best_lap") or {}).get("time") or "—")


def display_gap(row: dict[str, Any]) -> str:
    position = row.get("position")
    if position is None or position == 0 or position == "":
        return "—"
    value = str((row.get("gap") or {}).get("first") or "—")
    if value not in ("—", "0.000") and not value.startswith("+"):
        return f"+{value}"
    return value


def display_points(row: dict[str, Any]) -> str:
    value = row.get("points")
    if value is None or value == "":
        return "—"
    return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)


def display_status(row: dict[str, Any]) -> str:
    raw = str(row.get("status") or "—").strip()
    mapped = STATUS_OVERRIDES.get(raw.upper()) or backend.STATUS_NAMES.get(raw.upper())
    return mapped or raw


def format_event_dates(event: dict[str, Any]) -> str:
    start = str(event.get("date_start") or "")
    end = str(event.get("date_end") or "")
    if start and end and start != end:
        return f"{start} — {end}"
    return start or end or "日期待定"


def format_session_date(value: Any) -> str:
    if not value:
        return "时间待定"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return str(value)


def current_event_state(event: dict[str, Any], api_finished: bool = False) -> str:
    """Prefer confirmed calendar dates over PulseLive's occasionally stale flag."""
    today = datetime.now().astimezone().date()
    start_text = str(event.get("date_start") or "")
    end_text = str(event.get("date_end") or start_text)
    try:
        start = date.fromisoformat(start_text)
    except ValueError:
        start = None
    try:
        end = date.fromisoformat(end_text)
    except ValueError:
        end = None

    if start and today < start:
        return "upcoming"
    if end and today > end:
        return "finished"
    if start and end and start <= today <= end:
        return "ongoing"
    return "finished" if api_finished else "upcoming"


def fetch_schedule(year: int) -> tuple[str, list[dict[str, Any]]]:
    if backend.motogp is None:
        raise backend.RacePackError(
            "缺少 pymotogp。请使用项目自带启动器打开，或重新安装依赖。"
        )

    client = backend.motogp.PulseLiveClient(cache_dir=None, timeout=20, retries=3)
    season = client.get_season(year)
    events = [
        event
        for event in client.get_events(season["id"], finished_only=False)
        if not event.get("test")
    ]
    finished_ids = {
        event["id"] for event in client.get_events(season["id"], finished_only=True)
    }
    events.sort(
        key=lambda event: (
            event.get("date_start") or event.get("date_end") or "",
            event.get("name") or "",
        )
    )

    rounds = []
    for index, event in enumerate(events, start=1):
        api_finished = event["id"] in finished_ids
        rounds.append(
            {
                "round": index,
                "event": event,
                "finished": current_event_state(event, api_finished) == "finished",
                "state": current_event_state(event, api_finished),
            }
        )
    return season["id"], rounds


BASE_CSS = r"""
:root {
  --ios-font: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", Arial, sans-serif;
  --bg: #f2f2f7;
  --bg-soft: #ffffff;
  --panel: rgba(255, 255, 255, 0.76);
  --panel-solid: #ffffff;
  --panel-2: #f7f7fa;
  --line: rgba(60, 60, 67, 0.12);
  --line-strong: rgba(60, 60, 67, 0.22);
  --text: #1c1c1e;
  --muted: #8e8e93;
  --mint: #1c1c1e;
  --mint-soft: rgba(28, 28, 30, 0.07);
  --red: #1c1c1e;
  --amber: #636366;
  --shadow: 0 4px 24px rgba(0, 0, 0, 0.04);
}

* { box-sizing: border-box; }
html { background: var(--bg); color-scheme: light; }
body {
  margin: 0;
  min-height: 100vh;
  color: var(--text);
  font-family: var(--ios-font);
  background:
    radial-gradient(circle at 78% -18%, rgba(255, 255, 255, 0.92), transparent 34rem),
    linear-gradient(180deg, #fafafa 0%, var(--bg) 34%, #ececf2 100%);
  background-color: var(--bg);
  transition: background-color .7s ease, color .7s ease;
}

a { color: inherit; text-decoration: none; }
button, input { font: inherit; }
.page-shell { width: min(1480px, calc(100% - 48px)); margin: 0 auto; padding-bottom: 64px; }

.topbar {
  min-height: 76px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  border-bottom: 1px solid rgba(60, 60, 67, 0.10);
}
.brand { display: flex; align-items: center; gap: 12px; letter-spacing: 0.05em; }
.brand-mark {
  width: 42px;
  height: 42px;
  flex: 0 0 auto;
  border: 1px solid rgba(255,255,255,.3);
  border-radius: 8px;
  object-fit: cover;
  box-shadow: 0 4px 16px rgba(0,0,0,.08);
}
.brand-main {
  font-family: "Bahnschrift Condensed", "Arial Narrow", "Microsoft YaHei UI", sans-serif;
  font-size: 22px;
  font-weight: 800;
}
.brand-main span { color: var(--red); }
.brand-sub { color: var(--muted); font-size: 11px; letter-spacing: 0.22em; }
.build-tag {
  padding: 4px 7px;
  border: 1px solid rgba(60,60,67,.14);
  border-radius: 999px;
  color: var(--mint);
  font-size: 8px;
  letter-spacing: .09em;
}
.top-meta { color: var(--muted); font-size: 12px; letter-spacing: 0.13em; text-transform: uppercase; }
.top-actions { display: flex; align-items: center; gap: 16px; }
.update-time { color: var(--muted); font-size: 11px; }
.refresh-button {
  padding: 8px 11px;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  background: rgba(255,255,255,.72);
  color: var(--mint);
  font-size: 11px;
  font-weight: 750;
  cursor: pointer;
}
.refresh-button:hover { border-color: var(--line-strong); background: #fff; }
.back-link { color: var(--muted); font-size: 13px; transition: color .2s ease; }
.back-link:hover { color: var(--mint); }

.hero {
  position: relative;
  overflow: hidden;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 330px;
  gap: 34px;
  margin-top: 28px;
  padding: 40px;
  border: 1px solid var(--line-strong);
  border-radius: 24px;
  background:
    linear-gradient(90deg, rgba(5,5,6,.98) 0%, rgba(5,5,6,.91) 38%, rgba(10,2,4,.60) 68%, rgba(5,5,6,.82) 100%),
    linear-gradient(180deg, rgba(5,5,6,.12), rgba(5,5,6,.55)),
    url("/assets/racing-community-background.jpg") center center / cover no-repeat;
  box-shadow: var(--shadow);
}
.hero::after {
  content: "";
  position: absolute;
  width: 56%;
  height: 180%;
  right: -23%;
  top: -37%;
  transform: skewX(-19deg);
  border-left: 1px solid rgba(255,255,255,.11);
  background:
    repeating-linear-gradient(90deg, transparent 0 25px, rgba(28,28,30,.035) 26px 28px),
    linear-gradient(90deg, transparent, rgba(28,28,30,.035));
  pointer-events: none;
}
.eyebrow {
  color: var(--mint);
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.22em;
  text-transform: uppercase;
}
.hero h1 {
  position: relative;
  z-index: 1;
  margin: 10px 0 16px;
  max-width: 920px;
  font-family: "Bahnschrift Condensed", "Arial Narrow", "Microsoft YaHei UI", sans-serif;
  font-size: clamp(54px, 6.3vw, 104px);
  line-height: .86;
  letter-spacing: -0.025em;
  text-transform: uppercase;
}
.hero h1 .accent { color: var(--red); }
.hero-copy { color: #b8ccca; line-height: 1.7; max-width: 780px; }
.hero-meta { display: flex; flex-wrap: wrap; gap: 12px 25px; margin-top: 24px; color: #b7c9c7; font-size: 13px; }
.hero-meta span::before { content: "◆"; margin-right: 8px; color: var(--red); font-size: 8px; }
.hero-side {
  position: relative;
  z-index: 1;
  align-self: stretch;
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 25px;
  background: rgba(2, 2, 3, 0.68);
  backdrop-filter: blur(9px);
}
.hero-round {
  font-family: "Bahnschrift Condensed", "Arial Narrow", sans-serif;
  font-size: 62px;
  line-height: .9;
  color: rgba(238,248,246,.82);
}
.hero-season { color: var(--red); font-size: 14px; letter-spacing: 0.08em; margin-top: 8px; }
.meta-list { margin-top: 34px; border-top: 1px solid var(--line); }
.meta-row { display: grid; grid-template-columns: 86px 1fr; gap: 12px; padding: 13px 0; border-bottom: 1px solid var(--line); font-size: 12px; }
.meta-row dt { color: var(--muted); text-transform: uppercase; letter-spacing: .08em; }
.meta-row dd { margin: 0; text-align: right; line-height: 1.45; }

.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 6px 10px;
  border-radius: 999px;
  background: var(--mint-soft);
  color: var(--mint);
  font-size: 11px;
  font-weight: 800;
  letter-spacing: .06em;
}
.status-pill::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; box-shadow: 0 0 12px currentColor; }
.status-pill.upcoming { color: var(--amber); background: rgba(244,180,77,.09); }
.data-warning {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-top: 16px;
  padding: 14px 18px;
  border: 1px solid rgba(244,180,77,.28);
  border-radius: 12px;
  background: rgba(244,180,77,.07);
  color: #d7c39b;
  font-size: 12px;
  line-height: 1.55;
}
.data-warning strong { flex: 0 0 auto; color: var(--amber); }

.weather-panel {
  position: relative;
  min-width: 0;
  overflow: hidden;
  margin-bottom: 22px;
  padding: 20px;
  border: 1px solid rgba(60,60,67,.10);
  border-radius: 18px;
  background:
    linear-gradient(135deg, rgba(38,15,20,.58), rgba(5,5,7,.80)),
    rgba(9,8,10,.74);
  box-shadow:
    inset 0 1px 0 rgba(255,255,255,.05),
    0 4px 24px rgba(0,0,0,.04);
  backdrop-filter: blur(16px);
}
.weather-panel::before {
  content: "";
  position: absolute;
  top: 0;
  left: 28px;
  width: 130px;
  height: 2px;
  background: linear-gradient(90deg, var(--red), transparent);
  box-shadow: none;
}
.weather-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 18px; }
.weather-head h2 { margin: 5px 0 0; font-family: "Bahnschrift Condensed", "Arial Narrow", "Microsoft YaHei UI", sans-serif; font-size: 27px; }
.weather-controls { display: flex; align-items: center; gap: 7px; min-width: 0; }
.weather-select {
  min-width: 0;
  max-width: 270px;
  padding: 9px 31px 9px 11px;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  background: #080709;
  color: var(--text);
  font-size: 11px;
  cursor: pointer;
}
.weather-refresh {
  width: 34px;
  height: 34px;
  border: 1px solid rgba(60,60,67,.14);
  border-radius: 8px;
  background: rgba(255,255,255,.8);
  color: var(--mint);
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
}
.weather-refresh:hover { background: #f7f7fa; }
.weather-refresh.spinning { animation: weather-spin .8s linear infinite; }
.weather-body { display: grid; grid-template-columns: minmax(210px, .78fr) minmax(340px, 1.5fr); gap: 18px; align-items: center; margin-top: 17px; }
.weather-now { display: flex; align-items: center; gap: 15px; min-width: 0; }
.weather-icon {
  width: 62px;
  height: 62px;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  color: var(--text);
  filter: none;
}
.weather-icon svg { width: 58px; height: 58px; }
.weather-temp { font-family: "Bahnschrift Condensed", "Arial Narrow", sans-serif; font-size: 45px; font-weight: 900; line-height: .9; }
.weather-desc { margin-top: 7px; color: #c7bec2; font-size: 12px; }
.weather-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
.weather-metric {
  min-width: 0;
  padding: 10px 11px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: rgba(255,255,255,.025);
}
.weather-metric span { display: block; color: var(--muted); font-size: 8px; letter-spacing: .12em; text-transform: uppercase; }
.weather-metric strong { display: block; margin-top: 5px; font-size: 12px; white-space: nowrap; }
.weather-weekend { margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--line); }
.weather-weekend-head { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 9px; color: var(--muted); font-size: 9px; letter-spacing: .12em; text-transform: uppercase; }
.weather-week { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 9px; }
.weather-day {
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: rgba(255,255,255,.025);
  color: var(--text);
  text-align: left;
  cursor: pointer;
  transition: border-color .18s ease, background .18s ease, transform .18s ease;
}
.weather-day:hover { transform: translateY(-1px); border-color: rgba(28,28,30,.25); }
.weather-day.active { border-color: var(--text); background: #f2f2f7; }
.weather-day-top { display: flex; justify-content: space-between; gap: 8px; color: #d6cdd1; font-size: 11px; }
.weather-day-top strong { font-size: 11px; }
.weather-day-main { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 9px; }
.weather-day-icon svg { width: 35px; height: 35px; }
.weather-day-temp { font-family: "Bahnschrift Condensed", "Arial Narrow", sans-serif; font-size: 23px; font-weight: 900; white-space: nowrap; }
.weather-day-temp small { color: var(--muted); font-family: "Segoe UI", sans-serif; font-size: 10px; font-weight: 400; }
.weather-day-meta { display: flex; flex-wrap: wrap; gap: 5px 10px; margin-top: 8px; color: var(--muted); font-size: 9px; }
.weather-day-empty { grid-column: 1 / -1; padding: 14px; border: 1px dashed var(--line); border-radius: 10px; color: var(--muted); font-size: 10px; }
.weather-hourly { display: flex; gap: 7px; overflow-x: auto; margin-top: 9px; padding-bottom: 3px; scrollbar-width: thin; }
.weather-hour {
  flex: 0 0 66px;
  padding: 8px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: rgba(255,255,255,.018);
  text-align: center;
}
.weather-hour time { color: var(--muted); font-size: 9px; }
.weather-hour .weather-day-icon { display: grid; place-items: center; height: 30px; margin: 4px 0; }
.weather-hour .weather-day-icon { color: var(--mood, var(--text)); }
.weather-hour .weather-day-icon svg { width: 25px; height: 25px; }
.weather-hour strong { display: block; font-size: 12px; }
.weather-hour small { color: var(--muted); font-size: 8px; }
.weather-foot { display: flex; justify-content: space-between; align-items: center; gap: 14px; margin-top: 15px; padding-top: 12px; border-top: 1px solid var(--line); color: var(--muted); font-size: 10px; }
.weather-location { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.weather-updated { flex: 0 0 auto; }
.weather-alert { color: var(--amber); }
.weather-panel.loading .weather-temp, .weather-panel.loading .weather-desc { color: var(--muted); }
.weather-panel.loading .weather-icon { animation: weather-pulse 1.2s ease-in-out infinite alternate; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }

@keyframes weather-spin { to { transform: rotate(360deg); } }
@keyframes weather-pulse { from { opacity: .45; transform: scale(.96); } to { opacity: 1; transform: scale(1.03); } }

.stats-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin-top: 18px; }
.stat-card {
  min-height: 118px;
  padding: 20px 22px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: linear-gradient(150deg, rgba(24,16,19,.96), rgba(8,7,9,.96));
}
.stat-label { color: var(--muted); font-size: 10px; letter-spacing: .18em; text-transform: uppercase; font-weight: 800; }
.stat-value { margin-top: 12px; font-family: "Bahnschrift Condensed", "Arial Narrow", "Microsoft YaHei UI", sans-serif; font-size: 34px; line-height: 1; }
.stat-detail { margin-top: 8px; color: var(--muted); font-size: 12px; }
.progress { height: 4px; margin-top: 16px; overflow: hidden; border-radius: 4px; background: rgba(255,255,255,.07); }
.progress span { display: block; height: 100%; border-radius: inherit; background: var(--mint); box-shadow: none; }

.weekend-layout { display: grid; grid-template-columns: 250px minmax(0, 1fr); gap: 18px; margin-top: 18px; align-items: start; }
.session-nav {
  position: sticky;
  top: 18px;
  padding: 20px;
  border: 1px solid var(--line);
  border-radius: 18px;
  background: rgba(12, 10, 12, .94);
}
.panel-kicker { color: var(--muted); font-size: 10px; letter-spacing: .18em; text-transform: uppercase; font-weight: 800; }
.session-nav h2, .schedule-title h2 { margin: 8px 0 18px; font-family: "Bahnschrift Condensed", "Arial Narrow", "Microsoft YaHei UI", sans-serif; font-size: 28px; }
.session-buttons { display: grid; gap: 6px; }
.session-button {
  width: 100%;
  display: grid;
  grid-template-columns: 44px 1fr 8px;
  align-items: center;
  gap: 10px;
  padding: 13px 14px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: transparent;
  color: var(--muted);
  text-align: left;
  cursor: pointer;
  transition: background .18s ease, color .18s ease, border-color .18s ease;
}
.session-button:hover { color: var(--text); background: rgba(255,255,255,.03); }
.session-button.active { color: var(--text); background: rgba(28,28,30,.06); border-color: var(--line-strong); }
.session-button:disabled { opacity: .46; cursor: not-allowed; }
.session-button:disabled i { background: var(--amber); box-shadow: 0 0 10px rgba(244,180,77,.45); }
.session-button b { font-size: 11px; letter-spacing: .06em; }
.session-button em { font-style: normal; font-size: 12px; }
.session-button i { width: 6px; height: 6px; border-radius: 50%; background: var(--mint); box-shadow: none; }

.session-stage {
  min-width: 0;
  padding: 25px;
  border: 1px solid var(--line);
  border-radius: 18px;
  background: rgba(8, 7, 9, .90);
}
.session-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; margin-bottom: 20px; }
.session-head h2 { margin: 4px 0 0; font-family: "Bahnschrift Condensed", "Arial Narrow", "Microsoft YaHei UI", sans-serif; font-size: clamp(36px, 4vw, 56px); line-height: 1; }
.session-date { color: var(--muted); font-size: 12px; }
.podium-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  align-items: start;
  gap: 12px;
  padding-top: 8px;
}
.podium-card {
  position: relative;
  min-height: 224px;
  overflow: hidden;
  padding: 18px;
  border: 1px solid color-mix(in srgb, var(--accent) 42%, transparent);
  border-radius: 14px;
  background:
    radial-gradient(circle at 82% 16%, color-mix(in srgb, var(--accent) 15%, transparent), transparent 8rem),
    rgba(14, 12, 14, .91);
}
.podium-1 { grid-column: 2; grid-row: 1; min-height: 300px; }
.podium-2 { grid-column: 1; grid-row: 1; min-height: 258px; margin-top: 42px; }
.podium-3 { grid-column: 3; grid-row: 1; min-height: 230px; margin-top: 78px; }
.rider-photo {
  position: absolute;
  z-index: 1;
  right: 4px;
  bottom: 52px;
  width: 49%;
  height: calc(100% - 66px);
  object-fit: contain;
  object-position: bottom center;
  filter: drop-shadow(0 12px 20px rgba(0,0,0,.4));
  pointer-events: none;
}
.podium-card::after {
  content: attr(data-number);
  position: absolute;
  right: 12px;
  bottom: -21px;
  color: rgba(255,255,255,.045);
  font-family: "Bahnschrift Condensed", "Arial Narrow", sans-serif;
  font-size: 128px;
  font-weight: 900;
  line-height: 1;
}
.podium-top { position: relative; z-index: 2; display: flex; justify-content: space-between; color: var(--muted); font-size: 9px; letter-spacing: .14em; text-transform: uppercase; }
.podium-pos { color: var(--text); font-family: "Bahnschrift Condensed", "Arial Narrow", sans-serif; font-size: 32px; font-weight: 900; }
.rider-head { position: relative; z-index: 2; display: flex; align-items: center; gap: 12px; max-width: 68%; margin-top: 22px; }
.rider-disc {
  flex: 0 0 auto;
  width: 42px;
  height: 42px;
  display: grid;
  place-items: center;
  border: 1px solid color-mix(in srgb, var(--accent) 72%, white 10%);
  border-radius: 50%;
  background: color-mix(in srgb, var(--accent) 15%, #050506);
  color: var(--text);
  font-family: "Bahnschrift Condensed", "Arial Narrow", sans-serif;
  font-size: 17px;
  font-weight: 900;
}
.rider-head h3 { margin: 0; font-size: 17px; line-height: 1.15; }
.rider-head p { margin: 5px 0 0; color: var(--muted); font-size: 11px; line-height: 1.35; }
.podium-foot { position: absolute; z-index: 3; left: 18px; right: 18px; bottom: 16px; display: flex; justify-content: space-between; gap: 10px; padding-top: 13px; border-top: 1px solid var(--line); background: linear-gradient(180deg, transparent, rgba(10,8,10,.86) 30%); }
.podium-foot small { display: block; color: var(--muted); font-size: 8px; letter-spacing: .12em; }
.podium-foot strong { display: block; margin-top: 4px; font-size: 13px; }
.constructor-dot { display: inline-block; width: 6px; height: 6px; margin-right: 6px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 8px var(--accent); }

.table-section { margin-top: 24px; }
.table-titlebar { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 10px; }
.table-titlebar h3 { margin: 0; font-size: 13px; letter-spacing: .1em; text-transform: uppercase; }
.table-titlebar span { color: var(--muted); font-size: 11px; }
.table-wrap { overflow: auto; border: 1px solid var(--line); border-radius: 12px; }
table { width: 100%; min-width: 880px; border-collapse: collapse; font-size: 12px; }
thead { position: sticky; top: 0; z-index: 2; background: #120d10; }
th { padding: 11px 12px; color: var(--muted); font-size: 9px; letter-spacing: .12em; text-align: left; text-transform: uppercase; white-space: nowrap; }
td { padding: 11px 12px; border-top: 1px solid var(--line); vertical-align: middle; }
tbody tr { transition: background .15s ease; }
tbody tr:hover { background: #f7f7fa; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.pos-cell { width: 42px; font-family: "Bahnschrift Condensed", "Arial Narrow", sans-serif; font-size: 17px; font-weight: 900; }
.table-rider { display: flex; align-items: center; gap: 10px; min-width: 160px; }
.table-avatar {
  position: relative;
  flex: 0 0 auto;
  width: 34px;
  height: 34px;
  overflow: hidden;
  display: grid;
  place-items: center;
  border: 1px solid color-mix(in srgb, var(--accent) 72%, white 10%);
  border-radius: 50%;
  background: color-mix(in srgb, var(--accent) 15%, #050506);
  color: var(--text);
  font-family: "Bahnschrift Condensed", "Arial Narrow", sans-serif;
  font-size: 12px;
  font-weight: 900;
}
.avatar-number {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
}
.table-avatar.placeholder::before {
  content: "";
  position: absolute;
  top: 6px;
  width: 11px;
  height: 11px;
  border: 1.5px solid var(--muted);
  border-radius: 50%;
}
.table-avatar.placeholder::after {
  content: "";
  position: absolute;
  bottom: 4px;
  width: 20px;
  height: 10px;
  border: 1.5px solid var(--muted);
  border-radius: 10px 10px 5px 5px;
}
.table-avatar img {
  position: absolute;
  inset: 0;
  z-index: 1;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center top;
  transform: scale(2.35);
  transform-origin: 50% 0;
}
.table-avatar.number-only { display: grid; }
.table-rider strong { display: block; font-size: 12px; }
.table-rider small { color: var(--muted); font-size: 9px; }
.muted-cell { color: var(--muted); }
.constructor { white-space: nowrap; }
.constructor::before { content: ""; display: inline-block; width: 6px; height: 6px; margin-right: 7px; border-radius: 50%; background: var(--dot, var(--mint)); box-shadow: 0 0 8px var(--dot, var(--mint)); }
.status-ok { color: var(--mint); white-space: nowrap; }
.status-out { color: var(--amber); white-space: nowrap; }

.schedule-layout { display: grid; grid-template-columns: 320px minmax(0, 1fr); gap: 22px; margin-top: 28px; align-items: start; }
.schedule-layout > *, .weekend-layout > * { min-width: 0; }
.schedule-intro { position: sticky; top: 18px; padding: 26px; border: 1px solid var(--line); border-radius: 18px; background: rgba(11,30,33,.9); }
.schedule-intro h2 { margin: 8px 0 12px; font-family: "Bahnschrift Condensed", "Arial Narrow", "Microsoft YaHei UI", sans-serif; font-size: 42px; line-height: .98; }
.schedule-intro p { color: var(--muted); line-height: 1.65; font-size: 13px; }
.year-form { margin-top: 24px; }
.year-form label { display: block; margin-bottom: 8px; color: var(--muted); font-size: 10px; letter-spacing: .14em; text-transform: uppercase; }
.year-row { display: flex; gap: 8px; }
.year-row input {
  min-width: 0;
  flex: 1;
  padding: 11px 12px;
  border: 1px solid var(--line-strong);
  border-radius: 9px;
  background: #090809;
  color: var(--text);
}
.year-row button {
  padding: 11px 15px;
  border: 0;
  border-radius: 9px;
  background: var(--mint);
  color: #041313;
  font-weight: 800;
  cursor: pointer;
}
.phone-access { margin-top: 22px; padding-top: 20px; border-top: 1px solid var(--line); }
.phone-access p { margin: 8px 0; font-size: 11px; }
.phone-link {
  display: block;
  overflow-wrap: anywhere;
  padding: 10px 11px;
  border: 1px solid rgba(60,60,67,.12);
  border-radius: 9px;
  background: rgba(255,255,255,.68);
  color: var(--mint);
  font-family: "Cascadia Mono", Consolas, monospace;
  font-size: 10px;
  line-height: 1.45;
}
.round-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.round-card {
  position: relative;
  display: grid;
  grid-template-columns: 74px minmax(0, 1fr) auto;
  align-items: center;
  gap: 16px;
  min-height: 104px;
  padding: 16px 18px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: linear-gradient(150deg, rgba(23,16,19,.96), rgba(7,7,8,.97));
  transition: transform .18s ease, border-color .18s ease, background .18s ease;
}
a.round-card:hover { transform: translateY(-2px); border-color: rgba(28,28,30,.24); background: rgba(255,255,255,.96); }
.round-card.upcoming { opacity: .58; }
.round-card.ongoing { border-color: rgba(244,180,77,.35); }
.round-index { font-family: "Bahnschrift Condensed", "Arial Narrow", sans-serif; font-size: 35px; font-weight: 900; line-height: 1; color: rgba(238,248,246,.72); }
.round-index small { display: block; margin-top: 4px; color: var(--red); font-size: 9px; letter-spacing: .1em; }
.round-dates { color: var(--muted); font-size: 10px; font-variant-numeric: tabular-nums; }
.round-name { margin-top: 5px; font-size: 14px; font-weight: 750; line-height: 1.25; }
.round-meta { display: flex; gap: 12px; margin-top: 7px; color: var(--muted); font-size: 10px; }
.round-state { align-self: center; }
.chevron { color: var(--mint); font-size: 20px; }

.error-card { max-width: 760px; margin: 80px auto; padding: 42px; border: 1px solid var(--line-strong); border-radius: 20px; background: var(--panel); box-shadow: var(--shadow); }
.error-card h1 { font-family: "Bahnschrift Condensed", "Arial Narrow", "Microsoft YaHei UI", sans-serif; font-size: 52px; margin: 12px 0; }
.error-card p { color: var(--muted); line-height: 1.7; }
.button-link { display: inline-flex; margin-top: 18px; padding: 11px 16px; border-radius: 9px; background: var(--mint); color: #050506; font-weight: 800; }

.intro-overlay {
  position: fixed;
  z-index: 9999;
  inset: 0;
  overflow: hidden;
  display: grid;
  place-items: center;
  isolation: isolate;
  background: #080809;
  transition: opacity .65s ease, visibility .65s ease;
}
html.intro-seen .intro-overlay { display: none; }
.intro-overlay.gone { opacity: 0; visibility: hidden; pointer-events: none; }
.intro-camera {
  position: absolute;
  inset: -8%;
  z-index: -3;
  background:
    linear-gradient(90deg, rgba(0,0,0,.92) 0%, rgba(0,0,0,.47) 25%, rgba(0,0,0,.12) 55%, rgba(0,0,0,.55) 100%),
    #080809;
  transform: perspective(900px) scale(1.16) translate3d(2.5%, 1.5%, 0) rotateX(2.5deg);
  animation: intro-camera-idle 4s ease-in-out infinite alternate;
}
.intro-video {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center center;
  filter: grayscale(1) contrast(1.18) brightness(.58);
  opacity: .92;
}
.intro-camera::after {
  content: "";
  position: absolute;
  inset: 0;
  background:
    radial-gradient(circle at 82% 55%, transparent 0 15%, rgba(0,0,0,.18) 38%, rgba(0,0,0,.72) 82%),
    repeating-linear-gradient(90deg, transparent 0 56px, rgba(255,255,255,.045) 57px 59px);
  mix-blend-mode: screen;
  opacity: .55;
}
.intro-speedlines {
  position: absolute;
  z-index: -2;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
}
.intro-speedlines span {
  position: absolute;
  right: -20%;
  width: 70%;
  height: 2px;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,.88), rgba(255,255,255,.42), transparent);
  filter: blur(.4px);
  opacity: .2;
  transform: skewX(-28deg);
  animation: intro-streak 1.8s linear infinite;
}
.intro-speedlines span:nth-child(1) { top: 17%; animation-delay: -.2s; }
.intro-speedlines span:nth-child(2) { top: 27%; width: 85%; animation-delay: -.9s; }
.intro-speedlines span:nth-child(3) { top: 62%; width: 64%; animation-delay: -.5s; }
.intro-speedlines span:nth-child(4) { top: 72%; width: 92%; animation-delay: -1.3s; }
.intro-speedlines span:nth-child(5) { top: 84%; width: 55%; animation-delay: -.7s; }
.intro-wheel {
  position: absolute;
  z-index: -1;
  right: -3vw;
  bottom: -17vw;
  width: min(48vw, 620px);
  aspect-ratio: 1;
  border: 3px dashed rgba(255,255,255,.32);
  border-radius: 50%;
  opacity: .22;
  filter: blur(1px);
  animation: intro-wheel-spin 1.15s linear infinite;
}
.intro-whiteout {
  position: absolute;
  z-index: 2;
  inset: -20%;
  opacity: 0;
  pointer-events: none;
  background: radial-gradient(circle at 50% 50%, #fff 0%, #fff 22%, rgba(255,255,255,.97) 48%, rgba(255,255,255,0) 76%);
}
.intro-content {
  position: relative;
  z-index: 3;
  width: min(760px, calc(100% - 40px));
  margin-left: min(-19vw, -160px);
  transition: opacity .5s ease, transform .6s cubic-bezier(.2,.7,.2,1), filter .5s ease;
}
.intro-logo {
  width: 86px;
  height: 86px;
  object-fit: cover;
  border: 1px solid rgba(255,255,255,.36);
  border-radius: 12px;
  box-shadow: 0 8px 30px rgba(255,255,255,.16);
}
.intro-kicker {
  margin-top: 24px;
  color: rgba(255,255,255,.75);
  font-size: 11px;
  font-weight: 850;
  letter-spacing: .28em;
  text-transform: uppercase;
}
.intro-content h2 {
  margin: 8px 0 18px;
  font-family: var(--ios-font);
  font-size: clamp(58px, 8.5vw, 132px);
  font-style: normal;
  font-weight: 900;
  line-height: .80;
  letter-spacing: -.045em;
  text-shadow: 0 12px 38px rgba(0,0,0,.72);
}
.intro-content h2 { color: #fff; }
.intro-content h2 span { color: rgba(255,255,255,.66); }
.intro-subtitle { max-width: 560px; color: #beb7bb; font-size: 14px; line-height: 1.65; }
.intro-actions { display: flex; align-items: center; gap: 16px; margin-top: 30px; }
.intro-start {
  position: relative;
  min-width: 190px;
  padding: 14px 22px;
  overflow: hidden;
  border: 1px solid #fff;
  border-radius: 14px;
  background: #fff;
  color: #1c1c1e;
  font-weight: 850;
  letter-spacing: .04em;
  cursor: pointer;
  box-shadow: 0 8px 30px rgba(255,255,255,.12);
}
.intro-start::after {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(110deg, transparent 20%, rgba(0,0,0,.10) 48%, transparent 72%);
  transform: translateX(-120%);
  animation: intro-button-sweep 2.2s ease-in-out infinite;
}
.intro-skip { border: 0; background: transparent; color: #c8bec3; font-size: 12px; cursor: pointer; }
.intro-sound-note { margin-top: 10px; color: #766d72; font-size: 10px; letter-spacing: .08em; }
.intro-hud {
  position: absolute;
  z-index: 3;
  left: 28px;
  right: 28px;
  bottom: 22px;
  display: flex;
  justify-content: space-between;
  color: rgba(255,255,255,.42);
  font-family: "Cascadia Mono", Consolas, monospace;
  font-size: 9px;
  letter-spacing: .12em;
}
.intro-hud::before {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: 22px;
  height: 2px;
  background: linear-gradient(90deg, #fff, rgba(255,255,255,.12));
  transform-origin: left;
  transform: scaleX(.08);
  transition: transform .22s linear;
}
.intro-overlay.launch .intro-camera { animation: intro-launch-camera 2.5s cubic-bezier(.12,.72,.18,1) forwards; }
.intro-overlay.launch .intro-speedlines span { animation-duration: .24s; opacity: .8; }
.intro-overlay.launch .intro-wheel { animation-duration: .09s; opacity: .5; }
.intro-overlay.launch .intro-whiteout { animation: intro-white-wash 2.45s cubic-bezier(.3,.65,.2,1) forwards; }
.intro-overlay.launch .intro-content {
  opacity: 0;
  transform: translateX(-10%) scale(1.05);
  filter: blur(3px);
}
.intro-overlay.launch .intro-hud::before { animation: intro-progress 2.35s linear forwards; }

@keyframes intro-camera-idle {
  from { transform: perspective(900px) scale(1.16) translate3d(2.5%, 1.5%, 0) rotateX(2.5deg); }
  to { transform: perspective(900px) scale(1.195) translate3d(1.2%, .5%, 0) rotateX(1.5deg); }
}
@keyframes intro-launch-camera {
  0% { transform: perspective(900px) scale(1.16) translate3d(2.5%, 1.5%, 0) rotateX(2.5deg); filter: blur(0); }
  18% { transform: perspective(900px) scale(1.20) translate3d(2%, 1%, 0) rotateX(2deg) translateY(1px); filter: blur(.3px); }
  55% { transform: perspective(900px) scale(1.30) translate3d(-1%, -1%, 0) rotateX(0deg) translateY(-2px); filter: blur(.8px); }
  100% { transform: perspective(900px) scale(1.58) translate3d(-8%, -4%, 0) rotateX(-3deg); filter: blur(2px); opacity: .5; }
}
@keyframes intro-streak {
  from { transform: translateX(42%) skewX(-28deg); }
  to { transform: translateX(-145%) skewX(-28deg); }
}
@keyframes intro-wheel-spin { to { transform: rotate(360deg); } }
@keyframes intro-button-sweep {
  0%, 35% { transform: translateX(-120%); }
  70%, 100% { transform: translateX(120%); }
}
@keyframes intro-progress { to { transform: scaleX(1); } }
@keyframes intro-white-wash {
  0%, 18% { opacity: 0; transform: scale(.55); }
  62% { opacity: .48; transform: scale(.92); }
  100% { opacity: 1; transform: scale(1.55); }
}

@media (max-width: 1080px) {
  .hero { grid-template-columns: 1fr; }
  .hero-side { display: grid; grid-template-columns: 150px 1fr; gap: 20px; }
  .meta-list { margin-top: 0; }
  .weekend-layout { grid-template-columns: 210px minmax(0, 1fr); }
  .weather-body { grid-template-columns: 1fr; }
  .schedule-layout { grid-template-columns: 260px minmax(0, 1fr); }
  .round-grid { grid-template-columns: 1fr; }
}

@media (max-width: 760px) {
  .page-shell { width: min(100% - 24px, 1480px); }
  .topbar { min-height: 64px; flex-wrap: wrap; align-items: center; padding: 9px 0 11px; }
  .brand-sub, .top-meta { display: none; }
  .brand { min-width: 0; gap: 9px; }
  .brand-mark { width: 38px; height: 38px; }
  .brand-main { font-size: 20px; line-height: .98; }
  .build-tag { display: none; }
  .hero { padding: 25px; border-radius: 18px; }
  .intro-camera { inset: -5%; background-position: 78% 54%; }
  .intro-video { object-position: 28% center; }
  .intro-content { width: calc(100% - 34px); margin-left: 0; }
  .intro-logo { width: 68px; height: 68px; }
  .intro-content h2 { font-size: clamp(58px, 20vw, 88px); }
  .intro-subtitle { font-size: 12px; }
  .intro-actions { align-items: flex-start; flex-direction: column; gap: 13px; }
  .intro-start { width: 100%; }
  .intro-wheel { right: -20vw; bottom: -8vw; width: 88vw; }
  .intro-hud { left: 16px; right: 16px; bottom: 15px; }
  .hero-side { display: block; }
  .hero-round { font-size: 46px; }
  .top-actions { width: 100%; justify-content: space-between; gap: 10px; margin-top: 8px; }
  .update-time { display: none; }
  .back-link { font-size: 11px; white-space: nowrap; }
  .refresh-button { white-space: nowrap; }
  .weather-panel { padding: 17px; }
  .weather-head { display: block; }
  .weather-controls { margin-top: 13px; }
  .weather-select { max-width: none; flex: 1; }
  .weather-body { grid-template-columns: 1fr; }
  .weather-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .weather-week { display: flex; overflow-x: auto; padding-bottom: 4px; }
  .weather-day { flex: 0 0 76%; }
  .weather-foot { align-items: flex-start; flex-direction: column; gap: 5px; }
  .stats-grid { grid-template-columns: 1fr; }
  .winner-overlay { padding: 14px; gap: 10px; }
  .winner-card {
    width: calc(100vw - 28px);
    max-height: calc(100vh - 74px);
    min-height: 0;
    display: flex;
    flex-direction: column;
    overflow-y: auto;
    border-radius: 24px;
  }
  .winner-photo-wrap {
    order: -1;
    width: 100%;
    min-height: 245px;
    max-height: 245px;
    flex: 0 0 245px;
    overflow: hidden;
  }
  .winner-photo {
    width: auto;
    max-width: 82%;
    height: 245px;
    object-fit: contain;
    object-position: bottom center;
  }
  .winner-copy { order: 1; padding: 4px 25px 30px; }
  .winner-session { margin-top: 10px; }
  .winner-copy h2 { font-size: clamp(34px, 10vw, 48px); }
  .winner-stats { margin-top: 20px; }
  .winner-word { left: 18px; bottom: 4px; font-size: 70px; }
  .weekend-layout, .schedule-layout { grid-template-columns: 1fr; }
  .session-nav, .schedule-intro { position: static; }
  .session-buttons { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .session-stage { padding: 18px; }
  .session-head { display: block; }
  .session-date { margin-top: 8px; }
  .data-warning { align-items: flex-start; flex-direction: column; gap: 5px; }
  .podium-grid { grid-template-columns: 1fr; padding-top: 0; }
  .podium-card.podium-1 { grid-column: 1; grid-row: 1; min-height: 240px; margin-top: 0; }
  .podium-card.podium-2 { grid-column: 1; grid-row: 2; min-height: 220px; margin-top: 0; }
  .podium-card.podium-3 { grid-column: 1; grid-row: 3; min-height: 210px; margin-top: 0; }
  .round-card { grid-template-columns: 62px 1fr auto; }
}

/* iOS light visual system: monochrome surfaces with team-color identity retained. */
body, button, input, select, textarea,
.brand-main, .hero h1, .stat-value, .session-nav h2, .schedule-title h2,
.session-head h2, .podium-pos, .rider-disc, .weather-temp, .weather-day-temp,
.round-index, .pos-cell, .error-card h1, .intro-content h2 {
  font-family: var(--ios-font) !important;
}
.page-shell { width: min(1420px, calc(100% - 56px)); }
.topbar {
  position: sticky;
  top: 0;
  z-index: 30;
  min-height: 78px;
  padding: 0 18px;
  border-bottom: 1px solid rgba(60,60,67,.10);
  border-radius: 0 0 20px 20px;
  background: rgba(242,242,247,.78);
  backdrop-filter: blur(24px) saturate(150%);
}
.brand-main, .brand-main span { color: var(--text); font-weight: 750; letter-spacing: -.02em; }
.brand-sub { color: var(--muted); letter-spacing: .12em; }
.brand-mark { border: 1px solid rgba(60,60,67,.14); border-radius: 12px; box-shadow: 0 4px 16px rgba(0,0,0,.08); }
.build-tag { border-color: rgba(60,60,67,.16); background: rgba(255,255,255,.58); color: var(--text); }
.update-time, .top-meta, .back-link { color: var(--muted); }
.refresh-button {
  border-color: rgba(60,60,67,.14);
  background: rgba(255,255,255,.72);
  color: var(--text);
  box-shadow: 0 2px 10px rgba(0,0,0,.03);
}
.refresh-button:hover { border-color: rgba(28,28,30,.32); background: #fff; }
.hero {
  margin-top: 28px;
  padding: 44px;
  border: 1px solid rgba(60,60,67,.10);
  border-radius: 24px;
  background: rgba(255,255,255,.72);
  box-shadow: var(--shadow);
  backdrop-filter: blur(20px) saturate(120%);
}
.hero::after {
  width: 36%;
  height: 180%;
  right: -20%;
  top: -40%;
  transform: skewX(-18deg);
  border-left: 1px solid rgba(60,60,67,.05);
  background:
    repeating-linear-gradient(90deg, transparent 0 34px, rgba(28,28,30,.025) 35px 36px),
    linear-gradient(90deg, transparent, rgba(28,28,30,.03));
}
.eyebrow, .panel-kicker { color: var(--muted); letter-spacing: .16em; }
.hero h1 { color: var(--text); font-weight: 750; letter-spacing: -.045em; text-transform: none; }
.hero h1 .accent { color: var(--muted); }
.hero-copy { color: #636366; }
.hero-meta { color: var(--muted); }
.hero-meta span::before { color: var(--text); }
.hero-side {
  border-color: rgba(60,60,67,.10);
  border-radius: 20px;
  background: rgba(255,255,255,.68);
  box-shadow: 0 4px 24px rgba(0,0,0,.035);
  backdrop-filter: blur(20px);
}
.hero-round { color: var(--text); font-weight: 700; }
.hero-season { color: var(--muted); }
.meta-list { border-top-color: var(--line); }
.meta-row { border-bottom-color: var(--line); }
.meta-row dt { color: var(--muted); }
.meta-row dd { color: var(--text); }
.status-pill {
  border: 1px solid rgba(60,60,67,.10);
  background: rgba(28,28,30,.06);
  color: var(--text);
}
.status-pill.upcoming { background: rgba(142,142,147,.09); color: var(--muted); }
.data-warning {
  border-color: rgba(60,60,67,.12);
  background: rgba(255,255,255,.72);
  color: #636366;
  box-shadow: var(--shadow);
  backdrop-filter: blur(20px);
}
.data-warning strong, .weather-alert { color: var(--text); }
.weather-panel, .stat-card, .session-nav, .session-stage, .schedule-intro,
.round-card, .error-card {
  border: 1px solid rgba(60,60,67,.10);
  border-radius: 20px;
  background: rgba(255,255,255,.74);
  box-shadow: 0 4px 24px rgba(0,0,0,.04);
  backdrop-filter: blur(20px) saturate(120%);
}
.weather-panel::before { background: linear-gradient(90deg, var(--text), transparent); box-shadow: none; }
.weather-head h2, .schedule-intro h2, .session-nav h2, .session-stage h2 { color: var(--text); }
.weather-select, .year-row input {
  border-color: rgba(60,60,67,.14);
  background: rgba(255,255,255,.8);
  color: var(--text);
}
.weather-refresh {
  border-color: rgba(60,60,67,.14);
  background: rgba(255,255,255,.8);
  color: var(--text);
}
.weather-refresh:hover { background: #f7f7fa; }
.weather-icon { color: var(--text); filter: none; }
.weather-temp, .weather-day-temp { color: var(--text); }
.weather-desc, .weather-metric span, .weather-foot, .weather-day-meta, .weather-hour time, .weather-hour small { color: var(--muted); }
.weather-metric, .weather-day, .weather-hour {
  border-color: rgba(60,60,67,.10);
  background: rgba(255,255,255,.56);
}
.weather-day:hover { border-color: rgba(28,28,30,.25); }
.weather-day.active { border-color: var(--text); background: #f2f2f7; }
.weather-weekend, .weather-foot { border-top-color: var(--line); }
.weather-weekend-head { color: var(--muted); }
.weather-day-empty { border-color: var(--line-strong); color: var(--muted); }
.stat-label, .stat-detail { color: var(--muted); }
.stat-value { color: var(--text); font-weight: 700; }
.progress { background: #e5e5ea; }
.progress span { background: var(--text); box-shadow: none; }
.session-buttons { gap: 5px; }
.session-button { color: var(--muted); border-radius: 12px; }
.session-button b { font-weight: 650; }
.session-button:hover { color: var(--text); background: #f7f7fa; }
.session-button.active {
  color: #fff;
  border-color: var(--text);
  background: var(--text);
}
.session-button.active em, .session-button.active b { color: #fff; }
.session-button i { background: var(--muted); box-shadow: none; }
.session-button.active i { background: #fff; }
.session-button:disabled { color: #aeaeb2; }
.session-button:disabled i { background: #c7c7cc; box-shadow: none; }
.session-date { color: var(--muted); }
.podium-card {
  border: 1px solid color-mix(in srgb, var(--accent) 42%, rgba(60,60,67,.12));
  background:
    radial-gradient(circle at 82% 16%, color-mix(in srgb, var(--accent) 7%, transparent), transparent 8rem),
    rgba(255,255,255,.78);
  box-shadow: 0 4px 24px rgba(0,0,0,.035);
  backdrop-filter: blur(16px);
}
.podium-card::after { color: rgba(28,28,30,.045); }
.podium-top { color: var(--muted); }
.podium-pos, .rider-head h3 { color: var(--text); }
.rider-head p { color: var(--muted); }
.rider-disc {
  border-color: color-mix(in srgb, var(--accent) 48%, rgba(60,60,67,.16));
  background: color-mix(in srgb, var(--accent) 9%, #fff);
  color: var(--text);
  font-weight: 700;
}
.podium-foot {
  border-top-color: var(--line);
  background: linear-gradient(180deg, transparent, rgba(255,255,255,.90) 30%);
}
.podium-foot small { color: var(--muted); }
.podium-foot strong { color: var(--text); }
.table-wrap { border-color: rgba(60,60,67,.10); }
thead { background: rgba(242,242,247,.94); }
th { color: var(--muted); }
td { border-top-color: rgba(60,60,67,.09); color: var(--text); }
tbody tr { background: rgba(255,255,255,.42); }
tbody tr:hover { background: #f7f7fa; }
.table-avatar {
  border-color: color-mix(in srgb, var(--accent) 45%, rgba(60,60,67,.16));
  background: color-mix(in srgb, var(--accent) 8%, #fff);
  color: var(--text);
}
.avatar-number { color: var(--text); }
.table-rider strong { color: var(--text); }
.table-rider small, .muted-cell { color: var(--muted); }
.constructor { color: #3a3a3c; }
.status-ok { color: var(--text); }
.status-out { color: var(--muted); }
.schedule-intro p, .round-dates, .round-meta { color: var(--muted); }
.year-row button, .button-link { background: var(--text); color: #fff; }
.phone-access { border-top-color: var(--line); }
.phone-link {
  border-color: rgba(60,60,67,.12);
  background: rgba(255,255,255,.68);
  color: var(--text);
}
.round-card { background: rgba(255,255,255,.74); }
a.round-card:hover {
  border-color: rgba(28,28,30,.24);
  background: rgba(255,255,255,.96);
  box-shadow: 0 8px 28px rgba(0,0,0,.055);
}
.round-card.upcoming { opacity: .58; }
.round-card.ongoing { border-color: rgba(28,28,30,.22); }
.round-index, .round-name { color: var(--text); }
.round-index small { color: var(--muted); }
.chevron { color: var(--text); }
.error-card p { color: var(--muted); }
.intro-overlay .intro-content h2 { font-style: normal; letter-spacing: -.05em; }

/* Colorful, weather-reactive atmosphere inside the light glass card. */
.weather-panel > :not(.weather-fx):not(script) { position: relative; z-index: 1; }
.weather-fx { position: absolute; z-index: 0; inset: 0; overflow: hidden; pointer-events: none; }
.weather-fx > * { position: absolute; opacity: 0; transition: opacity .45s ease; }
.weather-canvas {
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  transition: opacity .55s ease;
}
.fx-sun {
  top: -58px;
  right: 7%;
  width: 190px;
  height: 190px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(255,204,0,.36), rgba(255,214,10,.09) 52%, transparent 72%);
}
.fx-sun::before,
.fx-sun::after {
  content: "";
  position: absolute;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(255,210,35,.20), transparent 70%);
  filter: blur(9px);
}
.fx-sun::before { width: 120px; height: 120px; left: -82px; top: 92px; }
.fx-sun::after { width: 92px; height: 92px; right: -70px; top: 18px; }
.fx-cloud {
  top: 18px;
  width: 205px;
  height: 78px;
  border-radius: 50%;
  background:
    radial-gradient(circle at 24% 66%, rgba(185,207,228,.48) 0 29%, transparent 31%),
    radial-gradient(circle at 49% 42%, rgba(207,224,240,.58) 0 37%, transparent 39%),
    radial-gradient(circle at 73% 65%, rgba(191,211,229,.46) 0 31%, transparent 33%),
    radial-gradient(ellipse at 50% 80%, rgba(218,230,241,.42) 0 54%, transparent 57%);
  filter: blur(5px);
}
.fx-cloud::before,
.fx-cloud::after {
  content: "";
  position: absolute;
  border-radius: 50%;
  background: rgba(203,220,235,.54);
  filter: blur(5px);
}
.fx-cloud::before { width: 74px; height: 48px; left: 22px; top: -19px; }
.fx-cloud::after { width: 60px; height: 40px; right: 21px; top: -12px; }
.fx-cloud-a { right: 8%; }
.fx-cloud-a { right: 4%; }
.fx-cloud-b { top: 92px; right: 27%; width: 142px; height: 56px; }
.fx-rain, .fx-snow, .fx-wind { inset: 0; }
.fx-rain i, .fx-snow i { position: absolute; top: -15%; display: block; opacity: 0; }
.fx-rain i {
  width: 2px;
  height: 44px;
  background: linear-gradient(180deg, transparent, rgba(45,127,249,.72));
  transform: rotate(12deg);
  animation: weather-rainfall 1.15s linear infinite;
}
.fx-rain i:nth-child(1) { left: 4%; animation-delay: -.2s; }
.fx-rain i:nth-child(2) { left: 13%; animation-delay: -.7s; }
.fx-rain i:nth-child(3) { left: 21%; animation-delay: -.4s; }
.fx-rain i:nth-child(4) { left: 30%; animation-delay: -.9s; }
.fx-rain i:nth-child(5) { left: 39%; animation-delay: -.1s; }
.fx-rain i:nth-child(6) { left: 48%; animation-delay: -.55s; }
.fx-rain i:nth-child(7) { left: 56%; animation-delay: -.85s; }
.fx-rain i:nth-child(8) { left: 64%; animation-delay: -.32s; }
.fx-rain i:nth-child(9) { left: 72%; animation-delay: -.76s; }
.fx-rain i:nth-child(10) { left: 80%; animation-delay: -.15s; }
.fx-rain i:nth-child(11) { left: 88%; animation-delay: -.62s; }
.fx-rain i:nth-child(12) { left: 96%; animation-delay: -.42s; }
.fx-wind i {
  position: absolute;
  left: -20%;
  width: 46%;
  height: 48px;
  border-top: 2px solid rgba(0,132,158,.24);
  border-radius: 50%;
  background: transparent;
  filter: blur(.35px);
  animation: weather-wind 2.7s ease-in-out infinite;
}
.fx-wind i:nth-child(1) { top: 22%; animation-delay: -.2s; }
.fx-wind i:nth-child(2) { top: 39%; width: 58%; animation-delay: -.9s; }
.fx-wind i:nth-child(3) { top: 60%; width: 39%; animation-delay: -.5s; }
.fx-snow i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: rgba(106,169,233,.58);
  animation: weather-snowfall 2.4s linear infinite;
}
.fx-snow i:nth-child(1) { left: 10%; animation-delay: -.3s; }
.fx-snow i:nth-child(2) { left: 22%; animation-delay: -1.1s; }
.fx-snow i:nth-child(3) { left: 34%; animation-delay: -.7s; }
.fx-snow i:nth-child(4) { left: 46%; animation-delay: -1.6s; }
.fx-snow i:nth-child(5) { left: 58%; animation-delay: -.5s; }
.fx-snow i:nth-child(6) { left: 70%; animation-delay: -1.3s; }
.fx-snow i:nth-child(7) { left: 82%; animation-delay: -.9s; }
.fx-snow i:nth-child(8) { left: 92%; animation-delay: -.2s; }
.fx-fog {
  inset: 34% -10%;
  background: repeating-linear-gradient(180deg, transparent 0 24px, rgba(142,142,147,.08) 25px 31px);
  filter: blur(3px);
}
.fx-lightning {
  inset: 0;
  background: radial-gradient(circle at 62% 18%, rgba(108,92,231,.20), transparent 45%);
}
.weather-panel.mood-sunny .fx-sun { opacity: 1; animation: weather-sun-pulse 3.4s ease-in-out infinite alternate; }
.weather-panel.mood-cloudy .fx-cloud,
.weather-panel.mood-rainy .fx-cloud,
.weather-panel.mood-stormy .fx-cloud,
.weather-panel.mood-snowy .fx-cloud { opacity: .92; animation: weather-cloud-drift 9s ease-in-out infinite alternate; }
.weather-panel.mood-rainy .weather-canvas { opacity: 1; }
.weather-panel.mood-windy .fx-wind { opacity: 1; }
.weather-panel.mood-windy .fx-wind i { opacity: 1; }
.weather-panel.mood-snowy .fx-snow { opacity: 1; }
.weather-panel.mood-snowy .fx-snow i { opacity: 1; }
.weather-panel.mood-foggy .fx-fog { opacity: 1; animation: weather-fog-drift 6s ease-in-out infinite alternate; }
.weather-panel.mood-stormy .fx-lightning { opacity: 1; animation: weather-lightning 5.2s steps(1,end) infinite; }
.weather-panel.mood-sunny {
  border-color: rgba(229,165,0,.30);
  background: linear-gradient(145deg, #fff2ba 0%, #e8f4ff 52%, rgba(255,255,255,.92) 100%);
}
.weather-panel.mood-cloudy {
  background: linear-gradient(145deg, #d7e6f4 0%, #edf5fb 48%, rgba(255,255,255,.92) 100%);
}
.weather-panel.mood-rainy {
  border-color: rgba(45,127,249,.26);
  background: linear-gradient(145deg, #c7ddec 0%, #e8f2fa 52%, rgba(255,255,255,.92) 100%);
}
.weather-panel.mood-stormy {
  border-color: rgba(108,92,231,.28);
  background: linear-gradient(145deg, #d9d4f3 0%, #f0eefb 52%, rgba(255,255,255,.92) 100%);
}
.weather-panel.mood-snowy {
  background: linear-gradient(145deg, #dcecf9 0%, #f2f8fd 52%, rgba(255,255,255,.92) 100%);
}
.weather-panel.mood-foggy {
  background: linear-gradient(145deg, #e4e8eb 0%, #f4f6f8 52%, rgba(255,255,255,.92) 100%);
}
.weather-panel.mood-sunny .weather-icon { color: #df9f00; }
.weather-panel.mood-rainy .weather-icon { color: #2d7ff9; }
.weather-panel.mood-stormy .weather-icon { color: #6c5ce7; }
.weather-panel.mood-cloudy .weather-icon { color: #66788a; }
.weather-panel.mood-snowy .weather-icon { color: #5c93d7; }
.weather-panel.mood-sunny { box-shadow: 0 8px 28px rgba(229,170,0,.08); }
.weather-panel.mood-rainy, .weather-panel.mood-stormy { box-shadow: 0 8px 28px rgba(45,127,249,.09); }
.weather-panel.mood-windy { box-shadow: 0 8px 28px rgba(0,168,168,.09); }

@keyframes weather-rainfall {
  from { transform: translate3d(0,-20px,0) rotate(12deg); }
  to { transform: translate3d(-18px,680px,0) rotate(12deg); }
}
@keyframes weather-wind {
  from { transform: translateX(0); opacity: 0; }
  20%, 75% { opacity: 1; }
  to { transform: translateX(230%); opacity: 0; }
}
@keyframes weather-snowfall {
  from { transform: translate3d(0,-12px,0); }
  to { transform: translate3d(26px,520px,0); }
}
@keyframes weather-sun-pulse {
  from { transform: scale(.92); opacity: .65; }
  to { transform: scale(1.12); opacity: 1; }
}
@keyframes weather-cloud-drift {
  from { transform: translateX(-10px); }
  to { transform: translateX(16px); }
}
@keyframes weather-fog-drift {
  from { transform: translateX(-4%); }
  to { transform: translateX(4%); }
}
@keyframes weather-lightning {
  0%, 72%, 76%, 100% { filter: brightness(1); }
  73%, 75% { filter: brightness(1.65); }
}

/* Lighter, more lively iOS accent layer. */
body {
  background-color: #f2f2f7;
  background-image:
    radial-gradient(circle at 8% 2%, rgba(0,122,255,.12), transparent 31rem),
    radial-gradient(circle at 92% 8%, rgba(255,45,85,.09), transparent 27rem),
    radial-gradient(circle at 50% 100%, rgba(255,204,0,.07), transparent 30rem),
    linear-gradient(180deg, #fbfbfd 0%, #f2f2f7 48%, #ededf3 100%);
}
.hero::before {
  content: "";
  position: absolute;
  z-index: 0;
  top: -170px;
  right: -70px;
  width: 390px;
  height: 390px;
  border-radius: 50%;
  background: conic-gradient(from 180deg, #007aff, #5ac8fa, #ffcc00, #ff2d55, #af52de, #007aff);
  filter: blur(70px);
  opacity: .13;
  animation: ios-ambient-spin 20s linear infinite;
  pointer-events: none;
}
.hero > * { position: relative; z-index: 1; }
.hero::after { z-index: 0; }
.eyebrow { color: #007aff; }
.stat-card { position: relative; overflow: hidden; }
.stat-card::before {
  content: "";
  position: absolute;
  top: 0;
  left: 22px;
  width: 62px;
  height: 3px;
  border-radius: 0 0 4px 4px;
  background: #007aff;
}
.stat-card:nth-child(2)::before { background: #ff9500; }
.stat-card:nth-child(3)::before { background: #af52de; }
.progress span { background: linear-gradient(90deg, #007aff, #34c759); }
.session-button.active {
  color: #fff;
  border-color: #007aff;
  background: linear-gradient(135deg, #0a84ff, #0066d6);
  box-shadow: 0 6px 16px rgba(0,122,255,.18);
}
.weather-refresh { color: #007aff; }
.year-row button, .button-link {
  background: linear-gradient(180deg, #0a84ff, #006fdb);
  color: #fff;
  box-shadow: 0 5px 14px rgba(0,122,255,.2);
}
.chevron { color: #007aff; }
.phone-link { border-color: rgba(0,122,255,.18); background: rgba(0,122,255,.055); color: #0066d6; }
a.round-card:hover { border-color: rgba(0,122,255,.28); box-shadow: 0 8px 28px rgba(0,122,255,.08); }
.round-card.ongoing { border-color: rgba(255,149,0,.38); }
.status-pill.finished { border-color: rgba(255,59,48,.16); background: rgba(255,59,48,.09); color: #d70015; }
.status-pill.ongoing { border-color: rgba(255,149,0,.20); background: rgba(255,149,0,.11); color: #c26a00; }
.status-pill.upcoming { border-color: rgba(0,122,255,.18); background: rgba(0,122,255,.09); color: #0066d6; }
.weather-day.mood-sunny { --mood: #e5a500; border-top: 2px solid rgba(229,165,0,.45); }
.weather-day.mood-cloudy { --mood: #66788a; border-top: 2px solid rgba(102,120,138,.32); }
.weather-day.mood-rainy { --mood: #2d7ff9; border-top: 2px solid rgba(45,127,249,.38); }
.weather-day.mood-stormy { --mood: #6c5ce7; border-top: 2px solid rgba(108,92,231,.42); }
.weather-day.mood-snowy { --mood: #5c93d7; border-top: 2px solid rgba(92,147,215,.36); }
.weather-day.mood-foggy { --mood: #718096; border-top: 2px solid rgba(113,128,150,.32); }
.weather-day .weather-day-icon { color: var(--mood, var(--text)); }
.weather-day.active { border-color: var(--mood, var(--text)); background: color-mix(in srgb, var(--mood, #1c1c1e) 7%, #fff); }

.winner-overlay {
  position: fixed;
  z-index: 70;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 16px;
  padding: 28px;
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
  background: rgba(242,242,247,.56);
  backdrop-filter: blur(22px) saturate(120%);
  transition: opacity .35s ease, visibility .35s ease;
}
.winner-overlay.visible { opacity: 1; visibility: visible; pointer-events: auto; }
body.winner-open { overflow: hidden; }
.winner-card {
  position: relative;
  overflow: hidden;
  width: min(900px, calc(100vw - 44px));
  min-height: 450px;
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(280px, .9fr);
  border: 1px solid color-mix(in srgb, var(--winner-accent, #1c1c1e) 26%, rgba(60,60,67,.10));
  border-radius: 28px;
  background:
    radial-gradient(circle at 92% 8%, color-mix(in srgb, var(--winner-accent, #1c1c1e) 10%, transparent), transparent 18rem),
    rgba(255,255,255,.80);
  box-shadow: 0 28px 80px rgba(0,0,0,.12);
  backdrop-filter: blur(24px) saturate(130%);
  transform: translateY(18px) scale(.97);
  transition: transform .42s cubic-bezier(.2,.75,.2,1);
}
.winner-overlay.visible .winner-card { transform: translateY(0) scale(1); }
.winner-copy {
  position: relative;
  z-index: 2;
  align-self: center;
  padding: 44px 32px 44px 46px;
}
.winner-kicker { color: var(--winner-accent, var(--text)); font-size: 10px; font-weight: 800; letter-spacing: .2em; }
.winner-session { margin-top: 20px; color: var(--muted); font-size: 12px; letter-spacing: .08em; }
.winner-copy h2 { margin: 7px 0 8px; color: var(--text); font-size: clamp(36px, 4.2vw, 62px); font-weight: 750; line-height: .96; letter-spacing: -.045em; }
.winner-team { margin: 0; color: var(--muted); font-size: 14px; line-height: 1.5; }
.winner-stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 9px; margin-top: 28px; }
.winner-stats div { padding: 11px 12px; border: 1px solid var(--line); border-radius: 12px; background: rgba(255,255,255,.6); }
.winner-stats span { display: block; color: var(--muted); font-size: 8px; letter-spacing: .12em; text-transform: uppercase; }
.winner-stats strong { display: block; margin-top: 5px; color: var(--text); font-size: 13px; white-space: nowrap; }
.winner-photo-wrap {
  position: relative;
  z-index: 1;
  min-height: 420px;
  display: flex;
  align-items: flex-end;
  justify-content: center;
}
.winner-photo {
  width: min(100%, 360px);
  height: 430px;
  object-fit: contain;
  object-position: bottom center;
  filter: drop-shadow(0 18px 30px rgba(0,0,0,.16));
  opacity: 0;
  transition: opacity .28s ease;
}
.winner-photo.loaded { opacity: 1; }
.winner-word {
  position: absolute;
  left: 36px;
  bottom: -13px;
  z-index: 0;
  color: rgba(28,28,30,.035);
  font-size: clamp(74px, 10vw, 146px);
  font-weight: 800;
  letter-spacing: -.07em;
  pointer-events: none;
}
.winner-card.no-photo { grid-template-columns: 1fr; }
.winner-card.no-photo .winner-photo-wrap { display: none; }
.winner-dismiss { color: var(--muted); font-size: 11px; letter-spacing: .04em; text-align: center; }

@keyframes ios-ambient-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .weather-fx *, .hero::before { animation: none !important; }
}

@media (max-width: 760px) {
  .winner-card {
    width: calc(100vw - 28px);
    max-height: calc(100vh - 74px);
    min-height: 0;
    display: flex;
    flex-direction: column;
    overflow-y: auto;
    border-radius: 24px;
  }
  .winner-photo-wrap {
    order: -1;
    width: 100%;
    min-height: 245px;
    max-height: 245px;
    flex: 0 0 245px;
    overflow: hidden;
  }
  .winner-photo {
    width: auto;
    max-width: 82%;
    height: 245px;
    object-fit: contain;
    object-position: bottom center;
  }
  .winner-copy { order: 1; padding: 4px 25px 30px; }
  .winner-session { margin-top: 10px; }
  .winner-copy h2 { font-size: clamp(34px, 10vw, 48px); }
  .winner-stats { margin-top: 20px; }
  .winner-word { left: 18px; bottom: 4px; font-size: 70px; }
}
"""

WEATHER_JS = r"""
(() => {
  const panel = document.getElementById('weather-panel');
  if (!panel) return;
  const trackDataNode = document.getElementById('weather-track-data');
  const tracks = JSON.parse(trackDataNode.textContent);
  const select = document.getElementById('weather-track');
  const refresh = document.getElementById('weather-refresh');
  const temp = document.getElementById('weather-temp');
  const desc = document.getElementById('weather-desc');
  const icon = document.getElementById('weather-icon');
  const wind = document.getElementById('weather-wind');
  const gust = document.getElementById('weather-gust');
  const humidity = document.getElementById('weather-humidity');
  const rain = document.getElementById('weather-rain');
  const locationLabel = document.getElementById('weather-location');
  const updatedLabel = document.getElementById('weather-updated');
  const week = document.getElementById('weather-week');
  const hourly = document.getElementById('weather-hourly');
  const weekendRange = document.getElementById('weather-weekend-range');
  const weatherCanvas = document.getElementById('weather-canvas');
  const weatherContext = weatherCanvas ? weatherCanvas.getContext('2d') : null;
  let requestSequence = 0;
  let lastSuccessfulLoad = 0;
  let currentWeekend = null;
  let activeWeekendDate = null;
  let rainAnimationFrame = 0;
  let rainLastTime = 0;
  let rainDrops = [];

  const ICONS = {
    sun: '<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><circle cx="32" cy="32" r="10" fill="rgba(28,28,30,.06)"/><path d="M32 5v8M32 51v8M5 32h8M51 32h8M13 13l6 6M45 45l6 6M51 13l-6 6M19 45l-6 6"/></svg>',
    moon: '<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linejoin="round"><path d="M46 45c-15 8-32-4-31-20 1-9 6-16 13-20-2 13 7 25 20 28 1 4 0 8-2 12Z" fill="rgba(28,28,30,.06)"/></svg>',
    cloud: '<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M18 47h28a10 10 0 0 0 1-20 17 17 0 0 0-32-2 11 11 0 0 0 3 22Z" fill="rgba(255,255,255,.05)"/></svg>',
    rain: '<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M17 38h31a9 9 0 0 0 0-18 17 17 0 0 0-32-1 10 10 0 0 0 1 19Z" fill="rgba(255,255,255,.05)"/><path d="M19 47l-3 8M32 47l-3 8M45 47l-3 8"/></svg>',
    storm: '<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M17 37h31a9 9 0 0 0 0-18 17 17 0 0 0-32-1 10 10 0 0 0 1 19Z" fill="rgba(28,28,30,.04)"/><path d="m34 40-8 13h8l-3 9 12-15h-8l4-7Z" fill="rgba(28,28,30,.08)"/></svg>',
    snow: '<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M17 35h31a9 9 0 0 0 0-18 17 17 0 0 0-32-1 10 10 0 0 0 1 19Z" fill="rgba(28,28,30,.04)"/><path d="M20 47h.1M32 54h.1M44 47h.1M26 54h.1M38 54h.1"/></svg>',
    fog: '<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M17 29h31a9 9 0 0 0 0-18 17 17 0 0 0-32-1 10 10 0 0 0 1 19Z" fill="rgba(28,28,30,.04)"/><path d="M10 39h44M16 47h38M8 55h43"/></svg>',
  };

  const WEATHER = {
    0: ['晴', 'sun'], 1: ['大部晴朗', 'sun'], 2: ['多云', 'cloud'], 3: ['阴', 'cloud'],
    45: ['有雾', 'fog'], 48: ['雾凇', 'fog'],
    51: ['小毛毛雨', 'rain'], 53: ['毛毛雨', 'rain'], 55: ['强毛毛雨', 'rain'],
    56: ['冻毛毛雨', 'rain'], 57: ['强冻毛毛雨', 'rain'],
    61: ['小雨', 'rain'], 63: ['中雨', 'rain'], 65: ['大雨', 'rain'],
    66: ['冻雨', 'rain'], 67: ['强冻雨', 'rain'],
    71: ['小雪', 'snow'], 73: ['中雪', 'snow'], 75: ['大雪', 'snow'], 77: ['雪粒', 'snow'],
    80: ['小阵雨', 'rain'], 81: ['阵雨', 'rain'], 82: ['强阵雨', 'rain'],
    85: ['小阵雪', 'snow'], 86: ['强阵雪', 'snow'],
    95: ['雷暴', 'storm'], 96: ['雷暴伴冰雹', 'storm'], 99: ['强雷暴伴冰雹', 'storm'],
  };

  const round = value => value === null || value === undefined ? '—' : Math.round(Number(value));
  const decimal = value => value === null || value === undefined ? '—' : Number(value).toFixed(1);
  const formatTime = value => new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value));

  function weekendDates(track) {
    const dates = [];
    const cursor = new Date(`${track.weekendStart}T12:00:00Z`);
    const end = new Date(`${track.weekendEnd}T12:00:00Z`);
    while (cursor <= end && dates.length < 3) {
      dates.push(cursor.toISOString().slice(0, 10));
      cursor.setUTCDate(cursor.getUTCDate() + 1);
    }
    return dates;
  }

  function weekdayName(dateString) {
    const day = new Date(`${dateString}T12:00:00Z`).getUTCDay();
    return ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][day];
  }

  function shortDate(dateString) {
    return `${dateString.slice(5, 7)}/${dateString.slice(8, 10)}`;
  }

  function weatherIcon(code, isDay = 1) {
    const [, iconType] = WEATHER[code] || ['天气状况未知', 'cloud'];
    const iconName = iconType === 'sun' && isDay === 0 ? 'moon' : iconType;
    return ICONS[iconName] || ICONS.cloud;
  }

  function weatherMood(code) {
    const numericCode = Number(code);
    if ([0, 1].includes(numericCode)) return 'sunny';
    if (
      (numericCode >= 51 && numericCode <= 67) ||
      (numericCode >= 80 && numericCode <= 82) ||
      [95, 96, 99].includes(numericCode)
    ) return 'rainy';
    return 'cloudy';
  }

  function moodColor(mood) {
    return {
      sunny: '#e5a500',
      cloudy: '#66788a',
      rainy: '#2d7ff9',
      stormy: '#6c5ce7',
      snowy: '#5c93d7',
      foggy: '#718096',
    }[mood] || '#1c1c1e';
  }

  // Canvas 按设备像素比调整尺寸，避免高分屏上雨丝模糊。
  function resizeWeatherCanvas() {
    if (!weatherCanvas || !weatherContext) return;
    const bounds = weatherCanvas.getBoundingClientRect();
    const width = Math.max(1, Math.round(bounds.width));
    const height = Math.max(1, Math.round(bounds.height));
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    weatherCanvas.width = Math.round(width * ratio);
    weatherCanvas.height = Math.round(height * ratio);
    weatherContext.setTransform(ratio, 0, 0, ratio, 0, 0);
    const dropCount = Math.max(18, Math.round(width / 18));
    rainDrops = Array.from({ length: dropCount }, () => ({
      x: Math.random() * width,
      y: Math.random() * height,
      length: 10 + Math.random() * 15,
      speed: 55 + Math.random() * 70,
      alpha: 0.055 + Math.random() * 0.055,
    }));
  }

  // 雨丝在 Canvas 最底层匀速下落，颜色保持极浅的灰蓝色。
  function animateRain(timestamp) {
    if (!weatherContext || !weatherCanvas) return;
    if (!panel.classList.contains('mood-rainy')) {
      rainAnimationFrame = 0;
      return;
    }
    const delta = rainLastTime ? Math.min((timestamp - rainLastTime) / 1000, 0.05) : 0.016;
    rainLastTime = timestamp;
    const width = weatherCanvas.clientWidth;
    const height = weatherCanvas.clientHeight;
    weatherContext.clearRect(0, 0, width, height);
    weatherContext.lineCap = 'round';
    for (const drop of rainDrops) {
      drop.y += drop.speed * delta;
      drop.x -= drop.speed * delta * 0.055;
      if (drop.y > height + drop.length) {
        drop.y = -drop.length;
        drop.x = Math.random() * width;
      }
      if (drop.x < -8) drop.x = width + 8;
      weatherContext.globalAlpha = drop.alpha;
      weatherContext.strokeStyle = 'rgb(72, 105, 138)';
      weatherContext.lineWidth = 0.85;
      weatherContext.beginPath();
      weatherContext.moveTo(drop.x, drop.y);
      weatherContext.lineTo(drop.x - drop.length * 0.08, drop.y + drop.length);
      weatherContext.stroke();
    }
    weatherContext.globalAlpha = 1;
    rainAnimationFrame = requestAnimationFrame(animateRain);
  }

  let rainStopTimer = 0;
  function setRainAnimation(active) {
    window.clearTimeout(rainStopTimer);
    if (active && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      resizeWeatherCanvas();
      if (!rainAnimationFrame) {
        rainLastTime = 0;
        rainAnimationFrame = requestAnimationFrame(animateRain);
      }
      return;
    }
    rainStopTimer = window.setTimeout(() => {
      if (panel.classList.contains('mood-rainy')) return;
      if (rainAnimationFrame) cancelAnimationFrame(rainAnimationFrame);
      rainAnimationFrame = 0;
      if (weatherCanvas && weatherContext) {
        weatherContext.clearRect(0, 0, weatherCanvas.clientWidth, weatherCanvas.clientHeight);
      }
    }, 620);
  }

  // 根据 Open-Meteo 的 WMO 天气代码切换晴、云、雨场景，并保留风力效果。
  function setWeatherMood(code, windSpeed, gustSpeed) {
    const moods = ['sunny', 'cloudy', 'rainy', 'windy'];
    panel.classList.remove(...moods.map(mood => `mood-${mood}`));
    const mood = weatherMood(code);
    panel.classList.add(`mood-${mood}`);
    if (Number(windSpeed) >= 25 || Number(gustSpeed) >= 35) {
      panel.classList.add('mood-windy');
    }
    setRainAnimation(mood === 'rainy');
  }

  function extractWeekend(payload, track) {
    const dates = weekendDates(track);
    const daily = payload.daily || {};
    const hourlyData = payload.hourly || {};
    const dailyIndexes = dates.map(date => (daily.time || []).indexOf(date));
    if (dailyIndexes.some(index => index < 0)) {
      return null;
    }
    const selectedDaily = { time: dates };
    for (const key of [
      'weather_code',
      'temperature_2m_max',
      'temperature_2m_min',
      'precipitation_probability_max',
      'wind_speed_10m_max',
      'wind_gusts_10m_max',
    ]) {
      selectedDaily[key] = dailyIndexes.map(index => (daily[key] || [])[index] ?? null);
    }
    const hourlyIndexes = [];
    for (const [index, timeValue] of (hourlyData.time || []).entries()) {
      if (dates.includes(String(timeValue).slice(0, 10))) {
        hourlyIndexes.push(index);
      }
    }
    const selectedHourly = {};
    for (const key of [
      'time',
      'temperature_2m',
      'precipitation_probability',
      'weather_code',
      'wind_speed_10m',
      'wind_gusts_10m',
    ]) {
      selectedHourly[key] = hourlyIndexes.map(index => (hourlyData[key] || [])[index] ?? null);
    }
    return { daily: selectedDaily, hourly: selectedHourly };
  }

  function renderHourly(dateString) {
    const data = currentWeekend && currentWeekend.hourly;
    if (!data || !Array.isArray(data.time)) {
      hourly.innerHTML = '';
      return;
    }
    const preferredHours = [7, 9, 11, 13, 15, 17, 19];
    let indexes = [];
    for (const [index, timeValue] of data.time.entries()) {
      if (!String(timeValue || '').startsWith(dateString)) continue;
      const hour = Number(String(timeValue).slice(11, 13));
      if (preferredHours.includes(hour)) indexes.push(index);
    }
    if (!indexes.length) {
      indexes = data.time
        .map((timeValue, index) => String(timeValue || '').startsWith(dateString) && index % 2 === 0 ? index : -1)
        .filter(index => index >= 0);
    }
    hourly.innerHTML = indexes.map(index => {
      const code = data.weather_code[index];
      const conditions = WEATHER[code] ? WEATHER[code][0] : '未知';
      const probability = data.precipitation_probability[index];
      const mood = weatherMood(code);
      return `<div class="weather-hour" style="--mood:${moodColor(mood)}" title="${conditions}">
        <time>${String(data.time[index]).slice(11, 16)}</time>
        <div class="weather-day-icon">${weatherIcon(code, 1)}</div>
        <strong>${round(data.temperature_2m[index])}°</strong>
        <small>雨 ${probability === null || probability === undefined ? '—' : `${round(probability)}%`}</small>
        <small>风 ${decimal(data.wind_speed_10m[index])}</small>
      </div>`;
    }).join('');
  }

  function renderWeekend(track, weekend, sourceKind) {
    currentWeekend = weekend;
    weekendRange.textContent = `${shortDate(track.weekendStart)} — ${shortDate(track.weekendEnd)} · ${sourceKind}`;
    if (!weekend || !weekend.daily || !weekend.daily.time.length) {
      week.innerHTML = '<div class="weather-day-empty">该周末尚未进入 16 天预报范围，暂时无法显示逐日天气。</div>';
      hourly.innerHTML = '';
      return;
    }
    const today = new Date();
    const todayString = [
      today.getFullYear(),
      String(today.getMonth() + 1).padStart(2, '0'),
      String(today.getDate()).padStart(2, '0'),
    ].join('-');
    activeWeekendDate = weekend.daily.time.includes(todayString)
      ? todayString
      : weekend.daily.time[0];
    week.innerHTML = weekend.daily.time.map((dateString, index) => {
      const code = weekend.daily.weather_code[index];
      const conditions = WEATHER[code] ? WEATHER[code][0] : '未知';
      const probability = weekend.daily.precipitation_probability_max[index];
      const active = dateString === activeWeekendDate ? ' active' : '';
      const mood = weatherMood(code);
      return `<button class="weather-day mood-${mood}${active}" type="button" data-weather-date="${dateString}">
        <div class="weather-day-top"><strong>${weekdayName(dateString)}</strong><span>${shortDate(dateString)}</span></div>
        <div class="weather-day-main">
          <span class="weather-day-icon" title="${conditions}">${weatherIcon(code, 1)}</span>
          <span class="weather-day-temp">${round(weekend.daily.temperature_2m_max[index])}° <small>/ ${round(weekend.daily.temperature_2m_min[index])}°</small></span>
        </div>
        <div class="weather-day-meta">
          <span>雨 ${probability === null || probability === undefined ? '—' : `${round(probability)}%`}</span>
          <span>风 ${decimal(weekend.daily.wind_speed_10m_max[index])}</span>
          <span>${conditions}</span>
        </div>
      </button>`;
    }).join('');
    week.querySelectorAll('[data-weather-date]').forEach(button => {
      button.addEventListener('click', () => {
        activeWeekendDate = button.dataset.weatherDate;
        week.querySelectorAll('[data-weather-date]').forEach(item => {
          item.classList.toggle('active', item.dataset.weatherDate === activeWeekendDate);
        });
        renderHourly(activeWeekendDate);
      });
    });
    renderHourly(activeWeekendDate);
  }

  function selectedTrack() {
    return tracks[select.value] || tracks[panel.dataset.defaultTrack] || Object.values(tracks)[0];
  }

  function showLocation(code) {
    const track = tracks[code];
    if (!track) return;
    locationLabel.textContent = `${code} · ${track.place} · ${track.circuit}`;
  }

  function renderWeather(code, current, fetchedAt, sourceNote, weekend, weekendKind) {
    const [description, iconType] = WEATHER[current.weather_code] || ['天气状况未知', 'cloud'];
    const iconName = iconType === 'sun' && current.is_day === 0 ? 'moon' : iconType;
    icon.innerHTML = ICONS[iconName] || ICONS.cloud;
    setWeatherMood(current.weather_code, current.wind_speed_10m, current.wind_gusts_10m);
    temp.textContent = `${round(current.temperature_2m)}°C`;
    desc.textContent = `${description} · 体感 ${round(current.apparent_temperature)}°C`;
    wind.textContent = `${decimal(current.wind_speed_10m)} km/h`;
    gust.textContent = `${decimal(current.wind_gusts_10m)} km/h`;
    humidity.textContent = `${round(current.relative_humidity_2m)} %`;
    rain.textContent = `${decimal(current.precipitation)} mm`;
    showLocation(code);
    renderWeekend(tracks[code], weekend, weekendKind || '逐日预报');
    updatedLabel.classList.toggle('weather-alert', Boolean(sourceNote));
    const dataTime = current.time ? `数据 ${current.time.slice(11, 16)}` : '';
    updatedLabel.textContent = sourceNote || `Open-Meteo · ${dataTime} · 更新 ${formatTime(fetchedAt)}`;
  }

  function renderUnavailable() {
    icon.innerHTML = ICONS.cloud;
    setWeatherMood(0, 0, 0);
    temp.textContent = '—';
    desc.textContent = '天气暂不可用';
    wind.textContent = '— km/h';
    gust.textContent = '— km/h';
    humidity.textContent = '— %';
    rain.textContent = '— mm';
    week.innerHTML = '<div class="weather-day-empty">天气数据暂不可用，请检查网络后重试。</div>';
    hourly.innerHTML = '';
    updatedLabel.classList.add('weather-alert');
    updatedLabel.textContent = '请求失败，请检查网络后重试';
  }

  function readCache(code) {
    try {
      const raw = localStorage.getItem(`rc-weather:${code}`);
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  function writeCache(code, snapshot) {
    try {
      localStorage.setItem(`rc-weather:${code}`, JSON.stringify(snapshot));
    } catch (_) {}
  }

  async function loadWeather(code, fresh = false) {
    const track = tracks[code];
    if (!track) return;
    const sequence = ++requestSequence;
    select.value = code;
    showLocation(code);
    panel.classList.add('loading');
    refresh.classList.add('spinning');
    desc.textContent = '正在读取天气…';
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15000);
    const params = new URLSearchParams({
      latitude: track.lat,
      longitude: track.lon,
      current: 'temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m,wind_gusts_10m,is_day',
      daily: 'weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max,wind_gusts_10m_max',
      hourly: 'temperature_2m,precipitation_probability,weather_code,wind_speed_10m,wind_gusts_10m',
      wind_speed_unit: 'kmh',
      timezone: 'auto',
      past_days: '16',
      forecast_days: '16',
    });
    try {
      const response = await fetch(`https://api.open-meteo.com/v1/forecast?${params}`, {
        signal: controller.signal,
        cache: fresh ? 'no-store' : 'default',
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (sequence !== requestSequence) return;
      const fetchedAt = Date.now();
      let weekend = extractWeekend(payload, track);
      let weekendKind = '逐日预报';
      const weekendEnd = new Date(`${track.weekendEnd}T23:59:59Z`);
      const weekendIsOld = Date.now() - weekendEnd.getTime() > 5 * 24 * 60 * 60 * 1000;
      if (weekendIsOld) {
        const archiveParams = new URLSearchParams({
          latitude: track.lat,
          longitude: track.lon,
          start_date: track.weekendStart,
          end_date: track.weekendEnd,
          daily: 'weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max,wind_gusts_10m_max',
          hourly: 'temperature_2m,precipitation_probability,weather_code,wind_speed_10m,wind_gusts_10m',
          timezone: 'auto',
          wind_speed_unit: 'kmh',
        });
        try {
          const archiveResponse = await fetch(`https://archive-api.open-meteo.com/v1/archive?${archiveParams}`, {
            signal: controller.signal,
            cache: fresh ? 'no-store' : 'default',
          });
          if (archiveResponse.ok) {
            const archived = extractWeekend(await archiveResponse.json(), track);
            if (archived) {
              weekend = archived;
              weekendKind = '历史实况';
            }
          }
        } catch (_) {}
      }
      renderWeather(code, payload.current, fetchedAt, '', weekend, weekendKind);
      writeCache(code, {
        current: payload.current,
        weekend,
        weekendKind,
        fetchedAt,
      });
      lastSuccessfulLoad = fetchedAt;
    } catch (_) {
      if (sequence !== requestSequence) return;
      const cached = readCache(code);
      if (cached && cached.current) {
        renderWeather(
          code,
          cached.current,
          cached.fetchedAt,
          `网络请求失败 · 显示 ${formatTime(cached.fetchedAt)} 缓存`,
          cached.weekend,
          cached.weekendKind || '缓存预报'
        );
      } else {
        renderUnavailable();
      }
    } finally {
      if (sequence === requestSequence) {
        window.clearTimeout(timeout);
        panel.classList.remove('loading');
        refresh.classList.remove('spinning');
      }
    }
  }

  const requested = panel.dataset.defaultTrack;
  let remembered = null;
  try { remembered = localStorage.getItem('rc-weather-track'); } catch (_) {}
  const initial = tracks[requested] ? requested : (tracks[remembered] ? remembered : Object.keys(tracks)[0]);
  select.value = initial;
  select.addEventListener('change', () => {
    try { localStorage.setItem('rc-weather-track', select.value); } catch (_) {}
    loadWeather(select.value, true);
  });
  refresh.addEventListener('click', () => loadWeather(select.value, true));
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && Date.now() - lastSuccessfulLoad > 10 * 60 * 1000) {
      loadWeather(select.value, true);
    }
  });
  window.setInterval(() => {
    if (!document.hidden) loadWeather(select.value, true);
  }, 10 * 60 * 1000);
  window.addEventListener('resize', resizeWeatherCanvas);
  if ('ResizeObserver' in window) {
    new ResizeObserver(() => {
      if (panel.classList.contains('mood-rainy')) resizeWeatherCanvas();
    }).observe(panel);
  }
  loadWeather(initial, true);
})();
"""


def layout(title: str, body: str, script: str = "") -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <meta name="theme-color" content="#F2F2F7">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="default">
  <meta name="apple-mobile-web-app-title" content="Racing Community">
  <title>{esc(title)}</title>
  <link rel="manifest" href="/manifest.webmanifest">
  <link rel="icon" type="image/jpeg" href="/assets/racing-community-avatar-320.jpg">
  <link rel="apple-touch-icon" href="/assets/icon-192.png">
  <style>{BASE_CSS}</style>
  <script>
  try {{
    const forceIntro = new URLSearchParams(location.search).get('intro') === '1';
    if (!forceIntro && sessionStorage.getItem('rc-intro-seen') === '1') {{
      document.documentElement.classList.add('intro-seen');
    }}
  }} catch (_) {{}}
  </script>
</head>
<body>
  <section class="intro-overlay" id="intro-overlay" aria-label="Racing Community 开场动画">
    <div class="intro-camera">
      <video class="intro-video" id="intro-video" src="/assets/motogp-wheel-launch.mp4" poster="/assets/motogp-wheel-launch-poster.jpg" playsinline muted loop preload="auto" disablepictureinpicture></video>
    </div>
    <audio id="intro-audio" src="/assets/motorcycle-engine-start.mp3" preload="auto"></audio>
    <div class="intro-speedlines"><span></span><span></span><span></span><span></span><span></span></div>
    <div class="intro-wheel"></div>
    <div class="intro-whiteout"></div>
    <div class="intro-content">
      <img class="intro-logo" src="/assets/racing-community-avatar-320.jpg" alt="Racing Community">
      <div class="intro-kicker">MOTOGP · RACE WEEKEND EXPERIENCE</div>
      <h2>Racing<br><span>Community</span></h2>
      <p class="intro-subtitle">引擎点火，轮胎开始咬住赛道。进入本站完整赛果、领奖台与官方分类。</p>
      <div class="intro-actions">
        <button class="intro-start" id="intro-start" type="button">点击油门 · 进入</button>
        <button class="intro-skip" id="intro-skip" type="button">静音进入</button>
      </div>
      <div class="intro-sound-note">浏览器要求先点击后才能播放短暂引擎声</div>
    </div>
    <div class="intro-hud"><span>RC // MOTOGRAND PRIX DATA</span><span id="intro-status">SYSTEM READY</span></div>
  </section>
  <div class="page-shell">
{body}
  </div>
  <script>
  (() => {{
    const overlay = document.getElementById('intro-overlay');
    if (!overlay) return;
    const start = document.getElementById('intro-start');
    const skip = document.getElementById('intro-skip');
    const status = document.getElementById('intro-status');
    const video = document.getElementById('intro-video');
    const audio = document.getElementById('intro-audio');
    let launched = false;

    function playSynthEngine() {{
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) return;
      const context = new AudioContextClass();
      const now = context.currentTime;
      const master = context.createGain();
      const compressor = context.createDynamicsCompressor();
      const engineFilter = context.createBiquadFilter();
      engineFilter.type = 'lowpass';
      engineFilter.frequency.setValueAtTime(540, now);
      engineFilter.frequency.exponentialRampToValueAtTime(1250, now + 1.35);
      engineFilter.Q.value = 1.6;
      master.gain.setValueAtTime(0.0001, now);
      master.gain.exponentialRampToValueAtTime(0.072, now + 0.14);
      master.gain.exponentialRampToValueAtTime(0.050, now + 1.25);
      master.gain.exponentialRampToValueAtTime(0.0001, now + 2.55);
      engineFilter.connect(master);
      master.connect(compressor);
      compressor.connect(context.destination);

      const harmonics = [
        [42, 'sawtooth', 0.56],
        [84, 'square', 0.18],
        [126, 'sawtooth', 0.08],
      ];
      const oscillators = harmonics.map(([frequency, type, level], index) => {{
        const oscillator = context.createOscillator();
        const gain = context.createGain();
        oscillator.type = type;
        oscillator.frequency.setValueAtTime(frequency, now);
        oscillator.frequency.exponentialRampToValueAtTime(frequency * 2.05, now + 1.15);
        oscillator.frequency.exponentialRampToValueAtTime(frequency * 1.55, now + 2.4);
        oscillator.detune.value = index * 7 - 4;
        gain.gain.value = level;
        oscillator.connect(gain);
        gain.connect(engineFilter);
        oscillator.start(now);
        oscillator.stop(now + 2.65);
        return oscillator;
      }});

      const noiseLength = Math.floor(context.sampleRate * 2.5);
      const noiseBuffer = context.createBuffer(1, noiseLength, context.sampleRate);
      const noiseData = noiseBuffer.getChannelData(0);
      for (let index = 0; index < noiseLength; index += 1) {{
        noiseData[index] = (Math.random() * 2 - 1) * (1 - index / noiseLength);
      }}
      const noise = context.createBufferSource();
      const noiseFilter = context.createBiquadFilter();
      const noiseGain = context.createGain();
      noise.buffer = noiseBuffer;
      noiseFilter.type = 'bandpass';
      noiseFilter.frequency.value = 620;
      noiseFilter.Q.value = 0.75;
      noiseGain.gain.setValueAtTime(0.0001, now);
      noiseGain.gain.exponentialRampToValueAtTime(0.11, now + 0.18);
      noiseGain.gain.exponentialRampToValueAtTime(0.0001, now + 2.35);
      noise.connect(noiseFilter);
      noiseFilter.connect(noiseGain);
      noiseGain.connect(master);
      noise.start(now);
      noise.stop(now + 2.5);

      context.resume().catch(() => {{}});
      window.setTimeout(() => context.close().catch(() => {{}}), 2900);
    }}

    function playEngine() {{
      if (!audio) {{
        playSynthEngine();
        return;
      }}
      try {{
        audio.pause();
        audio.currentTime = 0;
        audio.volume = 0.0001;
        audio.playbackRate = 1.03;
        audio.play().then(() => {{
          const startedAt = performance.now();
          const shapeVolume = () => {{
            const elapsed = (performance.now() - startedAt) / 1000;
            let volume = 0.68;
            if (elapsed < 0.18) volume *= elapsed / 0.18;
            if (elapsed > 1.65) {{
              volume *= Math.max(0, 1 - (elapsed - 1.65) / 0.82);
            }}
            audio.volume = Math.max(0, Math.min(0.72, volume));
            if (elapsed < 2.47) {{
              requestAnimationFrame(shapeVolume);
            }} else {{
              audio.pause();
            }}
          }};
          requestAnimationFrame(shapeVolume);
        }}).catch(() => playSynthEngine());
      }} catch (_) {{
        playSynthEngine();
      }}
    }}

    function launch(withSound) {{
      if (launched) return;
      launched = true;
      try {{ sessionStorage.setItem('rc-intro-seen', '1'); }} catch (_) {{}}
      const currentUrl = new URL(location.href);
      if (currentUrl.searchParams.get('intro') === '1') {{
        currentUrl.searchParams.delete('intro');
        history.replaceState(null, '', currentUrl);
      }}
      start.disabled = true;
      skip.disabled = true;
      status.textContent = withSound ? 'IGNITION · ENGINE LIVE' : 'LAUNCH · SILENT';
      overlay.classList.add('launch');
      if (withSound) playEngine();
      if (video) {{
        try {{
          video.currentTime = 0.15;
          video.playbackRate = 0.72;
          const startedAt = performance.now();
          const rampPlayback = () => {{
            const progress = Math.min(1, (performance.now() - startedAt) / 2100);
            video.playbackRate = 0.72 + progress * 1.15;
            if (progress < 1) requestAnimationFrame(rampPlayback);
          }};
          video.play().then(() => requestAnimationFrame(rampPlayback)).catch(() => {{}});
        }} catch (_) {{}}
      }}
      window.setTimeout(() => {{
        if (video) video.pause();
        if (audio) audio.pause();
        overlay.classList.add('gone');
      }}, 2450);
      window.setTimeout(() => overlay.remove(), 3150);
    }}

    start.addEventListener('click', () => launch(true));
    skip.addEventListener('click', () => launch(false));
  }})();
  </script>
  <script>{WEATHER_JS}</script>
  <script>{script}</script>
  <script>
  if ('serviceWorker' in navigator) {{
    window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {{}}));
  }}
  </script>
</body>
</html>"""


def error_page(title: str, message: str, back_href: str = "/") -> str:
    body = f"""
<header class="topbar">
  <a class="brand" href="/"><img class="brand-mark" src="/assets/racing-community-avatar-320.jpg" alt="Racing Community"><span class="brand-main">MOTOGP <span>RACING COMMUNITY</span></span><span class="brand-sub">OFFICIAL SESSION RESULTS</span><span class="build-tag">LIVE · {BUILD_ID}</span></a>
  <a class="back-link" href="{esc(back_href)}">← 返回分站列表</a>
</header>
<section class="error-card">
  <div class="eyebrow">DATA UNAVAILABLE</div>
  <h1>{esc(title)}</h1>
  <p>{esc(message)}</p>
  <p>可能原因：网络连接异常，或该站数据尚未同步。请检查网络后重试，未完整同步时不会显示半截赛果。</p>
  <a class="button-link" href="{esc(back_href)}">返回重试</a>
</section>"""
    return layout(f"{title} · Racing Community", body)


def home_weather_track(rounds: list[dict[str, Any]]) -> str:
    for state in ("ongoing", "finished", "upcoming"):
        candidates = [item for item in rounds if item["state"] == state]
        for item in reversed(candidates):
            code = str((item["event"] or {}).get("short_name") or "")
            if code in WEATHER_TRACKS:
                return code
    return next(iter(WEATHER_TRACKS))


def render_weather_panel(default_track: str) -> str:
    code = default_track if default_track in WEATHER_TRACKS else next(iter(WEATHER_TRACKS))
    track = WEATHER_TRACKS[code]
    track_payload = {
        track_code: {
            **track_info,
            "weekendStart": WEATHER_WEEKENDS[track_code][0],
            "weekendEnd": WEATHER_WEEKENDS[track_code][1],
        }
        for track_code, track_info in WEATHER_TRACKS.items()
    }
    options = "".join(
        f'<option value="{esc(track_code)}"{" selected" if track_code == code else ""}>'
        f"{esc(track_code)} · {esc(track['place'])} · {esc(track['circuit'])}"
        "</option>"
        for track_code, track in WEATHER_TRACKS.items()
    )
    return f"""
<section class="weather-panel loading mood-sunny" id="weather-panel" data-default-track="{esc(code)}">
  <div class="weather-fx" aria-hidden="true">
    <div class="fx-sun"></div>
    <div class="fx-cloud fx-cloud-a"></div>
    <div class="fx-cloud fx-cloud-b"></div>
    <canvas class="weather-canvas" id="weather-canvas" aria-hidden="true"></canvas>
    <div class="fx-wind"><i></i><i></i><i></i></div>
    <div class="fx-snow"><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div>
    <div class="fx-fog"></div>
    <div class="fx-lightning"></div>
  </div>
  <div class="weather-head">
    <div><div class="panel-kicker">TRACK WEATHER · OPEN-METEO</div><h2>赛道天气</h2></div>
    <div class="weather-controls">
      <label class="sr-only" for="weather-track">选择赛道</label>
      <select class="weather-select" id="weather-track">{options}</select>
      <button class="weather-refresh" id="weather-refresh" type="button" aria-label="刷新天气" title="刷新天气">↻</button>
    </div>
  </div>
  <div class="weather-body">
    <div class="weather-now">
      <div class="weather-icon" id="weather-icon" aria-hidden="true"></div>
      <div><div class="weather-temp" id="weather-temp">—</div><div class="weather-desc" id="weather-desc">正在读取天气…</div></div>
    </div>
    <div class="weather-metrics">
      <div class="weather-metric"><span>风速</span><strong id="weather-wind">— km/h</strong></div>
      <div class="weather-metric"><span>阵风</span><strong id="weather-gust">— km/h</strong></div>
      <div class="weather-metric"><span>湿度</span><strong id="weather-humidity">— %</strong></div>
      <div class="weather-metric"><span>降水</span><strong id="weather-rain">— mm</strong></div>
    </div>
  </div>
  <div class="weather-weekend">
    <div class="weather-weekend-head"><span>比赛周末</span><span id="weather-weekend-range">周五 — 周日</span></div>
    <div class="weather-week" id="weather-week"><div class="weather-day-empty">正在读取周末预报…</div></div>
    <div class="weather-hourly" id="weather-hourly"></div>
  </div>
  <div class="weather-foot">
    <span class="weather-location" id="weather-location">{esc(code)} · {esc(track['place'])} · {esc(track['circuit'])}</span>
    <span class="weather-updated" id="weather-updated">正在连接官方天气数据…</span>
  </div>
  <script type="application/json" id="weather-track-data">{json.dumps(track_payload, ensure_ascii=False)}</script>
</section>"""


def render_home(year: int, rounds: list[dict[str, Any]], phone_url: str) -> str:
    completed = sum(1 for item in rounds if item["finished"])
    ongoing = sum(1 for item in rounds if item["state"] == "ongoing")
    updated_at = datetime.now().astimezone().strftime("%H:%M:%S")
    round_cards: list[str] = []
    for item in rounds:
        event = item["event"]
        circuit = event.get("circuit") or {}
        country = event.get("country") or {}
        state = item["state"]
        status_class = " upcoming" if state == "upcoming" else (" ongoing" if state == "ongoing" else "")
        if state == "finished":
            status_pill = '<span class="status-pill finished">已结束</span>'
        elif state == "ongoing":
            status_pill = '<span class="status-pill ongoing">进行中</span>'
        else:
            status_pill = '<span class="status-pill upcoming">尚未开始</span>'
        content = f"""
    <div class="round-index">{item['round']:02d}<small>{event_round_label(item['round'])}</small></div>
    <div>
      <div class="round-dates">{esc(format_event_dates(event))}</div>
      <div class="round-name">{esc(event.get('name') or '未知分站')}</div>
      <div class="round-meta"><span>{esc(event.get('short_name'))}</span><span>{esc(country.get('name'))}</span><span>{esc(circuit.get('name'))}</span></div>
    </div>
    <div class="round-state">{status_pill}</div>
    <div class="chevron">{'›' if state != 'upcoming' else ''}</div>"""
        if state != "upcoming":
            href = "/race?" + urlencode({"year": year, "round": item["round"]})
            round_cards.append(f'<a class="round-card{status_class}" href="{href}">{content}</a>')
        else:
            round_cards.append(f'<div class="round-card{status_class}">{content}</div>')

    body = f"""
<header class="topbar">
  <a class="brand" href="/"><img class="brand-mark" src="/assets/racing-community-avatar-320.jpg" alt="Racing Community"><span class="brand-main">MOTOGP <span>RACING COMMUNITY</span></span><span class="brand-sub">OFFICIAL SESSION RESULTS</span><span class="build-tag">LIVE · {BUILD_ID}</span></a>
  <div class="top-actions">
    <span class="update-time">更新于 {updated_at} · <span id="refresh-countdown">60 秒后自动刷新</span></span>
    <button class="refresh-button" type="button" id="refresh-now">立即刷新</button>
    <div class="top-meta">{year} SEASON · {completed:02d}/{len(rounds):02d} COMPLETE · {ongoing:02d} LIVE</div>
  </div>
</header>
<section class="hero">
  <div>
    <div class="eyebrow">SELECT A GRAND PRIX</div>
    <h1>选择分站<br><span class="accent">查看赛果</span></h1>
    <p class="hero-copy">选择已结束的分站，直接打开完整比赛周末。页面支持自由练习、练习赛、排位赛、冲刺赛、热身和正赛之间即时切换。</p>
    <div class="hero-meta">
      <span>MotoGP 最高组别</span>
      <span>{len(rounds)} 个分站</span>
      <span>官方分类数据</span>
    </div>
  </div>
  <aside class="hero-side">
    <div class="hero-round">{year}<div class="hero-season">CURRENT SEASON</div></div>
    <dl class="meta-list">
      <div class="meta-row"><dt>完成分站</dt><dd>{completed}</dd></div>
      <div class="meta-row"><dt>剩余分站</dt><dd>{max(0, len(rounds) - completed - ongoing)}</dd></div>
      <div class="meta-row"><dt>数据源</dt><dd>PulseLive · Official</dd></div>
    </dl>
  </aside>
</section>
<section class="schedule-layout">
  <aside class="schedule-intro">
    <div class="panel-kicker">SEASON CALENDAR</div>
    <h2>分站选择</h2>
    <p>点击已结束分站进入赛果页。尚未开始的分站会自动置灰。</p>
    <form class="year-form" action="/" method="get">
      <label for="year">切换赛季</label>
      <div class="year-row">
        <input id="year" name="year" type="number" min="1949" max="2200" value="{year}">
        <button type="submit">打开</button>
      </div>
    </form>
    <div class="phone-access">
      <div class="panel-kicker">PHONE · SAME WI-FI</div>
      <p>手机和电脑连接同一 Wi-Fi 后打开：</p>
      <a class="phone-link" href="{esc(phone_url)}">{esc(phone_url)}</a>
    </div>
  </aside>
  <section>
    <div class="schedule-title">
      <div class="panel-kicker">MOTOGP {year} CALENDAR</div>
      <h2>{year} 赛季</h2>
    </div>
    {render_weather_panel(home_weather_track(rounds))}
    <div class="round-grid">
      {''.join(round_cards)}
    </div>
  </section>
</section>"""
    script = r"""
const refreshButton = document.getElementById('refresh-now');
const countdown = document.getElementById('refresh-countdown');
let remaining = 60;
function updateCountdown() {
  if (countdown) countdown.textContent = `${remaining} 秒后自动刷新`;
}
if (refreshButton) refreshButton.addEventListener('click', () => location.reload());
updateCountdown();
setInterval(() => {
  if (document.hidden) return;
  remaining -= 1;
  if (remaining <= 0) {
    location.reload();
    return;
  }
  updateCountdown();
}, 1000);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    remaining = 60;
    updateCountdown();
  }
});
"""
    return layout(f"{year} 分站选择 · Racing Community", body, script)


def podium_card(
    row: dict[str, Any],
    position: int,
    photo_url: str = "",
) -> str:
    color = constructor_color(row)
    constructor = constructor_name(row) or "—"
    photo = (
        f'<img class="rider-photo" src="{esc(photo_url)}" '
        f'alt="{esc(rider_display_name(row))}" loading="lazy" '
        'referrerpolicy="no-referrer">'
        if photo_url
        else ""
    )
    return f"""
<article class="podium-card podium-{position}" data-number="{esc(rider_number(row))}" style="--accent:{color}">
  <div class="podium-top"><span>PODIUM · {position:02d}</span><span class="podium-pos">P{position}</span></div>
  {photo}
  <div class="rider-head">
    <div class="rider-disc">{esc(rider_number(row))}</div>
    <div><h3>{esc(rider_display_name(row))}</h3><p>{esc(team_display_name(row))}</p></div>
  </div>
  <div class="podium-foot">
    <div><small>{'总用时' if row.get('time') else '最佳圈速'}</small><strong>{esc(display_time(row))}</strong></div>
    <div><small>差距</small><strong>{esc(display_gap(row))}</strong></div>
    <div><small>积分</small><strong>{esc(display_points(row))}</strong></div>
  </div>
  <div class="podium-top" style="position:absolute;left:18px;bottom:76px;right:18px;color:var(--accent)">
    <span><i class="constructor-dot"></i>{esc(constructor)}</span>
  </div>
</article>"""


def result_row(row: dict[str, Any], photo_map: dict[str, Any]) -> str:
    position = row.get("position")
    rider = rider_display_name(row)
    constructor = constructor_name(row) or "—"
    color = constructor_color(row)
    photo_url = rider_photo_url(row, photo_map)
    status = display_status(row)
    status_class = "status-ok" if status in ("完赛", "已分类") else "status-out"
    avatar = (
        f'<span class="table-avatar" style="--accent:{color}">'
        f'<img src="{esc(photo_url)}" alt="" loading="lazy" '
        'referrerpolicy="no-referrer" onerror="this.remove();this.parentElement.classList.add(\'placeholder\')"></span>'
        if photo_url
        else f'<span class="table-avatar placeholder" style="--accent:{color}"></span>'
    )
    return f"""
<tr>
  <td class="pos-cell">{esc(position)}</td>
  <td>
    <div class="table-rider">
      {avatar}
      <div><strong>{esc(rider)}</strong></div>
    </div>
  </td>
  <td class="muted-cell">{esc(team_display_name(row))}</td>
  <td class="constructor" style="--dot:{color}">{esc(constructor)}</td>
  <td class="num">{esc(display_time(row))}</td>
  <td class="num">{esc(display_gap(row))}</td>
  <td class="num">{esc(display_points(row))}</td>
  <td class="num">{esc(row.get('total_laps'))}</td>
  <td class="{status_class}">{esc(status)}</td>
</tr>"""


def render_session_panel(
    item: dict[str, Any],
    active: bool,
    photo_map: dict[str, Any],
) -> str:
    session = item["session"]
    classification = item["classification"]
    code = backend.session_code(session)
    title = SESSION_TITLES.get(code, code or "未知场次")
    top3 = [row for row in classification if row.get("position") in (1, 2, 3)][:3]
    time_header = "总用时" if any(row.get("time") for row in classification) else "最佳圈速"
    hidden = "" if active else " hidden"
    podium_by_position = {
        int(row["position"]): row
        for row in top3
        if row.get("position") is not None
    }
    podium = "".join(
        podium_card(
            podium_by_position[position],
            position,
            rider_photo_url(podium_by_position[position], photo_map),
        )
        for position in (2, 1, 3)
        if position in podium_by_position
    )
    rows = "".join(result_row(row, photo_map) for row in classification)
    return f"""
<section class="session-panel" data-code="{esc(code)}"{hidden}>
  <div class="session-head">
    <div><div class="eyebrow">{esc(code)} · OFFICIAL CLASSIFICATION</div><h2>{esc(title)}</h2></div>
    <div class="session-date">{esc(format_session_date(session.get('date')))}<br><span class="status-pill finished">已结束</span></div>
  </div>
  <div class="podium-grid">{podium}</div>
  <div class="table-section">
    <div class="table-titlebar"><h3>完整官方分类</h3><span>{len(classification)} 位车手</span></div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>名次</th><th>车手</th><th>车队</th><th>厂商</th><th class="num">{time_header}</th><th class="num">时间差</th><th class="num">积分</th><th class="num">圈数</th><th>状态</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
  </div>
</section>"""


def render_race(
    year: int,
    round_number: int,
    event: dict[str, Any],
    weekend: list[dict[str, Any]],
    missing: list[dict[str, Any]],
) -> str:
    circuit = event.get("circuit") or {}
    country = event.get("country") or {}
    photo_map = fetch_rider_photo_map()
    codes = [backend.session_code(item["session"]) for item in weekend]
    default_code = "RAC" if "RAC" in codes else codes[-1]
    nav_entries: list[dict[str, Any]] = []
    for item in weekend:
        code = backend.session_code(item["session"])
        nav_entries.append(
            {
                "code": code,
                "date": item["session"].get("date") or "",
                "available": True,
            }
        )
    for item in missing:
        nav_entries.append(
            {
                "code": item["code"],
                "date": item.get("date") or "",
                "available": False,
            }
        )
    nav_entries.sort(key=lambda item: item["date"])

    buttons = []
    for item in nav_entries:
        code = item["code"]
        if item["available"]:
            active = " active" if code == default_code else ""
            buttons.append(
                f'<button class="session-button{active}" type="button" data-code="{esc(code)}">'
                f"<b>{esc(code)}</b><em>{esc(SESSION_SHORT.get(code, code))}</em><i></i></button>"
            )
        else:
            buttons.append(
                f'<button class="session-button" type="button" disabled data-missing="{esc(code)}">'
                f"<b>{esc(code)}</b><em>{esc(SESSION_SHORT.get(code, code))} · 待同步</em><i></i></button>"
            )

    panels = []
    for item in weekend:
        code = backend.session_code(item["session"])
        panels.append(
            render_session_panel(
                item,
                active=code == default_code,
                photo_map=photo_map,
            )
        )

    winner_data: dict[str, dict[str, Any]] = {}
    for item in weekend:
        code = backend.session_code(item["session"])
        first = next(
            (
                row
                for row in item["classification"]
                if row.get("position") == 1
            ),
            None,
        )
        if not first:
            continue
        winner_data[code] = {
            "code": code,
            "title": SESSION_TITLES.get(code, code),
            "name": rider_display_name(first),
            "team": team_display_name(first),
            "constructor": constructor_name(first) or "—",
            "photo": rider_photo_url(first, photo_map, width=600, height=900),
            "accent": constructor_color(first),
            "time": display_time(first),
            "gap": display_gap(first),
            "points": display_points(first),
        }

    race_item = next((item for item in weekend if backend.session_code(item["session"]) == "RAC"), None)
    winner = None
    if race_item:
        winner = next((row for row in race_item["classification"] if row.get("position") == 1), None)
    available_count = len(weekend)
    total_count = available_count + len(missing)
    progress_percent = round(available_count / total_count * 100) if total_count else 0
    state = current_event_state(event, api_finished=True)
    state_text = {
        "finished": "已结束",
        "ongoing": "进行中",
        "upcoming": "尚未开始",
    }[state]
    state_class = f" {state}"
    updated_at = datetime.now().astimezone().strftime("%H:%M:%S")
    missing_names = "、".join(
        f"{item['code']}（{SESSION_TITLES.get(item['code'], item['code'])}）"
        for item in missing
    )
    missing_notice = (
        f"""
<section class="data-warning">
  <strong>部分场次数据尚未同步</strong>
  <span>{esc(missing_names)} 暂无官方分类。当前页面已显示其余 {available_count} 个场次，并会继续自动刷新。</span>
</section>"""
        if missing
        else ""
    )

    body = f"""
<header class="topbar">
  <a class="brand" href="/"><img class="brand-mark" src="/assets/racing-community-avatar-320.jpg" alt="Racing Community"><span class="brand-main">MOTOGP <span>RACING COMMUNITY</span></span><span class="brand-sub">OFFICIAL SESSION RESULTS</span><span class="build-tag">LIVE · {BUILD_ID}</span></a>
  <div class="top-actions">
    <span class="update-time">更新于 {updated_at} · <span id="refresh-countdown">60 秒后自动刷新</span></span>
    <button class="refresh-button" type="button" id="refresh-now">立即刷新</button>
    <a class="back-link" href="/?year={year}">← 返回 {year} 分站列表</a>
  </div>
</header>
<section class="hero">
  <div>
    <div class="eyebrow">{event_round_label(round_number)} · MOTOGP · GRAND PRIX</div>
    <h1>{esc(event.get('name') or '未知分站')}</h1>
    <p class="hero-copy">本站 MotoGP 组别赛果直接读取官方接口。页面打开期间每 60 秒自动刷新；左侧切换场次，右侧查看领奖台和全部车手分类。</p>
    <div class="hero-meta">
      <span>{esc(circuit.get('name'))}</span>
      <span>{esc(format_event_dates(event))}</span>
      <span>{available_count}/{total_count} 个场次已发布</span>
    </div>
  </div>
  <aside class="hero-side">
    <div class="hero-round">{event_round_label(round_number)}<div class="hero-season">{year} / SEASON</div></div>
    <dl class="meta-list">
      <div class="meta-row"><dt>Location</dt><dd>{esc(circuit.get('place'))}<br>{esc(country.get('name'))}</dd></div>
      <div class="meta-row"><dt>Data</dt><dd>PulseLive · Official</dd></div>
      <div class="meta-row"><dt>Status</dt><dd><span class="status-pill{state_class}">{state_text}</span></dd></div>
    </dl>
  </aside>
</section>
{missing_notice}
{render_weather_panel(str(event.get('short_name') or ''))}
<section class="stats-grid">
  <article class="stat-card"><div class="stat-label">Data Progress</div><div class="stat-value">{available_count} / {total_count}</div><div class="stat-detail">{'全部场次已发布官方分类' if not missing else f'{len(missing)} 个场次等待官方同步'}</div><div class="progress"><span style="width:{progress_percent}%"></span></div></article>
  <article class="stat-card"><div class="stat-label">正赛冠军</div><div class="stat-value">{esc(rider_display_name(winner) if winner else '—')}</div><div class="stat-detail">{esc(team_display_name(winner) if winner else '官方分类未提供')}</div></article>
  <article class="stat-card"><div class="stat-label">比赛状态</div><div class="stat-value">{state_text}</div><div class="stat-detail">{available_count} 个场次可查看</div></article>
</section>
<section class="weekend-layout">
  <aside class="session-nav">
    <div class="panel-kicker">WEEKEND FLOW</div>
    <h2>场次切换</h2>
    <div class="session-buttons">{''.join(buttons)}</div>
  </aside>
  <main id="session-stage" class="session-stage">{''.join(panels)}</main>
</section>
<div class="winner-overlay" id="winner-overlay" aria-hidden="true">
  <article class="winner-card" id="winner-card">
    <div class="winner-copy">
      <div class="winner-kicker">SESSION WINNER</div>
      <div class="winner-session" id="winner-session">正赛</div>
      <h2 id="winner-name">—</h2>
      <p class="winner-team" id="winner-team">—</p>
      <div class="winner-stats">
        <div><span>成绩</span><strong id="winner-time">—</strong></div>
        <div><span>时间差</span><strong id="winner-gap">—</strong></div>
        <div><span>积分</span><strong id="winner-points">—</strong></div>
      </div>
    </div>
    <div class="winner-photo-wrap">
      <img class="winner-photo" id="winner-photo" src="" alt="">
    </div>
    <div class="winner-word">WINNER</div>
  </article>
  <div class="winner-dismiss">点击空白区域查看完整排名</div>
</div>"""

    script = r"""
const buttons = Array.from(document.querySelectorAll('[data-code].session-button:not([disabled])'));
const panels = Array.from(document.querySelectorAll('.session-panel'));
const titleMap = __TITLES__;
const winners = __WINNERS__;
const initial = new URLSearchParams(location.search).get('session');
const winnerOverlay = document.getElementById('winner-overlay');
const winnerCard = document.getElementById('winner-card');
const winnerPhoto = document.getElementById('winner-photo');

function showWinner(code) {
  const winner = winners[code];
  if (!winner || !winnerOverlay) return;
  winnerCard.style.setProperty('--winner-accent', winner.accent || '#1c1c1e');
  document.getElementById('winner-session').textContent = `${winner.code} · ${winner.title}`;
  document.getElementById('winner-name').textContent = winner.name;
  document.getElementById('winner-team').textContent = `${winner.team} · ${winner.constructor}`;
  document.getElementById('winner-time').textContent = winner.time || '—';
  document.getElementById('winner-gap').textContent = winner.gap || '—';
  document.getElementById('winner-points').textContent = winner.points || '—';
  winnerCard.classList.toggle('no-photo', !winner.photo);
  if (winner.photo) {
    winnerPhoto.classList.remove('loaded');
    winnerPhoto.alt = `${winner.name} official portrait`;
    winnerPhoto.addEventListener('load', () => winnerPhoto.classList.add('loaded'), { once: true });
    winnerPhoto.src = winner.photo;
  } else {
    winnerPhoto.removeAttribute('src');
    winnerPhoto.alt = '';
  }
  winnerOverlay.classList.add('visible');
  winnerOverlay.setAttribute('aria-hidden', 'false');
  document.body.classList.add('winner-open');
}

function hideWinner() {
  if (!winnerOverlay) return;
  winnerOverlay.classList.remove('visible');
  winnerOverlay.setAttribute('aria-hidden', 'true');
  document.body.classList.remove('winner-open');
}

function activate(code, updateUrl=true, revealWinner=false) {
  const available = panels.some(panel => panel.dataset.code === code);
  if (!available) code = panels[panels.length - 1]?.dataset.code;
  buttons.forEach(button => {
    const active = button.dataset.code === code;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  panels.forEach(panel => { panel.hidden = panel.dataset.code !== code; });
  if (code && titleMap[code]) document.title = titleMap[code] + ' · MotoGP ' + __YEAR__ + ' 第 ' + __ROUND__ + ' 站';
  if (updateUrl && code) {
    const url = new URL(location.href);
    url.searchParams.set('session', code);
    history.replaceState(null, '', url);
  }
  if (revealWinner && code) showWinner(code);
}

buttons.forEach(button => button.addEventListener('click', () => activate(button.dataset.code, true, true)));
activate(initial || __DEFAULT__, false);
if (winnerOverlay) {
  const activeCode = panels.find(panel => !panel.hidden)?.dataset.code;
  window.setTimeout(() => showWinner(activeCode), 80);
  winnerOverlay.addEventListener('click', event => {
    if (!event.target.closest('#winner-card')) hideWinner();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') hideWinner();
  });
  winnerPhoto.addEventListener('error', () => {
    winnerCard.classList.add('no-photo');
    winnerPhoto.removeAttribute('src');
  });
}

const refreshButton = document.getElementById('refresh-now');
const countdown = document.getElementById('refresh-countdown');
let remaining = 60;
function updateCountdown() {
  if (countdown) countdown.textContent = `${remaining} 秒后自动刷新`;
}
if (refreshButton) refreshButton.addEventListener('click', () => location.reload());
updateCountdown();
setInterval(() => {
  if (document.hidden) return;
  remaining -= 1;
  if (remaining <= 0) {
    location.reload();
    return;
  }
  updateCountdown();
}, 1000);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    remaining = 60;
    updateCountdown();
  }
});
"""
    script = (
        script.replace("__TITLES__", json.dumps(SESSION_TITLES, ensure_ascii=False))
        .replace("__WINNERS__", json.dumps(winner_data, ensure_ascii=False))
        .replace("__YEAR__", str(year))
        .replace("__ROUND__", str(round_number))
        .replace("__DEFAULT__", json.dumps(default_code))
    )
    return layout(
        f"MotoGP {year} 第 {round_number} 站 · {event.get('name') or '赛果'}",
        body,
        script,
    )


class RacePackHandler(BaseHTTPRequestHandler):
    server_version = "RacePackWeb/1.0"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def send_html(self, content: str, status: int = 200) -> None:
        payload = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def send_data(
        self,
        content: str,
        content_type: str,
        cache_control: str = "no-cache",
        head_only: bool = False,
    ) -> None:
        payload = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache_control)
        self.end_headers()
        if not head_only:
            self.wfile.write(payload)

    def send_asset(self, asset_name: str, head_only: bool = False) -> None:
        asset_path = (ASSETS_DIR / asset_name).resolve()
        if asset_path.parent != ASSETS_DIR.resolve() or not asset_path.is_file():
            self.send_html(error_page("资源不存在", "找不到请求的图片资源。"), status=404)
            return
        content_types = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".svg": "image/svg+xml",
            ".mp4": "video/mp4",
            ".mp3": "audio/mpeg",
            ".html": "text/html; charset=utf-8",
        }
        content_type = content_types.get(asset_path.suffix.lower(), "application/octet-stream")
        payload = asset_path.read_bytes()
        total_size = len(payload)
        start = 0
        end = total_size - 1
        status = 200
        range_header = self.headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            try:
                raw_start, raw_end = range_header[6:].split("-", 1)
                start = int(raw_start) if raw_start else 0
                end = int(raw_end) if raw_end else total_size - 1
                end = min(end, total_size - 1)
                if start < 0 or start > end:
                    raise ValueError("invalid byte range")
                status = 206
            except ValueError:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{total_size}")
                self.end_headers()
                return

        body = payload[start : end + 1]
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total_size}")
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path.startswith("/assets/"):
            self.send_asset(unquote(parsed.path[len("/assets/") :]))
            return

        if parsed.path == "/manifest.webmanifest":
            self.send_data(
                json.dumps(PWA_MANIFEST, ensure_ascii=False, indent=2),
                "application/manifest+json; charset=utf-8",
                cache_control="public, max-age=3600",
            )
            return

        if parsed.path == "/sw.js":
            self.send_data(
                SERVICE_WORKER_JS.replace("__BUILD__", BUILD_ID),
                "application/javascript; charset=utf-8",
                cache_control="no-cache, no-store, must-revalidate",
            )
            return

        if parsed.path == "/offline.html":
            self.send_asset("offline.html")
            return

        if parsed.path == "/health":
            payload = SERVER_ID.encode("ascii")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if parsed.path == "/favicon.ico":
            self.send_asset("racing-community-avatar-320.jpg")
            return

        if parsed.path not in ("/", "/index.html", "/race"):
            self.send_html(error_page("页面不存在", "找不到你请求的页面。"), status=404)
            return

        try:
            if parsed.path in ("/", "/index.html"):
                year = int((query.get("year") or [datetime.now().year])[0])
                _season_id, rounds = fetch_schedule(year)
                port = int(self.server.server_address[1])
                phone_url = f"http://{lan_ip_address()}:{port}/?build={BUILD_ID}"
                self.send_html(render_home(year, rounds, phone_url))
                return

            year = int((query.get("year") or [datetime.now().year])[0])
            round_values = query.get("round") or []
            if not round_values:
                raise backend.RacePackError("缺少分站轮次。")
            round_number = int(round_values[0])
            event, weekend, missing = backend.fetch_weekend_with_status(
                year,
                round_number,
                allow_incomplete=True,
            )
            if not weekend:
                raise backend.DataNotAvailableError(
                    f"{year} 第 {round_number} 站尚未发布任何场次分类。"
                )
            self.send_html(render_race(year, round_number, event, weekend, missing))
        except (ValueError, backend.RacePackError) as exc:
            self.send_html(error_page("暂时无法显示赛果", str(exc)), status=400)
        except backend.motogp.PulseLiveError as exc:
            self.send_html(
                error_page(
                    "无法连接官方数据",
                    f"拉取成绩失败。请检查网络后重试，或稍后再试数据尚未同步的分站。详细信息：{exc}",
                ),
                status=502,
            )
        except Exception as exc:
            self.send_html(
                error_page("打开赛果时出错", f"发生意外错误：{exc}"),
                status=500,
            )

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/manifest.webmanifest":
            self.send_data(
                json.dumps(PWA_MANIFEST, ensure_ascii=False),
                "application/manifest+json; charset=utf-8",
                head_only=True,
            )
            return
        if parsed.path == "/sw.js":
            self.send_data(
                SERVICE_WORKER_JS.replace("__BUILD__", BUILD_ID),
                "application/javascript; charset=utf-8",
                head_only=True,
            )
            return
        if parsed.path.startswith("/assets/"):
            self.send_asset(unquote(parsed.path[len("/assets/") :]), head_only=True)
            return
        if parsed.path == "/favicon.ico":
            self.send_asset("racing-community-avatar-320.jpg", head_only=True)
            return
        self.send_response(404)
        self.end_headers()


def own_server_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://{LOOPBACK_HOST}:{port}/health",
            timeout=0.7,
        ) as response:
            return response.read().decode("ascii") == SERVER_ID
    except Exception:
        return False


def start_server(port: int, open_browser: bool) -> int:
    selected_port = None
    for candidate in range(port, port + 12):
        if own_server_running(candidate):
            if open_browser:
                webbrowser.open(
                    f"http://{LOOPBACK_HOST}:{candidate}/?build={BUILD_ID}&intro=1"
                )
            return 0
        try:
            server = ThreadingHTTPServer((BIND_HOST, candidate), RacePackHandler)
            selected_port = candidate
            break
        except OSError:
            continue

    if selected_port is None:
        return 1

    if open_browser:
        threading.Timer(
            0.45,
            lambda: webbrowser.open(
                f"http://{LOOPBACK_HOST}:{selected_port}/?build={BUILD_ID}&intro=1"
            ),
        ).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="在浏览器中查看 MotoGP 完整赛果。")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", str(DEFAULT_PORT))),
    )
    parser.add_argument("--no-browser", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return start_server(args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    raise SystemExit(main())
