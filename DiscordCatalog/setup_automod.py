# AutoMod правила для сервера "Перекуп Калькулятор".
# Что делает:
# - блокирует спам и флуд
# - блокирует массовые упоминания (пинг-спам, рейд по упоминаниям)
# - блокирует рекламу Discord-инвайтов
# - блокирует сообщения из 12+ заглавных букв подряд (капс)
# - отправляет алерты в #лог-модерации
# - персонал и бот избавлены, как и каналы #админ-чат и #лог-модерации

from __future__ import annotations

import argparse
import os
import sys

from discord_api import DiscordError, api, enable_utf8_console, error_hint, load_env, resolve_guild

# Типы триггеров AutoMod (Discord API).
TRIGGER_KEYWORD = 1
TRIGGER_HARMFUL_LINK = 2
TRIGGER_SPAM = 3
TRIGGER_MENTION_SPAM = 5

# Типы действий AutoMod.
ACTION_BLOCK = 1
ACTION_ALERT = 2
ACTION_TIMEOUT = 3

# Discord API: event_type for message_send is 1.
EVENT_MESSAGE_SEND = 1

MAX_TIMEOUT = 2419200  # 28 дней

STAFF_ROLES = ("👑 Владелец", "🛡️ Модератор")
BOT_ROLES = ("🤖 Бот",)
EXEMPT_CHANNELS = ("админ-чат", "лог-модерации")
MODLOG_CHANNEL = "лог-модерации"

INVITE_PATTERNS = [
    r"(?:https?://)?(?:www\.)?discord\.gg/[A-Za-z0-9]+",
    r"(?:https?://)?(?:www\.)?discord(?:app)?\.com/invite/[A-Za-z0-9]+",
    r"(?:https?://)?(?:www\.)?dsc\.gg/[A-Za-z0-9]+",
]

def norm(text: str) -> str:
    return " ".join(str(text).casefold().replace("#", " ").split())

def find_channel(channels: list[dict], name: str) -> dict | None:
    wanted = norm(name)
    return next((c for c in channels if norm(c["name"]) == wanted), None)

def main() -> int:
    enable_utf8_console()
    load_env()

    ap = argparse.ArgumentParser(description="AutoMod-правила сервера")
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
        roles = api("GET", f"/guilds/{guild['id']}/roles", args.token)
        channels = api("GET", f"/guilds/{guild['id']}/channels", args.token)
    except DiscordError as exc:
        print(f"ОШИБКА: {exc}{error_hint(exc)}", file=sys.stderr)
        return 1

    gid = guild["id"]
    by_role = {r["name"].casefold(): r for r in roles}
    modlog = find_channel(channels, MODLOG_CHANNEL)
    if modlog is None:
        print(
            f"ОШИБКА: не нашёл канал «#{MODLOG_CHANNEL}». "
            "Сначала запусти security_setup.py --apply.",
            file=sys.stderr,
        )
        return 1

    exempt_roles: list[str] = []
    for name in STAFF_ROLES + BOT_ROLES:
        role = by_role.get(name.casefold())
        if role is None:
            print(f"  ! роль {name} не найдена — она не будет избавлена от правил", file=sys.stderr)
            continue
        exempt_roles.append(role["id"])

    exempt_channels: list[str] = []
    for name in EXEMPT_CHANNELS:
        channel = find_channel(channels, name)
        if channel is None:
            print(f"  ! канал «{name}» не найден — он не будет избавлен от правил", file=sys.stderr)
            continue
        exempt_channels.append(channel["id"])

    def block(message: str) -> dict:
        return {"type": ACTION_BLOCK, "metadata": {"custom_message": message}}

    def alert() -> dict:
        return {"type": ACTION_ALERT, "metadata": {"channel_id": modlog["id"]}}

    def timeout(seconds: int) -> dict:
        return {"type": ACTION_TIMEOUT, "metadata": {"duration_seconds": seconds}}

    # Список правил, которые точно поддерживаются в текущей версии API
    rules = [
        {
            "name": "📣 Анти-пинг-спам",
            "trigger_type": TRIGGER_MENTION_SPAM,
            "trigger_metadata": {
                "mention_total_limit": 6,
                "mention_raid_protection_enabled": True,
            },
            "actions": [
                block("Слишком много упоминаний в одном сообщении."),
                alert(),
                timeout(300),
            ],
        },
        {
            "name": "🚫 Реклама Discord-серверов",
            "trigger_type": TRIGGER_KEYWORD,
            "trigger_metadata": {
                "keyword_filter": ["discord.gg/", "discord.com/invite", "dsc.gg/"],
                "regex_patterns": INVITE_PATTERNS,
                "allow_list": [],
            },
            "actions": [
                block("Реклама других Discord-серверов запрещена. Напиши администрации, если это важно."),
                alert(),
            ],
        },
        {
            "name": "🔠 Капс",
            "trigger_type": TRIGGER_KEYWORD,
            "trigger_metadata": {
                "keyword_filter": [],
                "regex_patterns": [r"\b[А-ЯЁA-Z]{12,}\b"],
                "allow_list": [],
            },
            "actions": [
                block("Слишком много заглавных букв. Напиши нормальным регистром."),
                alert(),
            ],
        },
        {
            "name": "🛡️ Антиспам",
            "trigger_type": TRIGGER_SPAM,
            "trigger_metadata": {},
            "actions": [
                block("Сообщение удалено: слишком похоже на спам. Не флуди — пиши по теме."),
                alert(),
            ],
        },
    ]

    try:
        existing = api("GET", f"/guilds/{gid}/auto-moderation/rules", args.token)
    except DiscordError as exc:
        print(f"ОШИБКА: {exc}{error_hint(exc)}", file=sys.stderr)
        return 1

    by_name = {rule.get("name"): rule for rule in existing}
    changed = already = errors = 0

    print("\nAutoMod:")
    for rule in rules:
        payload = {
            "name": rule["name"],
            "event_type": EVENT_MESSAGE_SEND,
            "trigger_type": rule["trigger_type"],
            "trigger_metadata": rule["trigger_metadata"],
            "actions": rule["actions"],
            "enabled": True,
            "exempt_roles": exempt_roles,
            "exempt_channels": exempt_channels,
        }
        current = by_name.get(rule["name"])
        if current is None:
            if not args.apply:
                print(f"  → создать «{rule['name']}»")
                continue
            try:
                api("POST", f"/guilds/{gid}/auto-moderation/rules", args.token, payload=payload)
            except DiscordError as exc:
                errors += 1
                print(f"  ✗ «{rule['name']}»: {exc}{error_hint(exc)}", file=sys.stderr)
                continue
            changed += 1
            print(f"  ✓ создано «{rule['name']}»")
        else:
            same = (
                current.get("enabled") == payload["enabled"]
                and int(current.get("trigger_type", 0)) == payload["trigger_type"]
                and current.get("trigger_metadata", {}) == payload["trigger_metadata"]
                and len(current.get("actions", [])) == len(payload["actions"])
                and sorted(current.get("exempt_roles", [])) == sorted(payload["exempt_roles"])
                and sorted(current.get("exempt_channels", [])) == sorted(payload["exempt_channels"])
            )
            if same:
                already += 1
                print(f"  = «{rule['name']}» уже настроено")
                continue
            if not args.apply:
                print(f"  → обновить «{rule['name']}»")
                continue
            try:
                api(
                    "PATCH",
                    f"/guilds/{gid}/auto-moderation/rules/{current['id']}",
                    args.token,
                    payload=payload,
                )
            except DiscordError as exc:
                errors += 1
                print(f"  ✗ «{rule['name']}»: {exc}{error_hint(exc)}", file=sys.stderr)
                continue
            changed += 1
            print(f"  ✓ обновлено «{rule['name']}»")

    print(f"\nитог: настроено {changed} · уже было {already} · ошибок {errors}")
    if not args.apply:
        print("это предпросмотр. Для применения добавь --apply")
    return 1 if errors else 0

if __name__ == "__main__":
    raise SystemExit(main())