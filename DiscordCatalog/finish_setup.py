"""Финальная настройка сервера: Community для новичков, #роли, описания каналов.

Что делает:
- включает уровень проверки сервера (verification level 2) и фильтр
  откровенного контента;
- закрывает категорию `Community` от `@everyone` и открывает её ролям
  `✅ Знает правила`, `👑 Владелец`, `🛡️ Модератор`;
- создаёт канал `#роли` в категории «Основные каналы» и делает его
  read-only для `@everyone` (писать может бот и персонал);
- в информационных каналах (новости, медиа, обновления, установка, правила)
  запрещает писать `@everyone`, оставляя право записи владельцу и модератору;
- заполняет описания (topic) основных каналов, если они ещё пустые.

Скрипт идемпотентен: повторный запуск ничего не ломает и не дублирует.
Без --apply — только предпросмотр.

Запуск:
    python finish_setup.py --guild-name "Перекуп Калькулятор"            # предпросмотр
    python finish_setup.py --guild-name "Перекуп Калькулятор" --apply    # применить

Токен: DISCORD_BOT_TOKEN или `.env` рядом со скриптом. Боту нужны права
Manage Channels, Manage Roles и Manage Guild (на сервере у роли Perekup
уже есть Administrator).
"""

from __future__ import annotations

import argparse
import os
import sys

from discord_api import DiscordError, api, enable_utf8_console, error_hint, load_env, resolve_guild

VIEW_CHANNEL = 1 << 10
SEND_MESSAGES = 1 << 11
READ_MESSAGE_HISTORY = 1 << 16
ADD_REACTIONS = 1 << 6
MENTION_EVERYONE = 1 << 17
MANAGE_THREADS = 1 << 34
CREATE_PUBLIC_THREADS = 1 << 35
CREATE_PRIVATE_THREADS = 1 << 36
SEND_MESSAGES_IN_THREADS = 1 << 38

READ_ONLY_ALLOW = VIEW_CHANNEL | READ_MESSAGE_HISTORY
READ_ONLY_DENY = (
    SEND_MESSAGES
    | ADD_REACTIONS
    | MENTION_EVERYONE
    | MANAGE_THREADS
    | CREATE_PUBLIC_THREADS
    | CREATE_PRIVATE_THREADS
    | SEND_MESSAGES_IN_THREADS
)

COMMUNITY_CATEGORY = "Community"
MAIN_CATEGORY = "Основные каналы"
ROLES_CHANNEL = "роли"

VERIFIED_ROLE = "✅ Знает правила"
STAFF_ROLES = ("👑 Владелец", "🛡️ Модератор")

# Каналы «только для чтения»: писать может персонал, остальные — нет.
INFO_READ_ONLY = {
    "1559946759273451542": "📰｜новости",
    "1559946822582018108": "🎬｜медиа-новости",
    "1559946867335241728": "🔄｜обновления",
    "1559946939460624524": "📥｜установка",
    "1559946990165426266": "📜｜правила",
}
INFO_READ_ONLY_DENY = (
    SEND_MESSAGES
    | MANAGE_THREADS
    | CREATE_PUBLIC_THREADS
    | CREATE_PRIVATE_THREADS
    | SEND_MESSAGES_IN_THREADS
)
STAFF_WRITE_ALLOW = VIEW_CHANNEL | READ_MESSAGE_HISTORY | SEND_MESSAGES

# ID каналов сервера → описание. ID надёжнее имени: имя можно переименовать.
TOPICS = {
    "1559945025607962628": "👋 Знакомство и приветствия. Здесь бот встречает новичков и показывает, откуда они пришли.",
    "1559946759273451542": "📰 Новости проекта и сервера. Подписка на пинги — кнопкой в #роли.",
    "1559946822582018108": "🎬 Ролики, обзоры и медиа-дайджесты. Подписка на пинги — в #роли.",
    "1559946867335241728": "🔄 Релизы калькулятора и список изменений. Подписка на пинги — в #роли.",
    "1559946939460624524": "📥 Как установить калькулятор: EXE, веб-версия и инструкция. Новые версии — в #обновления.",
    "1559946990165426266": "📜 Правила сервера. Прочитал — нажми кнопку «Я прочитал правила», чтобы открыть Community.",
    "1559947279950020688": "⭐ Отзывы о калькуляторе и перекупах. Подписка на новые отзывы — в #роли.",
    "1559947305560309940": "💡 Идеи и предложения по калькулятору. Подписка на обсуждения — в #роли.",
    "1559947326540488914": "🆘 Помощь новичкам: установка, расчёты, ошибки. Живой ответ — тег @🧠 Хелпер.",
    "1559947351685074944": "💬 Общий чат сервера. Подписка на сборы и движухи — в #роли.",
    "1559947377417130004": "🖼️ Скины, скриншоты и мемы. Подписка на новые посты — в #роли.",
    "1559947397319098518": "🐛 Баги и ошибки калькулятора. Нашёл баг — напиши сюда с шагами воспроизведения.",
}


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

    ap = argparse.ArgumentParser(
        description="Финальная настройка: Community, #роли, описания каналов"
    )
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

    # 1. Защита сервера: проверка новичков и фильтр контента.
    print("\nзащита сервера:")
    guard_payload: dict = {}
    if int(guild.get("verification_level", 0)) < 2:
        guard_payload["verification_level"] = 2
        print("  → уровень проверки: средний (аккаунт старше 5 минут)")
    else:
        already += 1
        print("  = уровень проверки уже средний или выше")
    if int(guild.get("explicit_content_filter", 0)) < 2:
        guard_payload["explicit_content_filter"] = 2
        print("  → фильтр откровенного контента: сканировать всех")
    else:
        already += 1
        print("  = фильтр откровенного контента уже включён")
    if guard_payload:
        apply_change("PATCH", f"/guilds/{gid}", guard_payload, "обновить защиту сервера")

    # 2. Категория Community: скрыть от @everyone, открыть после верификации.
    print(f"\nкатегория «{COMMUNITY_CATEGORY}»:")
    community = find_category(channels, COMMUNITY_CATEGORY)
    if community is None:
        errors += 1
        print(f"  ✗ категория «{COMMUNITY_CATEGORY}» не найдена — пропуск", file=sys.stderr)
    else:
        overwrites = {ow["id"]: ow for ow in community.get("permission_overwrites", [])}

        def set_overwrite(
            target_id: str,
            label: str,
            *,
            allow_add: int = 0,
            allow_remove: int = 0,
            deny_add: int = 0,
            deny_remove: int = 0,
        ) -> None:
            nonlocal already
            current = overwrites.get(target_id)
            allow = int(current["allow"]) if current else 0
            deny = int(current["deny"]) if current else 0
            new_allow = (allow | allow_add) & ~allow_remove
            new_deny = (deny | deny_add) & ~deny_remove
            if new_allow == allow and new_deny == deny:
                already += 1
                print(f"  = {label}")
                return
            payload = {"type": 0, "allow": str(new_allow), "deny": str(new_deny)}
            if apply_change(
                "PUT",
                f"/channels/{community['id']}/permissions/{target_id}",
                payload,
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
            "@everyone: закрыть просмотр",
            allow_remove=VIEW_CHANNEL,
            deny_add=VIEW_CHANNEL,
        )
        verified = by_role.get(VERIFIED_ROLE.casefold())
        if verified is not None:
            set_overwrite(
                verified["id"],
                f"{VERIFIED_ROLE}: открыть просмотр",
                allow_add=VIEW_CHANNEL,
                deny_remove=VIEW_CHANNEL,
            )
        else:
            errors += 1
            print(f"  ✗ роли {VERIFIED_ROLE} нет — некому открыть Community", file=sys.stderr)
        for name in STAFF_ROLES:
            role = by_role.get(name.casefold())
            if role is not None:
                set_overwrite(
                    role["id"],
                    f"{name}: открыть просмотр",
                    allow_add=VIEW_CHANNEL,
                    deny_remove=VIEW_CHANNEL,
                )

    # 3. Канал #роли: создать или привести к read-only.
    print(f"\nканал «#{ROLES_CHANNEL}»:")
    main_category = find_category(channels, MAIN_CATEGORY)
    roles_channel = find_channel(channels, ROLES_CHANNEL)
    roles_topic = (
        "🔔 Панель подписок: включи пинги о новостях, обновлениях, мероприятиях "
        "и голосовых комнатах. Нажми кнопку — роль появится, нажми ещё раз — снимется."
    )

    if roles_channel is None:
        roles_overwrites = [
            {"id": gid, "type": 0, "allow": str(READ_ONLY_ALLOW), "deny": str(READ_ONLY_DENY)}
        ]
        for name in STAFF_ROLES:
            role = by_role.get(name.casefold())
            if role is not None:
                roles_overwrites.append(
                    {"id": role["id"], "type": 0, "allow": str(STAFF_WRITE_ALLOW), "deny": "0"}
                )
        payload = {
            "name": ROLES_CHANNEL,
            "type": 0,
            "topic": roles_topic,
            "permission_overwrites": roles_overwrites,
        }
        if main_category is not None:
            payload["parent_id"] = main_category["id"]
        if args.apply:
            try:
                roles_channel = api("POST", f"/guilds/{gid}/channels", args.token, payload=payload)
                channels.append(roles_channel)
                changed += 1
                print(f"  ✓ создать #{ROLES_CHANNEL} (read-only, только бот может писать)")
            except DiscordError as exc:
                errors += 1
                print(f"  ✗ создать #{ROLES_CHANNEL}: {exc}{error_hint(exc)}", file=sys.stderr)
        else:
            where = f" в «{main_category['name']}»" if main_category else " без категории"
            planned += 1
            print(f"  → создать #{ROLES_CHANNEL}{where}, read-only для @everyone")
    else:
        if roles_channel["type"] != 0:
            errors += 1
            print(f"  ✗ «{roles_channel['name']}» существует, но это не текстовый канал", file=sys.stderr)
        else:
            already += 1
            print(f"  = #{roles_channel['name']} уже есть")
            current = next(
                (ow for ow in roles_channel.get("permission_overwrites", []) if ow["id"] == gid),
                None,
            )
            allow = int(current["allow"]) if current else 0
            deny = int(current["deny"]) if current else 0
            new_allow = (allow | READ_ONLY_ALLOW) & ~READ_ONLY_DENY
            new_deny = (deny | READ_ONLY_DENY) & ~READ_ONLY_ALLOW
            if new_allow == allow and new_deny == deny:
                already += 1
                print("  = @everyone уже не может писать")
            else:
                apply_change(
                    "PUT",
                    f"/channels/{roles_channel['id']}/permissions/{gid}",
                    {"type": 0, "allow": str(new_allow), "deny": str(new_deny)},
                    "@everyone: запретить писать и реагировать",
                )

            role_overwrites = {
                ow["id"]: ow for ow in roles_channel.get("permission_overwrites", [])
            }
            for name in STAFF_ROLES:
                role = by_role.get(name.casefold())
                if role is None:
                    continue
                overwrite = role_overwrites.get(role["id"])
                staff_allow = int(overwrite["allow"]) if overwrite else 0
                staff_deny = int(overwrite["deny"]) if overwrite else 0
                new_staff_allow = staff_allow | STAFF_WRITE_ALLOW
                new_staff_deny = staff_deny & ~STAFF_WRITE_ALLOW
                if new_staff_allow == staff_allow and new_staff_deny == staff_deny:
                    already += 1
                    print(f"  = {name}: уже может писать")
                else:
                    apply_change(
                        "PUT",
                        f"/channels/{roles_channel['id']}/permissions/{role['id']}",
                        {
                            "type": 0,
                            "allow": str(new_staff_allow),
                            "deny": str(new_staff_deny),
                        },
                        f"{name}: разрешить писать",
                    )

            if not roles_channel.get("topic"):
                apply_change(
                    "PATCH",
                    f"/channels/{roles_channel['id']}",
                    {"topic": roles_topic},
                    "добавить описание канала",
                )
            else:
                already += 1
                print("  = описание уже есть")

    # 4. Информационные каналы: @everyone только читает, персонал пишет.
    print("\nканалы только для чтения:")
    for channel_id, fallback_name in INFO_READ_ONLY.items():
        channel = next((c for c in channels if c["id"] == channel_id), None)
        if channel is None:
            print(f"  = канал «{fallback_name}» не найден — пропуск")
            continue
        if channel["type"] != 0:
            print(f"  = «{channel['name']}» — не текстовый канал, пропуск")
            continue

        overwrites = {ow["id"]: ow for ow in channel.get("permission_overwrites", [])}
        everyone = overwrites.get(gid)
        allow = int(everyone["allow"]) if everyone else 0
        deny = int(everyone["deny"]) if everyone else 0
        new_allow = (allow | VIEW_CHANNEL | READ_MESSAGE_HISTORY) & ~INFO_READ_ONLY_DENY
        new_deny = (deny | INFO_READ_ONLY_DENY) & ~(VIEW_CHANNEL | READ_MESSAGE_HISTORY)
        if new_allow == allow and new_deny == deny:
            already += 1
            print(f"  = «{channel['name']}»: @everyone уже только читает")
        else:
            apply_change(
                "PUT",
                f"/channels/{channel['id']}/permissions/{gid}",
                {"type": 0, "allow": str(new_allow), "deny": str(new_deny)},
                f"«{channel['name']}»: @everyone — только чтение",
            )

        for name in STAFF_ROLES:
            role = by_role.get(name.casefold())
            if role is None:
                continue
            overwrite = overwrites.get(role["id"])
            staff_allow = int(overwrite["allow"]) if overwrite else 0
            staff_deny = int(overwrite["deny"]) if overwrite else 0
            new_staff_allow = staff_allow | STAFF_WRITE_ALLOW
            new_staff_deny = staff_deny & ~STAFF_WRITE_ALLOW
            if new_staff_allow == staff_allow and new_staff_deny == staff_deny:
                already += 1
                print(f"  = «{channel['name']}»: {name} уже может писать")
            else:
                apply_change(
                    "PUT",
                    f"/channels/{channel['id']}/permissions/{role['id']}",
                    {
                        "type": 0,
                        "allow": str(new_staff_allow),
                        "deny": str(new_staff_deny),
                    },
                    f"«{channel['name']}»: {name} — разрешить писать",
                )

    # 5. Описания основных каналов (только если topic пустой).
    print("\nописания каналов:")
    for channel_id, topic in TOPICS.items():
        channel = next((c for c in channels if c["id"] == channel_id), None)
        if channel is None:
            print(f"  = канал {channel_id} не найден — пропуск")
            continue
        if channel.get("topic"):
            already += 1
            print(f"  = «{channel['name']}» — описание уже есть")
            continue
        apply_change(
            "PATCH",
            f"/channels/{channel_id}",
            {"topic": topic},
            f"«{channel['name']}»: добавить описание",
        )

    print()
    if args.apply:
        summary = f"итог: настроено {changed} · уже было {already}"
        if errors:
            summary += f" · ошибок {errors}"
        print(summary)
        if changed:
            print(
                "Дальше: запусти бота (PerekupDisBot/README.md), затем на сервере "
                "команды /panel и /verify-panel."
            )
    else:
        print(f"предпросмотр: к настройке {planned} · уже готово {already}")
        if planned:
            print("применить: добавьте --apply")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
