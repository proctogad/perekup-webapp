"""Права и доступ персонала — шаги 5–6 из SETUP.md одним запуском.

Что настраивает:
- `👑 Владелец` получает Администратора, и роль вешается на владельца сервера;
- `🛡️ Модератор` — кик, бан, тайм-ауты, управление сообщениями и никами;
- обе роли получают доступ к категории «Администрация» (`@everyone` закрыт);
- каналы-заглушки `1` в «Администрации» получают имена «админ-чат» и
  «🔊 админ-войс».

Скрипт идемпотентен: повторный запуск ничего не ломает и не дублирует.
Без --apply — только предпросмотр.

Запуск:
    python setup_staff.py --guild-name "Перекуп Калькулятор"            # предпросмотр
    python setup_staff.py --guild-name "Перекуп Калькулятор" --apply    # применить

Токен: DISCORD_BOT_TOKEN или --token. Боту нужно право Manage Roles,
а его роль должна быть выше ролей персонала (после `--order` так и есть).
"""

from __future__ import annotations

import argparse
import os
import sys

from discord_api import DiscordError, api, enable_utf8_console, error_hint, load_env, resolve_guild

VIEW_CHANNEL = 1 << 10
ADMINISTRATOR = 1 << 3

RIGHT_NAMES = {
    "Администратор": ADMINISTRATOR,
    "Кик": 1 << 1,
    "Бан": 1 << 2,
    "Тайм-ауты": 1 << 40,
    "Управление сообщениями": 1 << 13,
    "Управление никами": 1 << 27,
}

STAFF_RIGHTS = {
    "👑 Владелец": ADMINISTRATOR,
    "🛡️ Модератор": (1 << 1) | (1 << 2) | (1 << 13) | (1 << 27) | (1 << 40),
}

CATEGORY_NAME = "Администрация"
# Тип канала → новое имя (0 — текстовый, 2 — голосовой).
CHANNEL_RENAMES = {0: "админ-чат", 2: "🔊 админ-войс"}
MESSY_NAMES = {"1", "#1", "🔊1", "🔊 1"}


def rights_text(mask: int) -> str:
    """Названия прав из маски — для вывода."""
    return ", ".join(name for name, bit in RIGHT_NAMES.items() if mask & bit) or "нет"


def main() -> int:
    enable_utf8_console()
    load_env()

    ap = argparse.ArgumentParser(
        description="Права персонала и доступ к «Администрации» (шаги 5–6 из SETUP.md)"
    )
    ap.add_argument("--guild-id", help="ID сервера")
    ap.add_argument("--guild-name", help="имя сервера (поиск по подстроке)")
    ap.add_argument("--token", default=os.environ.get("DISCORD_BOT_TOKEN", ""), help="токен бота")
    ap.add_argument("--apply", action="store_true", help="применить изменения")
    args = ap.parse_args()

    if not args.token:
        ap.error("нет токена: задайте DISCORD_BOT_TOKEN или --token")
    if not args.guild_id and not args.guild_name:
        ap.error("нужен --guild-id или --guild-name")

    try:
        print("ищу сервер…", file=sys.stderr)
        guild = resolve_guild(args.token, args.guild_id, args.guild_name)
        print(f"сервер: {guild['name']} ({guild['id']})", file=sys.stderr)
        # Полный объект сервера: в списке /users/@me/guilds нет owner_id.
        guild = api("GET", f"/guilds/{guild['id']}", args.token)
        roles = api("GET", f"/guilds/{guild['id']}/roles", args.token)
        channels = api("GET", f"/guilds/{guild['id']}/channels", args.token)
    except DiscordError as exc:
        print(f"ОШИБКА: {exc}{error_hint(exc)}", file=sys.stderr)
        return 1

    gid = guild["id"]
    by_name = {r["name"].casefold(): r for r in roles}
    already = changed = planned = errors = 0

    def apply_change(method: str, path: str, payload: dict | None, label: str) -> None:
        """Выполнить изменение или (в предпросмотре) показать, что будет сделано."""
        nonlocal changed, planned, errors
        if not args.apply:
            planned += 1
            print(f"  → {label}")
            return
        try:
            api(method, path, args.token, payload=payload)
        except DiscordError as exc:
            errors += 1
            print(f"  ✗ {label}: {exc}{error_hint(exc)}", file=sys.stderr)
            return
        changed += 1
        print(f"  ✓ {label}")

    # 1. Права ролей персонала.
    print("\nправа ролей:")
    for name, wanted in STAFF_RIGHTS.items():
        role = by_name.get(name.casefold())
        if role is None:
            errors += 1
            print(f"  ✗ роли {name} нет на сервере — пропуск", file=sys.stderr)
            continue
        current = int(role["permissions"])
        if current & wanted == wanted:
            already += 1
            print(f"  = {name}: уже есть {rights_text(wanted)}")
            continue
        apply_change(
            "PATCH",
            f"/guilds/{gid}/roles/{role['id']}",
            {"permissions": str(current | wanted)},
            f"{name}: выдать {rights_text(wanted)}",
        )

    # 2. Роль 👑 Владелец — владельцу сервера (у него и так все права, это для вида).
    print("\nроль владельца сервера:")
    owner_role = by_name.get("👑 Владелец".casefold())
    owner_id = guild.get("owner_id")
    if owner_role is None or not owner_id:
        print("  = пропуск: нет роли 👑 Владелец или владельца сервера")
    else:
        member = None
        try:
            member = api("GET", f"/guilds/{gid}/members/{owner_id}", args.token)
        except DiscordError as exc:
            errors += 1
            print(f"  ✗ не удалось прочитать владельца: {exc}{error_hint(exc)}", file=sys.stderr)
        if member is not None:
            if owner_role["id"] in member.get("roles", []):
                already += 1
                print("  = 👑 Владелец уже выдан владельцу сервера")
            else:
                apply_change(
                    "PUT",
                    f"/guilds/{gid}/members/{owner_id}/roles/{owner_role['id']}",
                    None,
                    "выдать 👑 Владелец владельцу сервера",
                )

    # 3. Категория «Администрация»: @everyone закрыт, персоналу открыт.
    category = next(
        (c for c in channels if c["type"] == 4 and c["name"].casefold() == CATEGORY_NAME.casefold()),
        None,
    )
    if category is None:
        print(f"\nкатегория «{CATEGORY_NAME}» не найдена — доступ и переименование пропускаю")
    else:
        print(f"\nкатегория «{category['name']}»:")
        overwrites = {ow["id"]: ow for ow in category.get("permission_overwrites", [])}

        everyone = overwrites.get(gid)
        allow = int(everyone["allow"]) if everyone else 0
        deny = int(everyone["deny"]) if everyone else 0
        if deny & VIEW_CHANNEL:
            already += 1
            print("  = @everyone: просмотр закрыт")
        else:
            apply_change(
                "PUT",
                f"/channels/{category['id']}/permissions/{gid}",
                {"type": 0, "allow": str(allow & ~VIEW_CHANNEL), "deny": str(deny | VIEW_CHANNEL)},
                "@everyone: закрыть просмотр",
            )

        for name in STAFF_RIGHTS:
            role = by_name.get(name.casefold())
            if role is None:
                continue
            overwrite = overwrites.get(role["id"])
            allow = int(overwrite["allow"]) if overwrite else 0
            deny = int(overwrite["deny"]) if overwrite else 0
            if allow & VIEW_CHANNEL and not deny & VIEW_CHANNEL:
                already += 1
                print(f"  = {name}: просмотр открыт")
            else:
                apply_change(
                    "PUT",
                    f"/channels/{category['id']}/permissions/{role['id']}",
                    {"type": 0, "allow": str(allow | VIEW_CHANNEL), "deny": str(deny & ~VIEW_CHANNEL)},
                    f"{name}: открыть просмотр",
                )

        # 4. Переименовать каналы-заглушки в категории.
        print("\nканалы «Администрации»:")
        kids = [c for c in channels if c.get("parent_id") == category["id"]]
        if not kids:
            print("  = пусто — нечего переименовывать")
        for channel in sorted(kids, key=lambda c: c["position"]):
            target = CHANNEL_RENAMES.get(channel["type"])
            current = channel["name"]
            if target is None:
                print(f"  = «{current}» — не текстовый и не голосовой, не трогаю")
            elif current == target:
                already += 1
                print(f"  = «{current}» — уже переименован")
            elif current.strip() in MESSY_NAMES:
                apply_change(
                    "PATCH",
                    f"/channels/{channel['id']}",
                    {"name": target},
                    f"«{current}» → «{target}»",
                )
            else:
                print(f"  = «{current}» — имя уже осмысленное, не трогаю")

    print()
    if args.apply:
        summary = f"итог: настроено {changed} · уже было {already}"
        if errors:
            summary += f" · ошибок {errors}"
        print(summary)
        if changed:
            print("Обнови Discord (Ctrl+R), чтобы увидеть изменения. "
                  "Дальше — шаг 7 из SETUP.md: закрыть Community для новичков.")
    else:
        print(f"предпросмотр: к настройке {planned} · уже готово {already}")
        if planned:
            print("применить: добавьте --apply")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
