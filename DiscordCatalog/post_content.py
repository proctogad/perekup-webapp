# Автопосты контента в каналы Discord.
# Что делает:
# - публикует/обновляет приветствие в #новости
# - публикует/обновляет информацию об обновлениях в #обновления
# - публикует инструкцию в #установка и закрепляет её
# - публикует информацию о медиа-новости в #медиа-новости
# - публикует приветствие в #welcom
# Идемпотентен: маркеры в футерах Embed allow обновлять те же сообщения.
# Без --apply — только предпросмотр.

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from discord_api import DiscordError, api, enable_utf8_console, error_hint, load_env, resolve_guild

BASE_DIR = Path(__file__).resolve().parent.parent / "PerekupDisBot"
CONFIG_FILE = BASE_DIR / "config.json"
STATE_FILE = BASE_DIR / "state.json"

BRAND_COLOR = 0x5865F2

MARKERS = {
    "news":    "Perekup • контент: новости",
    "updates": "Perekup • контент: обновления",
    "install": "Perekup • контент: установка",
    "media":   "Perekup • контент: медиа",
    "welcome": "Perekup • приветствие",
}

SITE_URL   = "https://proctogad.github.io/perekup-webapp/"
WORKER_URL = "https://neon-reseller.neon-reseller-worker.workers.dev"
TELEGRAM_BOT = "@perekup5rp_bot"
RELEASES_URL = "https://github.com/proctogad/perekup-webapp/releases/latest"
TELEGRAM_URL = f"https://t.me/{TELEGRAM_BOT.strip('@')}"


def norm(text: str) -> str:
    return " ".join(str(text).casefold().replace("#", " ").split())


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def link_row(labels_urls: list[tuple[str, str]]) -> list[dict]:
    """Возвращает список из одного ActionRow со ссылками (макс 5)."""
    return [
        {
            "type": 1,
            "components": [
                {"type": 2, "style": 5, "label": label, "url": url}
                for label, url in labels_urls
            ],
        }
    ]


def embed(title: str, description: str | None = None, *,
          color: int = BRAND_COLOR, fields: list[dict] | None = None) -> dict:
    e = {"title": title, "color": color, "footer": {"text": ""}}
    if description:
        e["description"] = description
    if fields:
        e["fields"] = fields
    return e


def find_message_by_marker(channel_id: int, marker: str, bot_id: int, token: str) -> dict | None:
    """Найти последнее сообщение бота с маркером в футере."""
    try:
        msgs = api("GET", f"/channels/{channel_id}/messages?limit=50", token)
    except DiscordError:
        return None
    for m in msgs:
        if m.get("author", {}).get("id") == bot_id and m.get("embeds"):
            footer = m["embeds"][0].get("footer", {}).get("text", "")
            if marker in footer:
                return m
    return None


def upsert_message(channel_id: int, marker_key: str, builder: callable, token: str,
                   bot_id: int, row: list[dict] | None = None, pin: bool = False,
                   state: dict | None = None) -> dict | None:
    """Создать или обновить сообщение с маркером. Вернуть message dict."""
    marker = MARKERS[marker_key]
    existing = find_message_by_marker(channel_id, marker, bot_id, token)
    payload = {"embeds": [builder()]}
    if row:
        payload["components"] = row
    if existing is None:
        created = api("POST", f"/channels/{channel_id}/messages", token, payload=payload)
        if state is not None:
            state.setdefault("post_ids", {})[marker_key] = created["id"]
            write_json_atomic(STATE_FILE, state)
    else:
        created = api("PATCH", f"/channels/{channel_id}/messages/{existing['id']}",
                      token, payload=payload)
    if pin and created is not None:
        try:
            api("PUT", f"/channels/{channel_id}/pins/{created['id']}", token)
        except DiscordError as exc:
            print(f"  ! не удалось закрепить: {exc}", file=sys.stderr)
    return created


def main() -> int:
    enable_utf8_console()
    load_env()

    ap = argparse.ArgumentParser(description="Автопосты контента")
    ap.add_argument("--guild-id", default=os.environ.get("DISCORD_GUILD_ID"), help="ID сервера")
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
        channels = api("GET", f"/guilds/{guild['id']}/channels", args.token)
        me = api("GET", "/users/@me", args.token)
    except DiscordError as exc:
        print(f"ОШИБКА: {exc}{error_hint(exc)}", file=sys.stderr)
        return 1

    gid = guild["id"]
    bot_id = me["id"]
    cnames = {norm(c["name"]): c["id"] for c in channels}
    news_id = cnames.get(norm("📰｜новости"))
    updates_id = cnames.get(norm("🔄｜обновления"))
    install_id = cnames.get(norm("📥｜установка"))
    media_id = cnames.get(norm("🎬｜медиа-новости"))
    welcome_id = cnames.get(norm("👋｜welcom"))
    rules_id = cnames.get(norm("📜｜правила"))

    missing = [n for n, cid in [
        ("📰｜новости", news_id), ("🔄｜обновления", updates_id),
        ("📥｜установка", install_id), ("🎬｜медиа-новости", media_id),
        ("👋｜welcom", welcome_id), ("📜｜правила", rules_id),
    ] if not cid]
    if missing:
        print(f"ОШИБКА: не найдены каналы: {missing}", file=sys.stderr)
        return 1

    state = read_json(STATE_FILE, {})
    changed = already = errors = 0

    def action(label: str) -> None:
        nonlocal changed, already
        if args.apply:
            changed += 1
        else:
            already += 1
        print(f"  {'✓' if args.apply else '→'} {label}")

    print("\nНовости:")
    if not args.apply:
        print(f"  → создать/обновить «{MARKERS['news']}» в #📰｜новости")
    else:
        upsert_message(
            news_id, "news",
            lambda: {
                **embed("🚀 Сервер «Перекуп Калькулятор» открыт",
                        description=(
                            "Добро пожаловать в сообщество **GTA5RP | Перекуп Калькулятор**.\n\n"
                            "Здесь считаем перекуп, проверяем уставку и делимся тем, что реально приносит прибыль.\n\n"
                            "**Что уже есть:**\n"
                            "• 📥 инструкция по установке — в #установка\n"
                            "• 💬 общение, отзывы, идеи, помощь и баги — в Community\n"
                            "• 🔔 подписки на новости, обновления, мероприятия и войс — в #роли\n"
                            "• ✅ верификация и доступ к закрытым каналам — в #правила\n\n"
                            "**Полезные ссылки — кнопками ниже.**"
                        ),
                        fields=[{"name": "С чего начать",
                                 "value": "1. Прочитай #правила\n"
                                          "2. Нажми «Я прочитал правила»\n"
                                          "3. Выбери подписки в #роли\n"
                                          "4. Загляни в #установка",
                                 "inline": False}]),
                **{"footer": {"text": MARKERS["news"]}}
            },
            token=args.token, bot_id=bot_id,
            row=link_row([
                ("Открыть калькулятор", WORKER_URL),
                ("Telegram-бот", TELEGRAM_URL),
                ("Скачать EXE", RELEASES_URL),
                ("Веб-версия", SITE_URL),
            ]),
            state=state,
        )
        action("новости обновлены")

    print("\nОбновления:")
    if args.apply:
        upsert_message(
            updates_id, "updates",
            lambda: {
                **embed("🔄 Обновления сервера",
                        description=(
                            "Здесь публикуются релизы калькулятора и изменения Discord-сервера.\n\n"
                            "Как не пропускать:\n"
                            "• В #роли нажми **🔔 Обновления** — роль даст пинг на новые посты.\n\n"
                            "**Что уже готово:**\n"
                            "• роли, права и иерархия;\n"
                            "• закрытый Community после верификации;\n"
                            "• панель подписок и кнопка верификации;\n"
                            "• приветствие новичков и метки источника входа;\n"
                            "• AutoMod и лог модерации.\n\n"
                            "Первое обновление: запуск сервера."
                        ),
                        fields=[{"name": "С чего начать",
                                 "value": "Подпишись на роль 🔔 Обновления в #роли",
                                 "inline": False}]),
                **{"footer": {"text": MARKERS["updates"]}}
            },
            token=args.token, bot_id=bot_id,
            row=link_row([
                ("Скачать EXE", RELEASES_URL),
                ("Веб-версия", SITE_URL),
            ]),
            state=state,
        )
        action("обновления обновлены")
    else:
        print(f"  → создать/обновить «{MARKERS['updates']}» в #🔄｜обновления")

    print("\nУстановка:")
    if args.apply:
        upsert_message(
            install_id, "install",
            lambda: {
                **embed("📥 Установка калькулятора",
                        description=(
                            "**1. Веб-версия / Mini App**\n"
                            "Открой Telegram-бота @perekup5rp_bot и нажми «Открыть». Для ПК-приложения получи ключ в разделе «Ключ для ПК-приложения».\n\n"
                            "**2. EXE для Windows**\n"
                            "Скачай последний релис, запусти установщик и вставь ключ из Mini App в разделе «Привязка компьютера».\n\n"
                            "**3. Сайт**\n"
                            "Веб-версия доступна по кнопке ниже. Если что-то не работает — напиши в #помощь.\n\n"
                            "⚠️ Не пересылай ключ: до первого входа его может использовать любой."
                        ),
                        fields=[{"name": "Ключ для ПК",
                                 "value": "Получи в Mini App → Раздел «Ключ для ПК-приложения».",
                                 "inline": False}]),
                **{"footer": {"text": MARKERS["install"]}}
            },
            token=args.token, bot_id=bot_id,
            row=link_row([
                ("Скачать EXE", RELEASES_URL),
                ("Telegram-бот", TELEGRAM_URL),
                ("Открыть сайт", SITE_URL),
                ("Все релизы", "https://github.com/proctogad/perekup-webapp/releases"),
            ]),
            pin=True, state=state,
        )
        action("установка опубликована и закреплена")
    else:
        print(f"  → создать/обновить «{MARKERS['install']}» в #📥｜установка (закрепить)")

    print("\nМедиа-новости:")
    if args.apply:
        upsert_message(
            media_id, "media",
            lambda: {
                **embed("🎬 Медиа-новости",
                        description=(
                            "Здесь будут ролики, обзоры, стримы и подборки по перекупу.\n\n"
                            "Хочешь попасть в ленту? Выложи видео в #скрины-и-мемы или напиши владельцу.\n"
                            "Подписка на пинг — роль **📺 Медиа-новости** в #роли."
                        )),
                **{"footer": {"text": MARKERS["media"]}}
            },
            token=args.token, bot_id=bot_id,
            row=link_row([("Telegram-бот", TELEGRAM_URL)]),
            state=state,
        )
        action("медиа-новости обновлены")
    else:
        print(f"  → создать/обновить «{MARKERS['media']}» в #🎬｜медиа-новости")

    print("\nПриветствие (#welcom):")
    if args.apply:
        upsert_message(
            welcome_id, "welcome",
            lambda: {
                **embed("Добро пожаловать!",
                        description=(
                            "Ты зашёл на сервер **GTA5RP | Перекуп Калькулятор**.\n\n"
                            "Чтобы получить роль «👋 Новичок» и доступ к Community:\n"
                            "1. Прочитай правила в #правила.\n"
                            "2. Нажми кнопку **«Я прочитал правила»** — откроется Community.\n"
                            "3. Выбери подписки в #роли — получишь пинги под свои интересы."
                        ),
                        color=BRAND_COLOR),
                **{"footer": {"text": MARKERS["welcome"]}}
            },
            token=args.token, bot_id=bot_id,
            row=link_row([("Правила", f"https://discord.com/channels/{gid}/{rules_id}")]),
            state=state,
        )
        action("приветствие обновлено")
    else:
        print(f"  → создать/обновить «{MARKERS['welcome']}» в #👋｜welcom")

    print(f"\n{'✅ Посты обновлены.' if args.apply else 'это предпросмотр. Для применения добавь --apply'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())