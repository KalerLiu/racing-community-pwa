#!/usr/bin/env python3
"""Build a local MotoGP race results pack from pymotogp data."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import motogp
except ImportError as exc:
    motogp = None
    MOTOGP_IMPORT_ERROR: ImportError | None = exc
else:
    MOTOGP_IMPORT_ERROR = None


SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = SCRIPT_DIR / "race_pack.md"

SESSION_NAMES = {
    "FP1": "FP1 · 自由练习 1",
    "FP2": "FP2 · 自由练习 2",
    "FP3": "FP3 · 自由练习 3",
    "FP4": "FP4 · 自由练习 4",
    "PR": "PR · 练习赛",
    "Q1": "Q1 · 排位赛 1",
    "Q2": "Q2 · 排位赛 2",
    "SPR": "SPR · 冲刺赛",
    "RAC": "RAC · 正赛",
    "WUP": "WUP · 热身",
}

STATUS_NAMES = {
    "INSTND": "完赛",
    "DNF": "退赛",
    "DNS": "未起步",
    "DNQ": "未获资格",
    "DSQ": "取消资格",
    "EXC": "排除",
    "EXCLUDED": "排除",
    "NOTFINISHFIRST": "未完赛",
    "NOT FINISHED": "未完赛",
    "RETIRED": "退赛",
    "WDR": "退出",
    "WITHDRAWN": "退出",
}


class RacePackError(RuntimeError):
    """An expected, user-facing race pack error."""


class DataNotAvailableError(RacePackError):
    """The requested race weekend does not have a complete result set."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="获取一个 MotoGP 分站的全部场次成绩。"
    )
    parser.add_argument(
        "--year",
        type=int,
        help="赛季年份，例如 2026。未提供时会在终端提示输入。",
    )
    parser.add_argument(
        "--round",
        dest="round_number",
        metavar="ROUND",
        type=int,
        help="赛季第几个分站，例如 16。未提供时会在终端提示输入。",
    )
    return parser.parse_args(argv)


def resolve_year_and_round(args: argparse.Namespace) -> tuple[int, int]:
    year = args.year
    round_number = args.round_number

    if year is None:
        raw_year = input(f"赛季年份 [{datetime.now().year}]: ").strip()
        year = int(raw_year) if raw_year else datetime.now().year

    if round_number is None:
        raw_round = input("分站轮次（例如 16）: ").strip()
        if not raw_round:
            raise ValueError("分站轮次不能为空。")
        round_number = int(raw_round)

    if not 1900 <= year <= 2200:
        raise ValueError(f"年份不合理：{year}")
    if round_number < 1:
        raise ValueError(f"分站轮次必须大于 0：{round_number}")
    return year, round_number


def session_code(session: dict[str, Any]) -> str:
    session_type = str(session.get("type") or "").strip().upper()
    number = session.get("number")
    return f"{session_type}{number or ''}"


def fetch_weekend_with_status(
    year: int,
    round_number: int,
    allow_incomplete: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch one round and report sessions whose classifications are unavailable."""
    if motogp is None:
        raise RacePackError(
            "缺少 pymotogp。请先运行：python -m pip install pymotogp pandas"
        )

    # Disable pymotogp's on-disk HTTP cache so a new run after a race does not
    # reuse a response captured earlier during the same race weekend.
    client = motogp.PulseLiveClient(cache_dir=None, timeout=20, retries=3)
    season = client.get_season(year)
    events = [
        event
        for event in client.get_events(season["id"], finished_only=False)
        if not event.get("test")
    ]
    events.sort(
        key=lambda event: (
            event.get("date_start") or event.get("date_end") or "",
            event.get("name") or "",
        )
    )

    if round_number > len(events):
        raise RacePackError(
            f"{year} 赛季没有第 {round_number} 站；当前赛历共有 {len(events)} 站。"
        )

    event = events[round_number - 1]
    category = client.get_category(season["id"], "motogp")
    sessions = client.get_sessions(event["id"], category["id"])
    if not sessions:
        raise DataNotAvailableError(
            f"{year} 第 {round_number} 站尚未返回任何 MotoGP 场次数据。"
        )

    weekend: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for session in sessions:
        code = session_code(session)
        payload = client.get_classification(session["id"])
        rows = payload.get("classification") or []
        if not rows:
            missing.append(
                {
                    "code": code or "未知场次",
                    "status": session.get("status"),
                    "date": session.get("date"),
                }
            )
            continue
        weekend.append({"session": session, "classification": rows})

    if missing and not allow_incomplete:
        raise DataNotAvailableError(
            f"{year} 第 {round_number} 站以下场次尚无成绩数据："
            + "、".join(item["code"] for item in missing)
        )

    return event, weekend, missing


def fetch_weekend(year: int, round_number: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fetch a complete MotoGP weekend, failing if any session is unavailable."""
    event, weekend, _missing = fetch_weekend_with_status(year, round_number)
    return event, weekend


def markdown_cell(value: Any, empty: str = "—") -> str:
    if value is None:
        return empty
    text = str(value).strip()
    if not text:
        return empty
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def rider_name(row: dict[str, Any]) -> str:
    rider = row.get("rider") or {}
    return markdown_cell(rider.get("full_name") or rider.get("name"))


def team_name(row: dict[str, Any]) -> str:
    team = row.get("team") or {}
    return markdown_cell(team.get("name"))


def result_time(row: dict[str, Any]) -> str:
    if row.get("time"):
        return markdown_cell(row.get("time"))
    best_lap = row.get("best_lap") or {}
    return markdown_cell(best_lap.get("time"))


def gap_to_leader(row: dict[str, Any]) -> str:
    position = row.get("position")
    if position is None or position == 0 or position == "":
        return "—"
    gap = row.get("gap") or {}
    return markdown_cell(gap.get("first"))


def points(row: dict[str, Any]) -> str:
    value = row.get("points")
    if value is None or value == "":
        return "—"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return markdown_cell(value)


def status_name(row: dict[str, Any]) -> str:
    code = markdown_cell(row.get("status"))
    if code == "—":
        return "—"
    return STATUS_NAMES.get(code.upper(), code)


def render_session(
    session: dict[str, Any],
    classification: list[dict[str, Any]],
) -> list[str]:
    code = session_code(session)
    heading = SESSION_NAMES.get(code, code or "未知场次")
    has_total_time = any(row.get("time") for row in classification)
    time_heading = "总用时" if has_total_time else "最佳圈速"

    lines = [
        f"### {heading}",
        "",
        f"| 名次 | 车手 | 车队 | {time_heading} | 时间差 | 积分 | 状态 |",
        "| ---: | --- | --- | ---: | ---: | ---: | --- |",
    ]

    for row in classification:
        position = markdown_cell(row.get("position"), empty="—")
        prefix = position if position == "—" else f"P{position}"
        lines.append(
            "| "
            + " | ".join(
                [
                    prefix,
                    rider_name(row),
                    team_name(row),
                    result_time(row),
                    gap_to_leader(row),
                    points(row),
                    status_name(row),
                ]
            )
            + " |"
        )

    lines.append("")
    return lines


def build_markdown(
    year: int,
    round_number: int,
    event: dict[str, Any],
    weekend: list[dict[str, Any]],
) -> str:
    circuit = event.get("circuit") or {}
    country = event.get("country") or {}
    generated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    event_name = event.get("name") or event.get("short_name") or "未知分站"

    lines = [
        f"# {year} 第 {round_number} 站 · {event_name}",
        "",
        f"- 组别：MotoGP",
        f"- 赛道：{markdown_cell(circuit.get('name'))}",
        f"- 地点：{markdown_cell(circuit.get('place'))}",
        f"- 国家/地区：{markdown_cell(country.get('name'))}",
        f"- 周末开始：{markdown_cell(event.get('date_start'))}",
        f"- 生成时间：{generated_at}",
        "- 数据来源：MotoGP PulseLive（经 pymotogp）",
        "",
        "## 结构化成绩",
        "",
    ]

    for item in weekend:
        lines.extend(render_session(item["session"], item["classification"]))

    return "\n".join(lines)


def write_markdown(content: str) -> None:
    temp_path = OUTPUT_PATH.with_name(f".{OUTPUT_PATH.name}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        temp_path.replace(OUTPUT_PATH)
    except OSError as exc:
        raise RacePackError(f"无法写入 race_pack.md：{exc}") from exc
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        year, round_number = resolve_year_and_round(args)
    except (ValueError, EOFError) as exc:
        print(f"参数错误：{exc}", file=sys.stderr)
        return 2

    if motogp is None:
        print(
            "错误：缺少 pymotogp。请先运行：python -m pip install pymotogp pandas",
            file=sys.stderr,
        )
        if MOTOGP_IMPORT_ERROR:
            print(f"详细信息：{MOTOGP_IMPORT_ERROR}", file=sys.stderr)
        return 2

    print(f"正在获取 {year} 年第 {round_number} 站的 MotoGP 成绩……")
    try:
        event, weekend = fetch_weekend(year, round_number)
        content = build_markdown(year, round_number, event, weekend)
        write_markdown(content)
    except motogp.PulseLiveError as exc:
        print("错误：无法从 MotoGP 官方数据接口获取成绩。", file=sys.stderr)
        print(
            "可能原因：网络连接异常，或该站数据尚未同步。请检查网络后重试，并稍后再试未同步的数据。",
            file=sys.stderr,
        )
        print(f"详细信息：{exc}", file=sys.stderr)
        return 1
    except DataNotAvailableError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        print(
            "可能原因：该站数据尚未同步。本次未写入 race_pack.md，原有文件未改动。",
            file=sys.stderr,
        )
        return 1
    except RacePackError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"错误：生成 race_pack.md 时发生意外错误：{exc}", file=sys.stderr)
        print("本次未写入 race_pack.md，原有文件未改动。", file=sys.stderr)
        return 1

    session_count = len(weekend)
    rider_rows = sum(len(item["classification"]) for item in weekend)
    print(
        f"完成：{session_count} 个场次、{rider_rows} 条车手记录。"
        f"文件：{OUTPUT_PATH}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
