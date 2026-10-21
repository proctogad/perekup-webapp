# Настройка временных голосовых каналов (join-to-create).
# Что делает:
# - создаёт голосовой канал-хаб «➕・создать-войс» в категории «Голосовые каналы»;
# - создаёт категорию «🔊 Временные комнаты», в которой бот создаёт комнаты на лету;
# - прописывает их в PerekupDisBot/config.json.
# - без --apply — только предпросмотр.

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from discord_api import DiscordError, api, enable_utf8_console, error_hint, load_env, resolve_guild

BASE_DIR = Path(__file__).resolve().parent.parent / "PerekupDisBot"
CONFIG_FILE = BASE_DIR / "config.json"

VOICE_HUB_NAME = "➕・создать-войс"
VOICE_TEMP_CATEGORY_NAME = "🔊 Временные комнаты"
VOICE_CATEGORY_NAME = "Голосовые каналы"


def norm(text: str) -> str:
    return " ".join(str(text).casefold().replace("#", " ").split())


def find_channel(channels: list[dict], name: str) -> dict | None:
    wanted = norm(name)
    return next((c for c in channels if norm(c["name"]) == wanted), None)


def find_category(channels: list[dict], name: str) -> dict | None:
    wanted = norm(name)
    return next((c for c in channels if c["type"] == 4 and norm(c["name"]) == wanted), None)


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    enable_utf8_console()
    load_env()

    ap = argparse.ArgumentParser(description="Настройка временных голосовых каналов")
    ap.add_argument("--guild-id", help="ID сервера")
    ap.add_argument("--guild-name", help="имя сервера (поиск по подстроке)")
    ap.add_argument("--token", default=os.environ.get("DISCORD_BOT_TOKEN", ""), help="токен бота")
    ap.add_argument("--apply", action="store_true", help="применить изменения")
    args = ap.parse_args()

    if not args.token:
        ap.error("нет токена: задайте DISCORD_BOT_TOKEN в .env или --token")
    if not args.guild_id and not args.guild_name:
        ap.error("нужен --guild-id или --guild-name")

    try:
        print("ищу сервер…", file=sys.stderr)
        guild = resolve_guild(args.token, args.guild_id, args.guild_name)
        print(f"сервер: {guild['name']} ({guild['id']})", file=sys.stderr)
        guild = api("GET", f"/guilds/{guild['id']}", args.token)
        channels = api("GET", f"/guilds/{guild['id']}/channels", args.token)
    except DiscordError as exc:
        print(f"ОШИБКА: {exc}{error_hint(exc)}", file=sys.stderr)
        return 1

    gid = guild["id"]
    voice_category = find_category(channels, VOICE_CATEGORY_NAME)
    if voice_category is None:
        print(f"ОШИБКА: нет категории «{VOICE_CATEGORY_NAME}».", file=sys.stderr)
        return 1

    hub = find_channel(channels, VOICE_HUB_NAME)
    if hub is not None:
        print(f"= хаб «{hub['name']}» уже есть ({hub['id']})")
    elif args.apply:
        try:
            hub = api(
                "POST",
                f"/guilds/{gid}/channels",
                args.token,
                payload={
                    "name": VOICE_HUB_NAME,
                    "type": 2,
                    "parent_id": voice_category["id"],
                    "user_limit": 0,
                },
            )
        except DiscordError as exc:
            print(f"ОШИБКА: не удалось создать хаб: {exc}{error_hint(exc)}", file=sys.stderr)
            return 1
        print(f"✓ создан хаб «{VOICE_HUB_NAME}» ({hub['id']})")
    else:
        print(f"→ создать хаб «{VOICE_HUB_NAME}» в «{VOICE_CATEGORY_NAME}»")

    temp_category = find_category(channels, VOICE_TEMP_CATEGORY_NAME)
    if temp_category is not None:
        print(f"= категория «{temp_category['name']}» уже есть ({temp_category['id']})")
    elif args.apply:
        try:
            temp_category = api(
                "POST",
                f"/guilds/{gid}/channels",
                args.token,
                payload={
                    "name": VOICE_TEMP_CATEGORY_NAME,
                    "type": 4,
                },
            )
        except DiscordError as exc:
            print(f"ОШИБКА: не удалось создать категорию: {exc}{error_hint(exc)}", file=sys.stderr)
            return 1
        print(f"✓ создана категория «{VOICE_TEMP_CATEGORY_NAME}» ({temp_category['id']})")
    else:
        print(f"→ создать категорию «{VOICE_TEMP_CATEGORY_NAME}»")

    if args.apply:
        config = read_json(CONFIG_FILE, {})
        channels_cfg = config.setdefault("channels", {})
        channels_cfg["voice_hub"] = VOICE_HUB_NAME
        channels_cfg["voice_temp_category"] = VOICE_TEMP_CATEGORY_NAME
        write_json_atomic(CONFIG_FILE, config)
        print("✓ PerekupDisBot/config.json обновлён")
    else:
        print("это предпросмотр. Для применения добавь --apply")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())