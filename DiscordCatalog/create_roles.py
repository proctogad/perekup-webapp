"""Роли для Discord-сервера по плану roles.plan.json.

В плане: роль на каждый канал (пинг-подписки, верификация, голосовые
комнаты), роли источника входа (Telegram / EXE / сайт) и особые роли
(персонал, бустеры, заслуги). Скрипт идемпотентен: роли ищутся по имени,
повторный запуск ничего не дублирует. Все роли создаются без прав
(permissions = 0) — чувствительные права выдаются вручную, см. roles.md.

Порядок групп в плане — это порядок ролей в иерархии Discord сверху вниз
(первыми идут персонал и особые роли). Флаг --order раскладывает роли
на сервере именно так.

Запуск:
    python create_roles.py --list                               # план без сети
    python create_roles.py --guild-name "Перекуп Калькулятор"             # предпросмотр
    python create_roles.py --guild-name "Перекуп Калькулятор" --apply     # создать
    python create_roles.py --guild-name "..." --apply --update  # + подтянуть цвета/пинг
    python create_roles.py --guild-name "..." --apply --order   # + разложить по порядку

Токен: DISCORD_BOT_TOKEN или --token. Боту нужно право Manage Roles, и его
высшая роль должна быть выше создаваемых, иначе Discord вернёт 403.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from discord_api import DiscordError, api, enable_utf8_console, error_hint, load_env, resolve_guild

DEFAULT_PLAN = Path(__file__).with_name("roles.plan.json")

# Человекочитаемые названия полей для вывода «что обновим».
LABELS = {"color": "цвет", "hoist": "показ в списке", "mentionable": "пинг"}


def load_plan(path: Path) -> dict:
    """Прочитать план ролей и проверить структуру."""
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"нет файла плана: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path}: не разобрать JSON ({exc})")
    if not plan.get("groups"):
        raise SystemExit(f"{path}: пусто — нет секции groups")
    return plan


def iter_roles(plan: dict):
    """Плоский список ролей с названием группы."""
    for group in plan["groups"]:
        for role in group["roles"]:
            yield group.get("category", "—"), role


def to_payload(role: dict) -> dict:
    """Поля роли для POST/PATCH Discord API."""
    return {
        "name": role["name"],
        "color": int(role.get("color", "#99AAB5").lstrip("#"), 16),
        "hoist": bool(role.get("hoist", False)),
        "mentionable": bool(role.get("mentionable", False)),
        "permissions": str(role.get("permissions", "0")),
    }


def print_plan(plan: dict) -> None:
    """План ролей для чтения глазами (без сети)."""
    count = 0
    for group in plan["groups"]:
        roles = group["roles"]
        count += len(roles)
        print(f"\n{group.get('category', '—')} ({len(roles)})")
        for role in roles:
            ping = "пинг" if role.get("mentionable") else "без пинга"
            line = f"  {role['name']}  ·  {role['color']}  ·  {ping}"
            if role.get("channel"):
                line += f"  ·  канал: {role['channel']}"
            print(line)
            if role.get("purpose"):
                print(f"      {role['purpose']}")
    print(f"\nвсего ролей: {count}")
    print("(порядок групп — как в иерархии Discord: сверху вниз)")


def reorder_roles(token: str, guild: dict, plan: dict) -> None:
    """Разложить роли плана сверху вниз — как группы в roles.plan.json.

    Discord разрешает боту двигать только роли НИЖЕ его собственной. Если
    роль бота лежит слишком низко (например, все роли свалены в одну кучу),
    скрипт сначала поднимает саму роль бота — а если Discord не разрешит,
    подскажет перетащить её мышкой.
    """
    url = f"/guilds/{guild['id']}/roles"
    me = api("GET", "/users/@me", token)
    plan_order = [role["name"] for _, role in iter_roles(plan)]
    plan_names = {name.casefold() for name in plan_order}
    missing: set[str] = set()

    def resolve(roles: list[dict]) -> list[dict]:
        """Найти роли плана на сервере, сохраняя порядок плана."""
        by_name = {r["name"].casefold(): r for r in roles}
        found: list[dict] = []
        for name in plan_order:
            current = by_name.get(name.casefold())
            if current is None:
                if name not in missing:
                    missing.add(name)
                    print(f"  порядок: роли «{name}» нет на сервере — пропуск", file=sys.stderr)
            else:
                found.append(current)
        return found

    def find_bot(roles: list[dict]) -> dict | None:
        """Роль бота — managed-роль с нашим bot_id в tags."""
        return next(
            (
                r
                for r in sorted(roles, key=lambda item: item["position"], reverse=True)
                if (r.get("tags") or {}).get("bot_id") == me["id"]
            ),
            None,
        )

    roles = api("GET", url, token)
    bot_role = find_bot(roles)
    if bot_role is None:
        print("  порядок: роль бота на сервере не найдена — пропускаю", file=sys.stderr)
        return

    desired = resolve(roles)
    if not desired:
        return

    # Под ролью бота должно поместиться всё: иначе поднимаем её на самый верх.
    if bot_role["position"] - 1 < len(desired) and not any(
        r["position"] > bot_role["position"] for r in desired
    ):
        print(f"  порядок: поднимаю роль бота «{bot_role['name']}» на самый верх…", file=sys.stderr)
        top = max((r["position"] for r in roles), default=0)
        try:
            api(
                "PATCH",
                url,
                token,
                payload=[{"id": bot_role["id"], "position": top + len(desired) + 1}],
            )
        except DiscordError as exc:
            print(
                f"  порядок: роль бота не поднялась автоматически ({exc}){error_hint(exc)}",
                file=sys.stderr,
            )
            print(
                "  порядок: перетащите роль бота в самый верх списка ролей "
                "(Настройки сервера → Роли) и запустите снова",
                file=sys.stderr,
            )
            return
        roles = api("GET", url, token)
        bot_role = find_bot(roles)
        if bot_role is None:
            print("  порядок: роль бота исчезла из списка — пропускаю", file=sys.stderr)
            return
        desired = resolve(roles)

    bot_pos = bot_role["position"]
    movable = [r for r in desired if r["position"] < bot_pos]
    for role in desired:
        if role["position"] >= bot_pos:
            print(
                f"  порядок: «{role['name']}» сейчас не ниже роли бота — оставляю как есть",
                file=sys.stderr,
            )
    if not movable:
        return

    # Ставим роли плана выше прочих ролей, которые тоже лежат ниже роли бота.
    other_positions = [
        r["position"]
        for r in roles
        if r["id"] != bot_role["id"]
        and 0 < r["position"] < bot_pos
        and r["name"].casefold() not in plan_names
    ]
    start = max(other_positions, default=0) + 1
    if start + len(movable) - 1 >= bot_pos:
        print(
            "  порядок: под ролью бота не хватает места — поднимите её выше и запустите снова",
            file=sys.stderr,
        )
        return

    # Снизу вверх: последняя роль плана получает самую низкую позицию.
    payload = [
        {"id": r["id"], "position": start + i}
        for i, r in enumerate(reversed(movable))
    ]
    api("PATCH", url, token, payload=payload)

    roles = api("GET", url, token)
    roles.sort(key=lambda r: r["position"], reverse=True)
    print("  порядок ролей (сверху вниз):")
    for r in roles:
        if r["position"] > 0:
            print(f"    {r['name']}")


def main() -> int:
    enable_utf8_console()
    load_env()

    ap = argparse.ArgumentParser(description="Создать роли Discord по плану roles.plan.json")
    ap.add_argument("--guild-id", help="ID сервера")
    ap.add_argument("--guild-name", help="имя сервера (поиск по подстроке); по умолчанию — из плана")
    ap.add_argument("--token", default=os.environ.get("DISCORD_BOT_TOKEN", ""), help="токен бота")
    ap.add_argument("--plan-file", default=str(DEFAULT_PLAN), help="файл плана ролей")
    ap.add_argument("--apply", action="store_true", help="создать роли (без флага — только предпросмотр)")
    ap.add_argument("--update", action="store_true", help="обновить цвет/пинг/hoist у уже существующих ролей")
    ap.add_argument(
        "--order",
        action="store_true",
        help="разложить роли сверху вниз по порядку плана (работает вместе с --apply)",
    )
    ap.add_argument("--list", action="store_true", help="показать план без обращения к API")
    args = ap.parse_args()

    plan = load_plan(Path(args.plan_file))

    if args.list:
        print(f"План ролей — {plan.get('guild_name', 'сервер')}")
        print_plan(plan)
        return 0

    if not args.token:
        ap.error("нет токена: задайте DISCORD_BOT_TOKEN или --token")
    guild_name = args.guild_name or (None if args.guild_id else plan.get("guild_name"))
    if not args.guild_id and not guild_name:
        ap.error("нужен --guild-id или --guild-name")

    try:
        print("ищу сервер…", file=sys.stderr)
        guild = resolve_guild(args.token, args.guild_id, guild_name)
        print(f"сервер: {guild['name']} ({guild['id']})", file=sys.stderr)
        existing = api("GET", f"/guilds/{guild['id']}/roles", args.token)
    except DiscordError as exc:
        print(f"ОШИБКА: {exc}{error_hint(exc)}", file=sys.stderr)
        print(
            "Подсказка: боту нужно право Manage Roles, и его высшая роль должна быть "
            "выше создаваемых.",
            file=sys.stderr,
        )
        return 1

    by_name = {r["name"].casefold(): r for r in existing}
    created: list[str] = []
    updated: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    differing: list[str] = []

    for category, role in iter_roles(plan):
        current = by_name.get(role["name"].casefold())
        if current:
            payload = to_payload(role)
            changes = {
                key: payload[key]
                for key in ("color", "hoist", "mentionable")
                if current.get(key) != payload[key]
            }
            if changes:
                differing.append(role["name"])
            if args.update and changes:
                if args.apply:
                    try:
                        api(
                            "PATCH",
                            f"/guilds/{guild['id']}/roles/{current['id']}",
                            args.token,
                            payload=changes,
                        )
                    except DiscordError as exc:
                        failed.append(role["name"])
                        print(f"  ✗ {role['name']}: {exc}{error_hint(exc)}", file=sys.stderr)
                        continue
                    updated.append(role["name"])
                    print(f"  обновлена:   {role['name']}  ({', '.join(LABELS[k] for k in changes)})")
                else:
                    print(f"  [обновить]   {role['name']}  ({', '.join(LABELS[k] for k in changes)})")
            else:
                skipped.append(role["name"])
                print(f"  уже есть:    {role['name']}  ({category})")
            continue

        if args.apply:
            try:
                new_role = api(
                    "POST",
                    f"/guilds/{guild['id']}/roles",
                    args.token,
                    payload=to_payload(role),
                )
            except DiscordError as exc:
                failed.append(role["name"])
                print(f"  ✗ {role['name']}: {exc}{error_hint(exc)}", file=sys.stderr)
                continue
            created.append(role["name"])
            print(f"  создана:     {role['name']}  ({category})  id={new_role['id']}")
        else:
            created.append(role["name"])
            print(f"  [создать]    {role['name']}  ({category})")

    print()
    if args.apply:
        summary = f"итог: создано {len(created)} · обновлено {len(updated)} · уже было {len(skipped)}"
        if failed:
            summary += f" · ошибок {len(failed)}"
        print(summary)
        if created and not args.order:
            print("Роли добавляются вниз списка — чтобы разложить их по порядку, запустите ещё раз с --order.")
    else:
        print(f"предпросмотр: к созданию {len(created)} · к обновлению {len(updated)} · уже есть {len(skipped)}")
        if created or updated:
            print("применить: добавьте --apply")
        if args.order:
            print("порядок ролей разложится вместе с --apply")
    if differing and not args.update:
        print(f"отличия от плана у {len(differing)} ролей: запустите с --update, чтобы подтянуть цвета и пинг.")

    if args.order and args.apply:
        print()
        try:
            reorder_roles(args.token, guild, plan)
        except DiscordError as exc:
            print(f"ОШИБКА: не удалось разложить роли: {exc}{error_hint(exc)}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
