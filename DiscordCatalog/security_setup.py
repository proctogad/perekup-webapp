"""Базовая защита сервера: пинги, 2FA и канал логов модерации.

Что делает:
- запрещает `@everyone` пинговать всех (у персонала право остаётся);
- включает обязательную 2FA для модерации, если владелец уже включил 2FA
  на своём аккаунте;
- создаёт канал `#лог-модерации` в категории «Администрация» и выдаёт
  доступ владельцу и модератору.

Скрипт идемпотентен. Без `--apply` — только предпросмотр.

Запуск:
    python security_setup.py --guild-name "Перекуп Калькулятор"
    python security_setup.py --guild-name "Перекуп Калькулятор" --apply
"""

from __future__ import annotations

import argparse
import os
import sys

from discord_api import DiscordError, api, enable_utf8_console, error_hint, load_env, resolve_guild

VIEW_CHANNEL = 1 << 10
SEND_MESSAGES = 1 << 11
READ_MESSAGE_HISTORY = 1 << 16
MENTION_EVERYONE = 1 << 17
ADMINISTRATOR = 1 << 28

STAFF_ROLES = ("👑 Владелец", "🛡️ Модератор")
ADMIN_CATEGORY = "Администрация"
MODLOG_CHANNEL = "лог-модерации"
MODLOG_TOPIC = "Логи модерации, AutoMod и действий персонала. Канал виден только персоналу."


def norm(text: str) -> str:
    return " ".join(str(text).casefold().replace("#", " ").split())


def find_channel(channels: list[dict], name: str) -> dict | None:
    wanted = norm(name)
    return next((c for c in channels if norm(c["name"]) == wanted), None)


def find_category(channels: list[dict], name: str) -> dict | None:
    wanted = norm(name)
    return next(
        (c for c in channels if c["type"] == 4 and norm(c["name"]) == wanted),
        None,
    )


def main() -> int:
    enable_utf8_console()
    load_env()

    ap = argparse.ArgumentParser(description="Пинги, 2FA и лог модерации")
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
    already = changed = planned = errors = 0

    def apply_change(method: str, path: str, payload: dict | None, label: str) -> bool:
        nonlocal changed, planned, errors
        if not args.apply:
            planned += 1
            print(f"  → {label}")
            return True
        try:
            api(method, path, args.token, payload=payload)
        except DiscordError as exc:
            errors += 1
            print(f"  ✗ {label}: {exc}{error_hint(exc)}", file=sys.stderr)
            return False
        changed += 1
        print(f"  ✓ {label}")
        return True

    # 1. @everyone больше не может пинговать всех.
    print("\nупоминания @everyone:")
    everyone = next((r for r in roles if r["id"] == gid), None)
    if everyone is None:
        errors += 1
        print("  ✗ роль @everyone не найдена", file=sys.stderr)
    else:
        current = int(everyone.get("permissions", "0"))
        new_permissions = current & ~MENTION_EVERYONE
        if new_permissions == current:
            already += 1
            print("  = @everyone уже не может пинговать всех")
        else:
            apply_change(
                "PATCH",
                f"/guilds/{gid}/roles/{gid}",
                {"permissions": str(new_permissions)},
                "@everyone: запретить MENTION_EVERYONE",
            )

    # 2. Персонал может пинговать @everyone (владельцу это не нужно из-за Administrator).
    print("\nпинг для персонала:")
    for name in STAFF_ROLES:
        role = by_role.get(name.casefold())
        if role is None:
            errors += 1
            print(f"  ✗ роль {name} не найдена", file=sys.stderr)
            continue
        current = int(role.get("permissions", "0"))
        if current & ADMINISTRATOR:
            already += 1
            print(f"  = {name}: Administrator уже даёт право пинга")
            continue
        new_permissions = current | MENTION_EVERYONE
        if new_permissions == current:
            already += 1
            print(f"  = {name}: уже может пинговать @everyone")
        else:
            apply_change(
                "PATCH",
                f"/guilds/{gid}/roles/{role['id']}",
                {"permissions": str(new_permissions)},
                f"{name}: разрешить MENTION_EVERYONE",
            )

    # 3. 2FA для модерации. Если у владельца не включена 2FA, Discord не даст.
    print("\n2FA для модерации:")
    if int(guild.get("mfa_level", 0)) >= 1:
        already += 1
        print("  = уже включена")
    else:
        ok = apply_change(
            "PATCH",
            f"/guilds/{gid}",
            {"mfa_level": 1},
            "включить обязательную 2FA для модерации",
        )
        if not ok and args.apply:
            print(
                "  подсказка: сначала включи 2FA на аккаунте владельца "
                "(Настройки пользователя → Безопасность), потом повтори.",
                file=sys.stderr,
            )

    # 4. Канал логов модерации.
    print(f"\nканал «#{MODLOG_CHANNEL}»:")
    modlog = find_channel(channels, MODLOG_CHANNEL)
    admin_category = find_category(channels, ADMIN_CATEGORY)
    staff_roles = [by_role[name.casefold()] for name in STAFF_ROLES if name.casefold() in by_role]

    if modlog is None:
        overwrites = [
            {
                "id": gid,
                "type": 0,
                "allow": "0",
                "deny": str(VIEW_CHANNEL),
            }
        ]
        for role in staff_roles:
            overwrites.append(
                {
                    "id": role["id"],
                    "type": 0,
                    "allow": str(VIEW_CHANNEL | READ_MESSAGE_HISTORY | SEND_MESSAGES),
                    "deny": "0",
                }
            )
        payload = {
            "name": MODLOG_CHANNEL,
            "type": 0,
            "topic": MODLOG_TOPIC,
            "permission_overwrites": overwrites,
        }
        if admin_category is not None:
            payload["parent_id"] = admin_category["id"]
        if args.apply:
            try:
                modlog = api("POST", f"/guilds/{gid}/channels", args.token, payload=payload)
                channels.append(modlog)
                changed += 1
                print(f"  ✓ создать #{MODLOG_CHANNEL} ({modlog['id']})")
            except DiscordError as exc:
                errors += 1
                print(f"  ✗ создать #{MODLOG_CHANNEL}: {exc}{error_hint(exc)}", file=sys.stderr)
        else:
            where = f" в «{ADMIN_CATEGORY}»" if admin_category else " без категории"
            planned += 1
            print(f"  → создать #{MODLOG_CHANNEL}{where}")
    else:
        already += 1
        print(f"  = #{modlog['name']} уже есть ({modlog['id']})")

    if modlog is not None and modlog.get("type") == 0:
        overwrites = {ow["id"]: ow for ow in modlog.get("permission_overwrites", [])}

        def set_overwrite(
            target_id: str,
            label: str,
            *,
            allow_add: int,
            deny_add: int,
        ) -> None:
            nonlocal already
            current = overwrites.get(target_id)
            allow = int(current["allow"]) if current else 0
            deny = int(current["deny"]) if current else 0
            new_allow = allow | allow_add
            new_deny = deny | deny_add
            if new_allow == allow and new_deny == deny:
                already += 1
                print(f"  = {label}")
                return
            if apply_change(
                "PUT",
                f"/channels/{modlog['id']}/permissions/{target_id}",
                {"type": 0, "allow": str(new_allow), "deny": str(new_deny)},
                label,
            ):
                overwrites[target_id] = {
                    "id": target_id,
                    "type": 0,
                    "allow": str(new_allow),
                    "deny": str(new_deny),
                }

        set_overwrite(
            gid,
            "@everyone: скрыть лог",
            allow_add=0,
            deny_add=VIEW_CHANNEL,
        )
        for role in staff_roles:
            set_overwrite(
                role["id"],
                f"{role['name']}: видеть и писать в лог",
                allow_add=VIEW_CHANNEL | READ_MESSAGE_HISTORY | SEND_MESSAGES,
                deny_add=0,
            )
        if not modlog.get("topic"):
            apply_change(
                "PATCH",
                f"/channels/{modlog['id']}",
                {"topic": MODLOG_TOPIC},
                "добавить описание лога",
            )
        else:
            already += 1
            print("  = описание уже есть")
    elif modlog is not None:
        errors += 1
        print(f"  ✗ «{modlog['name']}» существует, но это не текстовый канал", file=sys.stderr)

    print(f"\nитог: настроено {changed} · уже было {already} · ошибок {errors}")
    if not args.apply:
        print("это предпросмотр. Для применения добавь --apply")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
