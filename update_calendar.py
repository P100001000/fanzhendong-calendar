#!/usr/bin/env python3
"""Fetch Borussia Düsseldorf's public schedule and rebuild fanzhendong.ics."""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


SOURCE_URL = "https://www.borussia-duesseldorf.com/profis/spielplan"
TEAM = "Borussia Düsseldorf"
BERLIN = ZoneInfo("Europe/Berlin")
BEIJING = ZoneInfo("Asia/Shanghai")
OUT = Path(__file__).with_name("fanzhendong.ics")

# Used only if the public page is temporarily unavailable. Times are German local time.
FALLBACK = [
    ("德国杯", "1/8 决赛", "2026-08-29", "11:00", "TTF Liebherr Ochsenhausen", TEAM),
    ("德国乒乓球甲级联赛（TTBL）", "第 4 轮", "2026-09-05", "13:00", TEAM, "TTC Schwalbe Bergneustadt"),
    ("德国乒乓球甲级联赛（TTBL）", "第 5 轮", "2026-09-22", "19:00", "Post SV Mühlhausen", TEAM),
    ("德国乒乓球甲级联赛（TTBL）", "第 6 轮", "2026-09-27", "14:00", TEAM, "BV Borussia Dortmund"),
    ("德国乒乓球甲级联赛（TTBL）", "第 7 轮", "2026-11-09", "19:00", "TTC Zugbrücke Grenzau", TEAM),
    ("德国乒乓球甲级联赛（TTBL）", "第 8 轮", "2026-11-15", "14:00", TEAM, "ASC Grünwettersbach"),
    ("德国乒乓球甲级联赛（TTBL）", "第 9 轮", "2026-11-22", "15:30", "1.FC Saarbrücken-TT", TEAM),
    ("德国乒乓球甲级联赛（TTBL）", "第 10 轮", "2026-12-13", "17:30", "TTC RhönSprudel Fulda-Maberzell", TEAM),
    ("德国乒乓球甲级联赛（TTBL）", "第 11 轮", "2026-12-20", "14:00", TEAM, "TTF Liebherr Ochsenhausen"),
]

VENUES = {
    "2026-08-29": "Kia Metropol Arena Nürnberg",
    "2026-09-05": "ARAG CenterCourt, Düsseldorf",
    "2026-09-27": "CASTELLO Düsseldorf",
    "2026-11-15": "ARAG CenterCourt, Düsseldorf",
    "2026-12-20": "Georg-Gaßmann-Sporthalle, Marburg",
}


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def parse_schedule(page: str) -> list[tuple[str, str, str, str, str, str]]:
    soup = BeautifulSoup(page, "html.parser")
    events = []
    for table in soup.find_all("table"):
        heading = table.find_previous("h2")
        if not heading:
            continue
        title = clean(heading.get_text(" "))
        if "Bundesliga" in title:
            competition = "德国乒乓球甲级联赛（TTBL）"
        elif "Pokal" in title:
            competition = "德国杯"
        else:
            continue
        for row in table.find_all("tr"):
            cells = [clean(c.get_text(" ")) for c in row.find_all(["th", "td"])]
            if not any(re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", c) for c in cells):
                continue
            date_de = next(c for c in cells if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", c))
            time_de = next((c for c in cells if re.fullmatch(r"\d{1,2}:\d{2}", c)), None)
            if not time_de:
                continue
            date_iso = datetime.strptime(date_de, "%d.%m.%Y").date().isoformat()
            # The last two team-like fields are consistently home and away on the official table.
            date_i = cells.index(date_de)
            time_i = cells.index(time_de)
            after = [c for c in cells[time_i + 1 :] if c and not re.fullmatch(r"\d+\s*:\s*\d+", c)]
            if len(after) < 2:
                continue
            home, away = after[0], after[1]
            round_raw = cells[0]
            round_name = f"第 {round_raw} 轮" if round_raw.isdigit() else round_raw
            events.append((competition, round_name, date_iso, time_de, home, away))
    if not events:
        raise ValueError("No schedule rows found")
    return events


def esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line: str, limit: int = 73) -> list[str]:
    """Fold without splitting UTF-8 byte sequences (RFC 5545 recommends <=75 octets)."""
    parts, current = [], ""
    for char in line:
        prefix = " " if parts else ""
        if len((prefix + current + char).encode("utf-8")) > limit:
            parts.append((" " if parts else "") + current)
            current = char
        else:
            current += char
    parts.append((" " if parts else "") + current)
    return parts


def build(events: list[tuple[str, str, str, str, str, str]]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Fan Zhendong Calendar//CN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:🏓 樊振东比赛日历",
        "X-WR-TIMEZONE:Asia/Shanghai", "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    ordered = sorted(events, key=lambda event: (event[2], event[3], event[0]))
    for competition, round_name, date_iso, time_de, home, away in ordered:
        local = datetime.fromisoformat(f"{date_iso}T{time_de}:00").replace(tzinfo=BERLIN)
        bj = local.astimezone(BEIJING)
        if bj + timedelta(hours=4) < datetime.now(BEIJING):
            continue
        opponent = away if TEAM.lower() in home.lower() else home
        start = bj.strftime("%Y%m%dT%H%M%S")
        end = (bj + timedelta(hours=2, minutes=30)).strftime("%Y%m%dT%H%M%S")
        uid_seed = f"{competition}|{round_name}|{date_iso}|{home}|{away}"
        uid = hashlib.sha256(uid_seed.encode()).hexdigest()[:20] + "@fanzhendong-calendar"
        livestream = "Dyn（付费，全部 TTBL/德国杯比赛）"
        if date_iso == "2026-08-29":
            livestream += "；优酷/优酷体育（国内已预告）"
        description = (
            f"赛事：{competition}\\n轮次：{round_name}\\n对手：{opponent}\\n"
            f"开赛：北京时间 {bj:%Y-%m-%d %H:%M}\\n直播：{livestream}\\n"
            "说明：这是杜塞尔多夫俱乐部赛程；樊振东是否实际出场以临场名单为准。\\n"
            f"赛程来源：{SOURCE_URL}"
        )
        event_lines = [
            "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{now}",
            f"DTSTART;TZID=Asia/Shanghai:{start}", f"DTEND;TZID=Asia/Shanghai:{end}",
            f"SUMMARY:{esc('🏓 樊振东｜' + competition + '｜vs ' + opponent)}",
            f"DESCRIPTION:{esc(description)}", f"LOCATION:{esc(VENUES.get(date_iso, '德国（客场地点以官方为准）'))}",
            f"URL:{SOURCE_URL}", "STATUS:CONFIRMED", "TRANSP:OPAQUE",
            "BEGIN:VALARM", "TRIGGER:-PT30M", "ACTION:DISPLAY", "DESCRIPTION:樊振东比赛将在 30 分钟后开始", "END:VALARM",
            "END:VEVENT",
        ]
        lines.extend(event_lines)
    lines.append("END:VCALENDAR")
    folded = [part for line in lines for part in fold(line)]
    return "\r\n".join(folded) + "\r\n"


def main() -> None:
    try:
        response = requests.get(SOURCE_URL, timeout=30, headers={"User-Agent": "fanzhendong-calendar/1.0"})
        response.raise_for_status()
        events = parse_schedule(response.text)
    except Exception as exc:
        print(f"Warning: public schedule unavailable ({exc}); using bundled snapshot")
        events = FALLBACK
    with OUT.open("w", encoding="utf-8", newline="") as file:
        file.write(build(events))
    print(f"Wrote {OUT} with {len(events)} source rows")


if __name__ == "__main__":
    main()
