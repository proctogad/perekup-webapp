"""Четыре бессрочные инвайт-ссылки и файл invites.json для Discord-бота.

Ссылки:
- Telegram → роль `📱 Из Telegram`;
- EXE      → роль `🖥️ Из EXE`;
- Сайт     → роль `🌐 С сайта`;
- обычная  → без роли (запасная).

Скрипт можно запускать повторно: он проверяет коды из invites.json и создаёт
только недостающие ссылки. Без --apply — только предпросмотр.

Запуск:
    python create_invites.py --guild-name "Перекуп Калькулятор"            # предпросмотр
    python create_invites.py --guild-name "Перекуп Калькулятор" --apply    # создать

Файл по умолчанию пишется в `PerekupDisBot/invites.json` (рядом с ботом).
Токен: DISCORD_BOT_TOKEN или `.env`. Боту нужно право «Управление сервером»
(Manage Guild), чтобы читать и создавать инвайты.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from discord_api import DiscordError, api, enable_utf8_console, error_hint, load_env, resolve_guild

BASE_DIR = Path(__file__).resolve().parent
BOT_DIR = BASE_DIR.parent / "PerekupDisBot"
DEFAULT_OUT = (BOT_DIR / "invites.json") if BOT_DIR.is_dir() else (BASE_DIR / "invites.json")

SOURCES = [
    {"key": "telegram", "label": "Telegram", "role": "📱 Из Telegram"},
    {"key": "exe", "label": "EXE", "role": "🖥️ Из EXE"},
    {"key": "site", "label": "Сайт", "role": "🌐 С сайта"},
    {"key": "default", "label": "Обычная ссылка", "role": None},
]


def norm(text: str) -> str:
    return " ".join(str(text).casefold().replace("#", " ").split())


def find_invite_channel(channels: list[dict], query: str) -> dict | None:
    """Найти текстовый канал по имени или куску имени (welcom, обновления…)."""
    wanted = norm(query)
    text_channels = [c for c in channels if c["type"] == 0]
    exact = next((c for c in text_channels if norm(c["name"]) == wanted), None)
    if exact is not None:
        return exact
    if str(query).isdigit():
        return next((c for c in text_channels if c["id"] == str(query)), None)
    return next((c for c in text_channels if wanted in norm(c["name"])), None)


def load_existing(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_invites(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    enable_utf8_console()
    load_env()

    ap = argparse.ArgumentParser(description="Создать инвайт-ссылки Telegram / EXE / сайт")
    ap.add_argument("--guild-id", help="ID сервера")
    ap.add_argument("--guild-name", help="имя сервера (поиск по подстроке)")
    ap.add_argument("--token", default=os.environ.get("DISCORD_BOT_TOKEN", ""), help="токен бота")
    ap.add_argument("--channel", default="welcom", help="канал для инвайтов (по умолчанию welcom)")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="куда сохранить invites.json")
    ap.add_argument("--apply", action="store_true", help="создать недостающие ссылки")
    args = ap.parse_args()

    if not args.token:
        ap.error("нет токена: задайте DISCORD_BOT_TOKEN в .env или --token")
    if not args.guild_id and not args.guild_name:
        ap.error("нужен --guild-id или --guild-name")

    out_path = Path(args.out)
    old = load_existing(out_path)
    old_by_key = {
        str(item.get("key")): item
        for item in old.get("invites", [])
        if isinstance(item, dict) and item.get("key")
    }

    try:
        print("ищу сервер…", file=sys.stderr)
        guild = resolve_guild(args.token, args.guild_id, args.guild_name)
        print(f"сервер: {guild['name']} ({guild['id']})", file=sys.stderr)
        channels = api("GET", f"/guilds/{guild['id']}/channels", args.token)
    except DiscordError as exc:
        print(f"ОШИБКА: {exc}{error_hint(exc)}", file=sys.stderr)
        return 1

    channel = find_invite_channel(channels, args.channel)
    if channel is None:
        print(
            f"ОШИБКА: текстовый канал «{args.channel}» не найден. "
            "Укажи точное имя или ID через --channel.",
            file=sys.stderr,
        )
        return 1
    print(f"канал для ссылок: #{channel['name']} ({channel['id']})\n")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result: list[dict] = []
    created = reused = planned = errors = 0

    for source in SOURCES:
        key = source["key"]
        previous = old_by_key.get(key) or {}
        code = previous.get("code")
        invite = None

        if code:
            try:
                invite = api("GET", f"/invites/{code}", args.token, "?with_counts=true")
            except DiscordError as exc:
                text = str(exc)
                if "HTTP 404" in text or "HTTP 410" in text:
                    print(f"  ! {source['label']}: старая ссылка больше не работает — создам новую")
                else:
                    errors += 1
                    print(f"  ✗ {source['label']}: не удалось проверить код: {exc}{error_hint(exc)}", file=sys.stderr)
                    continue
            if invite is not None and str(invite.get("channel", {}).get("id")) != str(channel["id"]):
                print(f"  ! {source['label']}: ссылка ведёт в другой канал — создам новую")
                invite = None

        if invite is None:
            if not args.apply:
                planned += 1
                print(f"  → {source['label']}: создать бессрочную ссылку → {source['role'] or 'без роли'}")
                result.append(
                    {
                        "key": key,
                        "label": source["label"],
                        "code": None,
                        "url": None,
                        "role": source["role"],
                        "uses": 0,
                    }
                )
                continue
            try:
                invite = api(
                    "POST",
                    f"/channels/{channel['id']}/invites",
                    args.token,
                    payload={"max_age": 0, "max_uses": 0, "unique": True},
                )
            except DiscordError as exc:
                errors += 1
                print(f"  ✗ {source['label']}: {exc}{error_hint(exc)}", file=sys.stderr)
                continue
            created += 1
            print(f"  ✓ {source['label']}: создана")
        else:
            reused += 1
            print(f"  = {source['label']}: ссылка уже есть (использований: {invite.get('uses', 0)})")

        result.append(
            {
                "key": key,
                "label": source["label"],
                "code": invite["code"],
                "url": f"https://discord.gg/{invite['code']}",
                "role": source["role"],
                "uses": int(invite.get("uses", 0)),
                "channel_id": str(channel["id"]),
            }
        )

    print()
    if not args.apply:
        print(f"предпросмотр: к созданию {planned} · уже готово {reused}")
        if planned:
            print("применить: добавьте --apply")
        return 1 if errors else 0

    data = {
        "guild_id": str(guild["id"]),
        "channel_id": str(channel["id"]),
        "created_at": old.get("created_at") or now,
        "updated_at": now,
        "invites": result,
    }
    write_invites(out_path, data)
    print(f"итог: создано {created} · уже было {reused}" + (f" · ошибок {errors}" if errors else ""))
    print(f"файл: {out_path}")
    print("\nссылки:")
    for item in result:
        print(f"  {item['label']:<18} {item['url'] or '(не создана)'}  →  {item['role'] or 'без роли'}")
    print(
        "\nКуда вставить: Telegram — кнопка в боте/Mini App; EXE — ссылка в приложении; "
        "сайт — кнопка на странице. Обычная ссылка — запасная."
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
