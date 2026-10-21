"""Общий клиент Discord Bot API для скриптов DiscordCatalog.

Токен бота: переменная окружения DISCORD_BOT_TOKEN, файл `.env` рядом
с этим файлом или --token. Все обращения идут через api() — учитывает 429
(ждёт retry_after) и 5xx (до 5 попыток). resolve_guild() ищет сервер
по ID или по подстроке имени.
"""

from __future__ import annotations

import difflib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API = "https://discord.com/api/v10"

BASE_DIR = Path(__file__).resolve().parent

USER_AGENT = "DiscordCatalog/1.0 (+https://github.com/proctogad/perekup-webapp)"


class DiscordError(RuntimeError):
    """Ошибка обращения к Discord API."""


def error_hint(exc: Exception) -> str:
    """Короткая подсказка к типовым ошибкам Discord."""
    text = str(exc)
    if "HTTP 403" in text:
        return (" — у роли бота не включено «Управление ролями» или на сервере "
                "включена 2FA для модерации (подробно в SETUP.md)")
    if "HTTP 401" in text:
        return " — неверный токен"
    if "HTTP 429" in text:
        return " — слишком много запросов, подожди пару минут и повтори"
    return ""


def enable_utf8_console() -> None:
    """Windows-консоль по умолчанию cp1251 — включаем UTF-8, чтобы эмодзи не падали."""
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", "") or "").replace("-", "").lower()
        if encoding != "utf8":
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass


def load_env(path: Path | None = None) -> None:
    """Прочитать `.env` рядом со скриптами, не перезаписывая уже заданные переменные.

    Формат простой: `KEY=VALUE`, строки с `#` — комментарии. Кавычки вокруг
    значения снимаются. Ошибок не бросает: если файла нет — просто пропускает.
    """
    env_path = path or (BASE_DIR / ".env")
    try:
        text = env_path.read_text(encoding="utf-8-sig")
    except OSError:
        return

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def api(
    method: str,
    path: str,
    token: str,
    params: str = "",
    payload: dict | list | None = None,
) -> Any:
    """Один запрос к Discord API с обработкой 429 и 5xx.

    payload передаётся как JSON-тело (POST/PATCH/PUT).
    """
    url = f"{API}{path}{params}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bot {token}", "User-Agent": USER_AGENT}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method.upper())

    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", "replace")
            if exc.code == 429:
                try:
                    delay = json.loads(err_body).get("retry_after", 1.0)
                except json.JSONDecodeError:
                    delay = 1.0
                wait = float(delay) + 0.25
                print(f"  rate limit, ждём {wait:.2f} с", file=sys.stderr)
                time.sleep(wait)
                continue
            if exc.code in (500, 502, 503, 504) and attempt < 4:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise DiscordError(
                f"HTTP {exc.code} {exc.reason} — {method.upper()} {path}: {err_body}"
            ) from exc
        except urllib.error.URLError as exc:
            if attempt < 4:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise DiscordError(f"сеть недоступна: {exc.reason}") from exc

    raise DiscordError(f"не удалось выполнить {method.upper()} {path} после 5 попыток")


def _norm(text: str) -> str:
    """Привести к нижнему регистру и убрать лишние пробелы."""
    return " ".join(text.casefold().split())


def _key(text: str) -> str:
    """Ключ для сравнения: без знаков препинания и эмодзи."""
    cleaned = "".join(ch if ch.isalnum() else " " for ch in _norm(text))
    return " ".join(cleaned.split())


def find_guilds(guilds: list[dict], guild_name: str) -> list[dict]:
    """Найти серверы по куску имени: прямое совпадение, потом похожие.

    Сравнение не зависит от регистра, кириллицы/латиницы и эмодзи —
    «ГТА5РП | Перекуп Калькулятор» найдёт «GTA5RP | Перекуп Калькулятор».
    """
    needle = _norm(guild_name)
    if not needle:
        return []

    direct = [g for g in guilds if needle in _norm(g["name"])]
    if direct:
        return direct

    key_needle = _key(guild_name)
    if not key_needle:
        return []
    normalized = [g for g in guilds if key_needle in _key(g["name"])]
    if normalized:
        return normalized

    similar = difflib.get_close_matches(key_needle, [_key(g["name"]) for g in guilds], n=3, cutoff=0.6)
    return [g for key in similar for g in guilds if _key(g["name"]) == key]


def resolve_guild(token: str, guild_id: str | None, guild_name: str | None) -> dict:
    """Найти сервер: по ID напрямую либо поиском среди серверов бота."""
    if guild_id:
        return api("GET", f"/guilds/{guild_id}", token)

    guilds = api("GET", "/users/@me/guilds", token, "?with_counts=true")
    matches = find_guilds(guilds, guild_name or "")
    if not matches:
        available = "\n  ".join(f"{g['name']} — id {g['id']}" for g in guilds[:40]) or "(нет общих серверов)"
        raise DiscordError(
            f"сервер «{guild_name}» не найден. Доступные боту серверы:\n  {available}\n"
            f"Подсказка: хватит куска имени (например, «Перекуп») или укажите --guild-id."
        )
    if _norm(matches[0]["name"]) != _norm(guild_name or ""):
        print(f"  точного совпадения нет, беру похожий сервер: {matches[0]['name']}", file=sys.stderr)
    if len(matches) > 1:
        print(f"  найдено {len(matches)} совпадений, беру первое: {matches[0]['name']}", file=sys.stderr)
    return matches[0]
