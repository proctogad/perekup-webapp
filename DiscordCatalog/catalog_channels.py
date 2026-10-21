"""Каталогизатор каналов Discord.

Выгружает все категории и каналы сервера через Bot API и сохраняет каталог
в JSON (машинный вид) и Markdown (человеческий вид).

Запуск:
    python catalog_channels.py --guild-name "Перекуп Калькулятор"
    python catalog_channels.py --guild-id 123456789012345678
    python catalog_channels.py --guild-name "Мой сервер" --out-dir out --stats

Токен бота: переменная окружения DISCORD_BOT_TOKEN или --token.
У бота должен быть включён Server Members/Channel List Intent не нужен,
но сервер должен быть доступен боту и бот должен видеть каналы
(право View Channel / Manage Channels у роли бота).

Общий клиент (токен, запросы, поиск сервера) — в discord_api.py.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from discord_api import DiscordError, api, enable_utf8_console, load_env, resolve_guild

# Discord ChannelType -> человекочитаемое название
TYPE_NAMES: dict[int, str] = {
    0: "текстовый",
    1: "личные сообщения",
    2: "голосовой",
    3: "группа ЛС",
    4: "категория",
    5: "канал объявлений",
    10: "тред объявления",
    11: "публичный тред",
    12: "приватный тред",
    13: "stage-канал",
    14: "каталог серверов",
    15: "форум",
    16: "медиа-канал",
}

CATEGORY_TYPES = {4}
THREAD_TYPES = {10, 11, 12}
VOICE_TYPES = {2, 13}


def build_catalog(raw_channels: list[dict], include_threads: bool = False) -> dict:
    """Разложить плоский ответ API по категориям, сохранив порядок позиций."""
    categories = {
        c["id"]: c
        for c in raw_channels
        if c["type"] in CATEGORY_TYPES
    }
    orphans: list[dict] = []
    grouped: dict[str, list[dict]] = {cid: [] for cid in categories}

    for ch in raw_channels:
        if ch["type"] in CATEGORY_TYPES:
            continue
        if ch["type"] in THREAD_TYPES and not include_threads:
            continue
        parent = ch.get("parent_id")
        (grouped[parent] if parent in grouped else orphans).append(ch)

    ordered = sorted(categories.values(), key=lambda c: c.get("position", 0))
    orphans.sort(key=lambda c: c.get("position", 0))

    def pack(ch: dict) -> dict:
        entry = {
            "id": ch["id"],
            "name": ch["name"],
            "type": ch["type"],
            "type_name": TYPE_NAMES.get(ch["type"], f"тип-{ch['type']}"),
            "position": ch.get("position", 0),
            "parent_id": ch.get("parent_id"),
            "nsfw": ch.get("nsfw", False),
        }
        if ch["type"] in VOICE_TYPES:
            entry["bitrate"] = ch.get("bitrate")
            entry["user_limit"] = ch.get("user_limit")
        if ch.get("rate_limit_per_user") is not None:
            entry["slowmode"] = ch["rate_limit_per_user"]
        if ch.get("topic"):
            entry["topic"] = ch["topic"]
        if ch.get("message_count") is not None:
            entry["message_count"] = ch["message_count"]
        if ch.get("member_count") is not None:
            entry["member_count"] = ch["member_count"]
        return entry

    return {
        "categories": [
            {
                "id": cat["id"],
                "name": cat["name"],
                "type": cat["type"],
                "type_name": TYPE_NAMES.get(cat["type"], "категория"),
                "position": cat.get("position", 0),
                "nsfw": cat.get("nsfw", False),
                "channels": [pack(c) for c in sorted(grouped[cat["id"]], key=lambda c: c.get("position", 0))],
            }
            for cat in ordered
        ],
        "uncategorized": [pack(c) for c in orphans],
    }


def render_markdown(catalog: dict, guild: dict, generated_at: str) -> str:
    lines = [
        f"# Каталог каналов — {guild['name']}",
        "",
        f"- Сервер ID: `{guild['id']}`",
        f"- Категорий: **{len(catalog['categories'])}**",
        f"- Каналов всего: **{sum(len(c['channels']) for c in catalog['categories']) + len(catalog['uncategorized'])}**",
        f"- Обновлено: {generated_at}",
        "",
    ]

    def table(channels: list[dict]) -> list[str]:
        if not channels:
            return ["_пусто_", ""]
        rows = ["| Канал | Тип | ID | Позиция |", "| --- | --- | --- | ---: |"]
        rows += [
            f"| `{c['name']}` | {c['type_name']} | `{c['id']}` | {c['position']} |" for c in channels
        ]
        rows.append("")
        return rows

    for cat in catalog["categories"]:
        lines.append(f"## {cat['name']}")
        lines.append("")
        lines.append(f"`{cat['id']}` · тип: {cat['type_name']} · каналов: {len(cat['channels'])}")
        lines.append("")
        lines += table(cat["channels"])

    if catalog["uncategorized"]:
        lines.append("## Без категории")
        lines.append("")
        lines += table(catalog["uncategorized"])

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    enable_utf8_console()
    load_env()

    ap = argparse.ArgumentParser(description="Каталог каналов Discord сервера")
    ap.add_argument("--guild-id", help="ID сервера")
    ap.add_argument("--guild-name", help="имя сервера (поиск по подстроке)")
    ap.add_argument("--token", default=os.environ.get("DISCORD_BOT_TOKEN", ""), help="токен бота")
    ap.add_argument("--out-dir", default=".", help="куда сохранять файлы")
    ap.add_argument("--prefix", default="channels", help="префикс имён файлов")
    ap.add_argument("--threads", action="store_true", help="включить треды")
    ap.add_argument("--counts", action="store_true", help="запрашивать счётчики сообщений/участников")
    ap.add_argument("--stats", action="store_true", help="печатать статистику в консоль")
    args = ap.parse_args()

    if not args.token:
        ap.error("нет токена: задайте DISCORD_BOT_TOKEN или --token")
    if not args.guild_id and not args.guild_name:
        ap.error("нужен --guild-id или --guild-name")

    try:
        print("ищу сервер…", file=sys.stderr)
        guild = resolve_guild(args.token, args.guild_id, args.guild_name)
        print(f"сервер: {guild['name']} ({guild['id']})", file=sys.stderr)

        params = "?with_counts=true" if args.counts else ""
        raw = api("GET", f"/guilds/{guild['id']}/channels", args.token, params)
    except DiscordError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        print(
            "Подсказка: бот должен быть на сервере, токен — с интентом и правами "
            "View Channel / Manage Channels.",
            file=sys.stderr,
        )
        return 1

    catalog = build_catalog(raw, include_threads=args.threads)
    generated_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    catalog["guild"] = {"id": guild["id"], "name": guild["name"], "icon": guild.get("icon")}
    catalog["generated_at"] = generated_at

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"{args.prefix}.json"
    md_path = out / f"{args.prefix}.md"
    json_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    md_path.write_text(render_markdown(catalog, guild, generated_at), encoding="utf-8")

    total = sum(len(c["channels"]) for c in catalog["categories"]) + len(catalog["uncategorized"])
    print(f"готово: {len(catalog['categories'])} категорий, {total} каналов")
    print(f"  {json_path}")
    print(f"  {md_path}")

    if args.stats:
        by_type: dict[str, int] = {}
        all_channels = catalog["uncategorized"] + [
            c for cat in catalog["categories"] for c in cat["channels"]
        ]
        for ch in all_channels:
            by_type[ch["type_name"]] = by_type.get(ch["type_name"], 0) + 1
        print("\nпо типам:")
        for name, cnt in sorted(by_type.items(), key=lambda kv: -kv[1]):
            print(f"  {name}: {cnt}")
        print("\nпо категориям:")
        for cat in catalog["categories"]:
            voice = sum(1 for c in cat["channels"] if c["type"] in VOICE_TYPES)
            print(f"  {cat['name']}: {len(cat['channels'])} (голосовых: {voice})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())