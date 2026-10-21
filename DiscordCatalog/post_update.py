"""Вебхук для канала обновлений и посты о релизах.

Что делает:
- создаёт (или переиспользует) вебхук `Perekup Updates` в канале обновлений;
- сохраняет URL в `DiscordCatalog/.env` как `DISCORD_WEBHOOK` (в чат не печатает);
- умеет отправить тестовый пост и сразу удалить его;
- умеет отправить красивый анонс релиза.

Запуск:
    python post_update.py --guild-name "Перекуп Калькулятор" --create-webhook            # предпросмотр
    python post_update.py --guild-name "Перекуп Калькулятор" --create-webhook --apply    # создать
    python post_update.py --test --apply                                                 # проверить
    python post_update.py --title "Версия 1.4" --version 1.4 --url https://... --notes "Что нового"

Токен: DISCORD_BOT_TOKEN или `.env`. Для создания вебхука нужно право
«Управление вебхуками» (Manage Webhooks) в канале.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from discord_api import DiscordError, api, enable_utf8_console, error_hint, load_env, resolve_guild

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
WEBHOOK_NAME = "Perekup Updates"
DEFAULT_CHANNEL = "обновления"
USER_AGENT = "PerekupDisBot/1.0"


def norm(text: str) -> str:
    return " ".join(str(text).casefold().replace("#", " ").split())


def find_channel(channels: list[dict], query: str) -> dict | None:
    wanted = norm(query)
    text_channels = [c for c in channels if c["type"] in (0, 5)]
    exact = next((c for c in text_channels if norm(c["name"]) == wanted), None)
    if exact is not None:
        return exact
    if str(query).isdigit():
        return next((c for c in text_channels if c["id"] == str(query)), None)
    return next((c for c in text_channels if wanted in norm(c["name"])), None)


def mask_webhook(url: str) -> str:
    """Показать ID вебхука, но скрыть токен."""
    marker = "/api/webhooks/"
    if marker not in url:
        return "(вебхук)"
    rest = url.split(marker, 1)[1]
    webhook_id = rest.split("/", 1)[0]
    return f"https://discord.com/api/webhooks/{webhook_id}/••••"


def read_env_value(path: Path, key: str) -> str:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return ""
    prefix = f"{key}="
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix):
            value = stripped[len(prefix):].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value
    return ""


def save_env_value(path: Path, key: str, value: str) -> None:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        lines = []
    prefix = f"{key}="
    replaced = False
    for index, line in enumerate(lines):
        if line.strip().startswith(prefix):
            lines[index] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def webhook_request(method: str, url: str, payload: dict | None = None) -> dict | None:
    """Запрос к вебхуку (у него своя авторизация — токен в URL)."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"User-Agent": USER_AGENT}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method.upper())

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", "replace")
            if exc.code == 429:
                try:
                    delay = float(json.loads(err_body).get("retry_after", 1.0))
                except (json.JSONDecodeError, TypeError, ValueError):
                    delay = 1.0
                time.sleep(delay + 0.25)
                continue
            raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {err_body}") from exc
        except urllib.error.URLError as exc:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"сеть недоступна: {exc.reason}") from exc
    raise RuntimeError("не удалось выполнить запрос к вебхуку")


def webhook_embed(args: argparse.Namespace) -> dict:
    title = args.title.strip()
    if not title.startswith("🚀"):
        title = f"🚀 {title}"
    description = (args.notes or "").strip() or "Новая версия уже доступна."
    embed: dict = {
        "title": title[:256],
        "description": description[:4096],
        "color": int(args.color.lstrip("#"), 16),
        "footer": {"text": "Perekup Updates"},
    }
    if args.url:
        embed["url"] = args.url
    fields = []
    if args.version:
        fields.append({"name": "Версия", "value": args.version[:1024], "inline": True})
    if args.url:
        fields.append({"name": "Ссылка", "value": args.url[:1024], "inline": True})
    if fields:
        embed["fields"] = fields
    return embed


def main() -> int:
    enable_utf8_console()
    load_env()

    ap = argparse.ArgumentParser(description="Вебхук обновлений и посты о релизах")
    ap.add_argument("--guild-id", help="ID сервера")
    ap.add_argument("--guild-name", help="имя сервера (поиск по подстроке)")
    ap.add_argument("--token", default=os.environ.get("DISCORD_BOT_TOKEN", ""), help="токен бота")
    ap.add_argument("--channel", default=DEFAULT_CHANNEL, help="канал обновлений (по умолчанию «обновления»)")
    ap.add_argument("--webhook", help="готовый URL вебхука (иначе из .env)")
    ap.add_argument("--create-webhook", action="store_true", help="создать вебхук и сохранить URL в .env")
    ap.add_argument("--test", action="store_true", help="отправить и удалить тестовый пост")
    ap.add_argument("--title", help="заголовок анонса")
    ap.add_argument("--notes", help="текст анонса")
    ap.add_argument("--version", help="версия (необязательно)")
    ap.add_argument("--url", help="ссылка на релиз (необязательно)")
    ap.add_argument("--color", default="#F39C12", help="цвет embed, например #F39C12")
    ap.add_argument("--apply", action="store_true", help="выполнить действие (без флага — предпросмотр)")
    args = ap.parse_args()

    if args.create_webhook and not args.token:
        ap.error("нет токена: задайте DISCORD_BOT_TOKEN в .env или --token")
    if not args.create_webhook and not args.test and not args.title:
        ap.error("укажи действие: --create-webhook, --test или --title")
    if args.create_webhook and not args.guild_id and not args.guild_name:
        ap.error("для --create-webhook нужен --guild-id или --guild-name")
    try:
        args.color = f"#{int(args.color.lstrip('#'), 16):06X}"
    except ValueError:
        ap.error("--color должен быть HEX-цветом, например #F39C12")

    webhook_url = (args.webhook or os.environ.get("DISCORD_WEBHOOK") or read_env_value(ENV_FILE, "DISCORD_WEBHOOK")).strip()

    # 1. Создание/проверка вебхука.
    if args.create_webhook:
        try:
            print("ищу сервер…", file=sys.stderr)
            guild = resolve_guild(args.token, args.guild_id, args.guild_name)
            print(f"сервер: {guild['name']} ({guild['id']})", file=sys.stderr)
            channels = api("GET", f"/guilds/{guild['id']}/channels", args.token)
        except DiscordError as exc:
            print(f"ОШИБКА: {exc}{error_hint(exc)}", file=sys.stderr)
            return 1

        channel = find_channel(channels, args.channel)
        if channel is None:
            print(f"ОШИБКА: канал «{args.channel}» не найден", file=sys.stderr)
            return 1
        print(f"канал: #{channel['name']} ({channel['id']})")

        if webhook_url:
            try:
                webhook_request("GET", webhook_url)
                print(f"  = вебхук уже работает: {mask_webhook(webhook_url)}")
                if args.apply and not read_env_value(ENV_FILE, "DISCORD_WEBHOOK"):
                    save_env_value(ENV_FILE, "DISCORD_WEBHOOK", webhook_url)
                    print(f"  ✓ URL сохранён в {ENV_FILE}")
            except RuntimeError as exc:
                print(f"  ! старый вебхук не отвечает ({exc}) — создам новый")
                webhook_url = ""

        if not webhook_url:
            try:
                hooks = api("GET", f"/channels/{channel['id']}/webhooks", args.token)
            except DiscordError as exc:
                print(f"ОШИБКА: не прочитать вебхуки канала: {exc}{error_hint(exc)}", file=sys.stderr)
                return 1

            named = [hook for hook in hooks if hook.get("name") == WEBHOOK_NAME]
            reusable = next((hook for hook in named if hook.get("token")), None)
            if reusable is not None:
                webhook_url = f"https://discord.com/api/webhooks/{reusable['id']}/{reusable['token']}"
                print(f"  = найден готовый вебхук: {mask_webhook(webhook_url)}")
            else:
                if named and not args.apply:
                    print(f"  → удалить старый вебхук без токена ({len(named)} шт.) и создать новый")
                if not args.apply:
                    print(f"  → создать вебхук «{WEBHOOK_NAME}» в #{channel['name']}")
                else:
                    for hook in named:
                        try:
                            api("DELETE", f"/webhooks/{hook['id']}", args.token)
                            print(f"  ✓ удалён старый вебхук {hook['id']}")
                        except DiscordError as exc:
                            print(f"  ! не удалось удалить {hook['id']}: {exc}{error_hint(exc)}", file=sys.stderr)
                    try:
                        created = api(
                            "POST",
                            f"/channels/{channel['id']}/webhooks",
                            args.token,
                            payload={"name": WEBHOOK_NAME},
                        )
                    except DiscordError as exc:
                        print(f"ОШИБКА: не создать вебхук: {exc}{error_hint(exc)}", file=sys.stderr)
                        return 1
                    webhook_url = f"https://discord.com/api/webhooks/{created['id']}/{created['token']}"
                    print(f"  ✓ создан вебхук: {mask_webhook(webhook_url)}")

            if webhook_url and args.apply:
                save_env_value(ENV_FILE, "DISCORD_WEBHOOK", webhook_url)
                print(f"  ✓ URL сохранён в {ENV_FILE}")

        if not args.apply:
            print("\nпредпросмотр: добавьте --apply, чтобы создать вебхук")
            return 0

    if not webhook_url:
        print(
            "ОШИБКА: нет вебхука. Сначала выполни --create-webhook --apply "
            "или задай DISCORD_WEBHOOK в .env.",
            file=sys.stderr,
        )
        return 1

    # 2. Тестовый пост — отправляем и сразу удаляем.
    if args.test:
        print("отправляю тестовый пост…")
        try:
            message = webhook_request(
                "POST",
                f"{webhook_url}?wait=true",
                {
                    "username": WEBHOOK_NAME,
                    "embeds": [
                        {
                            "title": "🔔 Проверка вебхука",
                            "description": "Если ты видишь это сообщение — вебхук работает. Сейчас удалю его.",
                            "color": int(args.color.lstrip("#"), 16),
                        }
                    ],
                },
            )
        except RuntimeError as exc:
            print(f"ОШИБКА: {exc}", file=sys.stderr)
            return 1
        message_id = (message or {}).get("id")
        if message_id:
            try:
                webhook_request("DELETE", f"{webhook_url}/messages/{message_id}")
                print(f"✓ тест отправлен и удалён (id {message_id})")
            except RuntimeError as exc:
                print(f"! тест отправлен, но не удалён: {exc} (id {message_id})", file=sys.stderr)
        else:
            print("✓ тест отправлен (ответ без id — проверь канал)")
        return 0

    # 3. Анонс релиза.
    if args.title:
        if not args.apply:
            print("\nпредпросмотр анонса:")
            print(json.dumps(webhook_embed(args), ensure_ascii=False, indent=2))
            print("отправить: добавьте --apply")
            return 0
        print("отправляю анонс…")
        try:
            webhook_request(
                "POST",
                f"{webhook_url}?wait=true",
                {"username": WEBHOOK_NAME, "embeds": [webhook_embed(args)]},
            )
        except RuntimeError as exc:
            print(f"ОШИБКА: {exc}", file=sys.stderr)
            return 1
        print("✓ анонс отправлен в канал обновлений")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
