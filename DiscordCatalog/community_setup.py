# Включение native Community для сервера.
# Что делает:
# - добавляет фичу COMMUNITY (нативные правила, онбординг, правила-канал)
# - указывает rules_channel_id и public_updates_channel_id
# - проверяет, что уровень верификации и фильтр контента достаточны
# - без --apply — только предпросмотр.

from __future__ import annotations

import argparse
import os
import sys

from discord_api import DiscordError, api, enable_utf8_console, error_hint, load_env, resolve_guild

def norm(text: str) -> str:
    return " ".join(str(text).casefold().replace("#", " ").split())

def find_channel(channels: list[dict], name: str) -> dict | None:
    wanted = norm(name)
    return next((c for c in channels if norm(c["name"]) == wanted), None)

def main() -> int:
    enable_utf8_console()
    load_env()

    ap = argparse.ArgumentParser(description="Включить native Community")
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
    features = list(guild.get("features", []))
    rules = find_channel(channels, "📜｜правила")
    updates = find_channel(channels, "🔄｜обновления")

    if int(guild.get("mfa_level", 0)) < 1:
        print("⚠️ 2FA не включена — рекомендуется включить (security_setup.py).", file=sys.stderr)
    if int(guild.get("verification_level", 0)) < 1:
        print("⚠️ Уровень верификации меньше 1 — Discord потребует повышение.", file=sys.stderr)
    if int(guild.get("explicit_content_filter", 0)) < 1:
        print("⚠️ Фильтр контента меньше 1 — Discord потребует повышение.", file=sys.stderr)

    if not rules or not updates:
        print("❌ Не найдены #правила или #обновления — создайте их и перезапустите.", file=sys.stderr)
        return 1

    print(f"\nТекущие фичи: {features}")
    print(f"rules_channel_id:      {guild.get('rules_channel_id')}")
    print(f"public_updates_channel: {guild.get('public_updates_channel_id')}")
    print(f"system_channel_id:     {guild.get('system_channel_id')}")

    changed = already = errors = 0

    # 1. Фичи + каналы в одном PATCH (Discord требует rules_channel_id при COMMUNITY)
    if "COMMUNITY" in features:
        already += 1
        print("\n  = COMMUNITY уже включён")
    else:
        payload = {
            "features": features + ["COMMUNITY"],
            "rules_channel_id": rules["id"],
            "public_updates_channel_id": updates["id"],
        }
        if not args.apply:
            print("\n  → включить COMMUNITY + указать каналы правил и обновлений")
        else:
            try:
                api("PATCH", f"/guilds/{gid}", args.token, payload=payload)
                changed += 1
                print("\n  ✓ COMMUNITY включён, каналы проставлены")
            except DiscordError as exc:
                errors += 1
                print(f"\n  ✗ COMMUNITY: {exc}{error_hint(exc)}", file=sys.stderr)

    # 2. Каналы правил и обновлений (если COMMUNITY уже был)
    channel_payload = {
        "rules_channel_id": rules["id"],
        "public_updates_channel_id": updates["id"],
    }
    needs_channels = (
        guild.get("rules_channel_id") != rules["id"]
        or guild.get("public_updates_channel_id") != updates["id"]
    )
    if not needs_channels:
        already += 1
        print("  = rules_channel_id и public_updates_channel_id уже верные")
    elif not args.apply:
        print("  → проставить rules_channel_id и public_updates_channel_id")
    else:
        try:
            api("PATCH", f"/guilds/{gid}", args.token, payload=channel_payload)
            changed += 1
            print("  ✓ проставлены rules_channel_id и public_updates_channel_id")
        except DiscordError as exc:
            errors += 1
            print(f"  ✗ каналы Community: {exc}{error_hint(exc)}", file=sys.stderr)

    # 3. Проверка результата
    if args.apply:
        try:
            after = api("GET", f"/guilds/{gid}", args.token)
            print(f"\nИтог: features={after.get('features')}")
            print(f"rules_channel_id={after.get('rules_channel_id')} "
                  f"public_updates_channel_id={after.get('public_updates_channel_id')}")
        except DiscordError as exc:
            print(f"не удалось перепроверить: {exc}", file=sys.stderr)

    print(f"\nитог: настроено {changed} · уже было {already} · ошибок {errors}")
    if not args.apply:
        print("это предпросмотр. Для применения добавь --apply")
    return 1 if errors else 0

if __name__ == "__main__":
    raise SystemExit(main())