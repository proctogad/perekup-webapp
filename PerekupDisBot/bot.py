"""Discord-бот сервера «GTA5RP | Перекуп Калькулятор».

Что делает:
- панель подписок в канале «роли»: 14 кнопок, роль выдаётся/снимается нажатием;
- панель верификации в «правила»: кнопка выдаёт `✅ Знает правила`;
- при входе вешает `👋 Новичок`, приветствует в «welcom» и определяет,
  по какой инвайт-ссылке пришёл человек (Telegram / EXE / сайт);
- слэш-команды `/panel`, `/verify-panel`, `/status` для админов.

Запуск:
    python bot.py

Настройки — в config.json, токен — в .env (см. .env.example).
Подробная инструкция и деплой — в README.md.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import discord
from discord import app_commands

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
DEFAULT_STATE_FILE = BASE_DIR / "state.json"
DEFAULT_INVITES_FILE = BASE_DIR / "invites.json"

BRAND_COLOR = 0x5865F2
PANEL_MARKER = "Perekup • панель подписок"
VERIFY_MARKER = "Perekup • верификация"

log = logging.getLogger("perekup")


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def enable_utf8_console() -> None:
    """Windows-консоль по умолчанию cp1251 — переключаем на UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", "") or "").replace("-", "").lower()
        if encoding != "utf8":
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass


def load_env(path: Path) -> None:
    """Прочитать .env, не перезаписывая уже заданные переменные окружения."""
    try:
        text = path.read_text(encoding="utf-8-sig")
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


def norm(text: str) -> str:
    """Нормализовать имя канала/роли для сравнения."""
    return " ".join(str(text).casefold().replace("#", " ").split())


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError) as exc:
        log.error("не удалось прочитать %s: %s", path, exc)
        return default


def write_json_atomic(path: Path, data: Any) -> None:
    """Записать JSON через временный файл, чтобы не оставить половину."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.warning("не удалось сохранить %s: %s", path, exc)


def load_config() -> dict:
    config = read_json(CONFIG_FILE, None)
    if not isinstance(config, dict):
        raise SystemExit(f"нет или повреждён {CONFIG_FILE} — проверь JSON")
    subscriptions = config.get("subscriptions") or []
    if not isinstance(subscriptions, list) or not subscriptions:
        raise SystemExit("в config.json нет списка subscriptions")
    if len(subscriptions) > 25:
        raise SystemExit("в config.json больше 25 подписок — Discord не покажет столько кнопок")
    keys = [str(item.get("key", "")) for item in subscriptions]
    if any(not key for key in keys) or len(set(keys)) != len(keys):
        raise SystemExit("у каждой подписки должен быть уникальный key")
    for item in subscriptions:
        if not item.get("role") or not item.get("label"):
            raise SystemExit("у каждой подписки должны быть поля role и label")
    return config


def human_uptime(seconds: float) -> str:
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    if days:
        return f"{days} д {hours} ч {minutes} мин"
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


# ---------------------------------------------------------------------------
# Кнопки и панели
# ---------------------------------------------------------------------------


class SubscriptionButton(discord.ui.Button):
    def __init__(self, bot: "PerekupBot", entry: dict, row: int) -> None:
        super().__init__(
            label=entry["label"],
            emoji=entry.get("emoji") or None,
            style=discord.ButtonStyle.secondary,
            custom_id=f"sub:{entry['key']}",
            row=row,
        )
        self.bot = bot
        self.entry = entry

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Кнопка работает только на сервере.", ephemeral=True)
            return

        role = self.bot.resolve_role(self.entry["role"])
        if role is None:
            await interaction.response.send_message(
                f"Роль **{self.entry['role']}** не найдена. Напиши администратору.", ephemeral=True
            )
            return

        member = interaction.user
        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Самоназначение подписки")
                text = f"🔕 Подписка снята: **{role.name}**"
            else:
                await member.add_roles(role, reason="Самоназначение подписки")
                text = f"🔔 Подписка включена: **{role.name}**"
        except discord.Forbidden:
            log.exception("не удалось изменить роли участника %s", member.id)
            text = (
                "Не получилось выдать роль. Проверь, что роль бота стоит выше выбранной "
                "и у бота есть право «Управление ролями»."
            )

        await interaction.response.send_message(text, ephemeral=True)


class SubscriptionView(discord.ui.View):
    def __init__(self, bot: "PerekupBot") -> None:
        super().__init__(timeout=None)
        for index, entry in enumerate(bot.config["subscriptions"]):
            self.add_item(SubscriptionButton(bot, entry, row=index // 5))


class VerificationButton(discord.ui.Button):
    def __init__(self, bot: "PerekupBot") -> None:
        super().__init__(
            label="Я прочитал правила",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id="verify:rules",
        )
        self.bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Кнопка работает только на сервере.", ephemeral=True)
            return

        role = self.bot.resolve_role(self.bot.config["roles"].get("verified"))
        novice = self.bot.resolve_role(self.bot.config["roles"].get("unverified"))
        if role is None:
            await interaction.response.send_message(
                "Роль «✅ Знает правила» не найдена. Напиши администратору.", ephemeral=True
            )
            return

        member = interaction.user
        if role in member.roles:
            await interaction.response.send_message(
                "Ты уже подтвердил правила — доступ к Community открыт. "
                "Подписки на пинги можно выбрать в канале «роли».",
                ephemeral=True,
            )
            return

        try:
            await member.add_roles(role, reason="Верификация: прочитал правила")
        except discord.Forbidden:
            log.exception("не удалось выдать роль верификации %s", member.id)
            await interaction.response.send_message(
                "Не получилось выдать роль. Проверь права бота и высоту его роли.", ephemeral=True
            )
            return

        if novice is not None and novice in member.roles:
            try:
                await member.remove_roles(novice, reason="Верификация пройдена")
            except discord.Forbidden:
                log.warning("не удалось снять роль «Новичок» у %s", member.id)

        await interaction.response.send_message(
            "✅ Готово! Доступ к Community открыт.\n"
            "Загляни в **#чат**, **#отзывы**, **#идеи**, **#помощь** и **#баги**. "
            "А в канале **«роли»** можно включить пинги под свои интересы.",
            ephemeral=True,
        )


class VerificationView(discord.ui.View):
    def __init__(self, bot: "PerekupBot") -> None:
        super().__init__(timeout=None)
        self.add_item(VerificationButton(bot))


# ---------------------------------------------------------------------------
# Бот
# ---------------------------------------------------------------------------


class PerekupBot(discord.Client):
    def __init__(self, config: dict, state: dict) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = os.environ.get("MEMBERS_INTENT", "true").strip().lower() not in {"0", "false", "no"}
        super().__init__(intents=intents)

        self.config = config
        self.state = state
        self.tree = app_commands.CommandTree(self)
        self.guild_id = int(os.environ.get("DISCORD_GUILD_ID") or config.get("guild_id") or 0)
        self.state_file = Path(os.environ.get("STATE_FILE", DEFAULT_STATE_FILE))
        self.invites_file = Path(os.environ.get("INVITES_FILE", DEFAULT_INVITES_FILE))
        self.invite_sources: dict[str, dict] = {}
        self.invite_cache: dict[str, int] = {}
        self.temp_voice_channels: set[int] = set()
        raw = self.state.get("temp_voice_channels") or []
        if isinstance(raw, list):
            self.temp_voice_channels = {int(x) for x in raw if str(x).isdigit()}
        self.started_at = time.monotonic()
        self.synced = False
        self.publish_only = False

    # --- инфраструктура -------------------------------------------------

    @property
    def guild(self) -> discord.Guild | None:
        return self.get_guild(self.guild_id) if self.guild_id else None

    def resolve_role(self, name: str | None) -> discord.Role | None:
        if not name:
            return None
        guild = self.guild
        if guild is None:
            return None
        if str(name).isdigit():
            return guild.get_role(int(name))
        wanted = norm(name)
        return next((role for role in guild.roles if norm(role.name) == wanted), None)

    def resolve_channel(self, key: str) -> discord.abc.GuildChannel | None:
        value = (self.config.get("channels") or {}).get(key)
        if not value:
            return None
        guild = self.guild
        if guild is None:
            return None
        if str(value).isdigit():
            return guild.get_channel(int(value))
        wanted = norm(value)
        return next((channel for channel in guild.channels if norm(channel.name) == wanted), None)

    def set_panel(self, name: str, channel_id: int, message_id: int) -> None:
        panels = self.state.setdefault("panels", {})
        panels[name] = {
            "channel_id": str(channel_id),
            "message_id": str(message_id),
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        write_json_atomic(self.state_file, self.state)

    def load_invite_sources(self) -> None:
        raw = os.environ.get("INVITES_JSON", "").strip()
        if raw:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                log.error("INVITES_JSON: не разобрать JSON (%s)", exc)
                data = {}
        else:
            data = read_json(self.invites_file, {})
        invites = data.get("invites", []) if isinstance(data, dict) else []
        self.invite_sources = {
            str(item["code"]): item
            for item in invites
            if isinstance(item, dict) and item.get("code")
        }
        if self.invite_sources:
            log.info("источники входа: %s", ", ".join(item.get("label", code) for code, item in self.invite_sources.items()))
        else:
            log.info("invites.json не найден или пуст — метки источника входа не выдаются")

    async def refresh_invite_cache(self, guild: discord.Guild) -> None:
        try:
            invites = await guild.invites()
        except discord.Forbidden:
            log.warning("нет права «Управление сервером» — трекинг инвайтов выключен")
            return
        except discord.HTTPException:
            log.exception("не удалось получить инвайты")
            return
        self.invite_cache = {invite.code: invite.uses for invite in invites}
        log.info("кэш инвайтов: %d", len(self.invite_cache))

    async def setup_hook(self) -> None:
        self.add_view(SubscriptionView(self))
        self.add_view(VerificationView(self))
        self.load_invite_sources()

        # Команды-методы не попадают в дерево сами: добавляем их явно.
        for command in (self.panel_command, self.verify_panel_command, self.status_command):
            if command.name not in {item.name for item in self.tree.get_commands()}:
                self.tree.add_command(command)

        try:
            if self.guild_id:
                guild = discord.Object(id=self.guild_id)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            else:
                await self.tree.sync()
            self.synced = True
            log.info("слэш-команды синхронизированы")
        except discord.HTTPException:
            log.exception("не удалось синхронизировать слэш-команды")

    async def on_ready(self) -> None:
        log.info("вошёл как %s (%s)", self.user, getattr(self.user, "id", "?"))
        guild = self.guild
        if guild is None:
            log.error(
                "сервер с ID %s не найден. Проверь config.json → guild_id / DISCORD_GUILD_ID "
                "и что бот добавлен на сервер.",
                self.guild_id or "(не задан)",
            )
            if self.publish_only:
                await self.close()
            return

        if self.publish_only:
            for kind, title in (("subscriptions", "подписки"), ("verification", "верификация")):
                try:
                    message, action = await self.ensure_panel(kind)
                except RuntimeError as exc:
                    log.error("%s: %s", title, exc)
                else:
                    log.info("панель %s %s: %s", title, action, message.jump_url)
            await self.close()
            return

        await self.refresh_invite_cache(guild)
        log.info("сервер: %s · участников: %s", guild.name, guild.member_count or "—")
        if not self.intents.members:
            log.warning(
                "Server Members Intent выключен: приветствие, роль «Новичок» и источник входа "
                "работать не будут. Включи его в Discord Developer Portal → Bot → Privileged "
                "Gateway Intents."
            )

    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        guild = member.guild

        novice = self.resolve_role(self.config["roles"].get("unverified"))
        if novice is not None:
            try:
                await member.add_roles(novice, reason="Новый участник")
            except discord.Forbidden:
                log.warning("не удалось выдать роль «Новичок» участнику %s", member.id)

        source = await self.detect_source(guild)
        source_role = self.resolve_role(source.get("role")) if source else None
        if source_role is not None:
            try:
                await member.add_roles(source_role, reason="Источник входа по инвайту")
            except discord.Forbidden:
                log.warning("не удалось выдать роль источника %s участнику %s", source_role.name, member.id)

        channel = self.resolve_channel("welcome")
        if channel is None or not isinstance(channel, discord.TextChannel):
            log.warning("канал приветствия не найден — пропускаю приветствие")
            return

        rules = self.resolve_channel("rules")
        roles_channel = self.resolve_channel("roles")
        rules_text = rules.mention if rules is not None else "**#правила**"
        roles_text = roles_channel.mention if roles_channel is not None else "**#роли**"

        embed = discord.Embed(
            title=f"Добро пожаловать, {member.display_name}!",
            description=(
                f"Ты на сервере **{guild.name}**. Здесь считают перекуп, "
                "делятся скинами и собираются на движухи."
                + (f"\n\nОткуда пришёл: **{source.get('label')}**." if source and source.get("label") else "")
            ),
            color=BRAND_COLOR,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(
            name="1. Прочитай правила",
            value=f"Коротко и по делу — в {rules_text}.",
            inline=False,
        )
        embed.add_field(
            name="2. Подтверди согласие",
            value="Нажми кнопку **«Я прочитал правила»** в канале правил — откроется Community.",
            inline=False,
        )
        embed.add_field(
            name="3. Выбери подписки",
            value=f"В {roles_text} можно включить пинги: новости, обновления, мероприятия, войс и другое.",
            inline=False,
        )
        embed.set_footer(text=f"{guild.name} · участник #{guild.member_count or '—'}")

        try:
            await channel.send(
                content=member.mention,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        except discord.Forbidden:
            log.warning("не удалось отправить приветствие в #%s", channel.name)

    async def detect_source(self, guild: discord.Guild) -> dict | None:
        """Найти инвайт, по которому пришёл участник, и вернуть его источник."""
        try:
            invites = await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            return None

        used: list[tuple[int, discord.Invite]] = []
        for invite in invites:
            before = self.invite_cache.get(invite.code)
            if before is not None and invite.uses > before:
                used.append((invite.uses - before, invite))

        self.invite_cache = {invite.code: invite.uses for invite in invites}
        if not used:
            return {"key": None, "label": "обычная ссылка", "role": None}

        used.sort(key=lambda pair: pair[0], reverse=True)
        invite = used[0][1]
        entry = self.invite_sources.get(invite.code)
        if entry is None:
            log.info("инвайт %s не размечен в invites.json", invite.code)
            return {"key": None, "label": "обычная ссылка", "role": None, "code": invite.code}
        return entry

    async def on_invite_create(self, invite: discord.Invite) -> None:
        self.invite_cache[invite.code] = invite.uses

    async def on_invite_delete(self, invite: discord.Invite) -> None:
        self.invite_cache.pop(invite.code, None)

    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        if member.bot:
            return
        guild = member.guild
        hub = self.resolve_channel("voice_hub")
        if hub is not None and after.channel is not None and after.channel.id == hub.id:
            category = self.resolve_channel("voice_temp_category")
            if not isinstance(category, discord.CategoryChannel):
                log.warning("категория временных голосовых каналов не найдена")
                return
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=True),
                member: discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True, move_members=True),
            }
            verified = self.resolve_role(self.config["roles"].get("verified"))
            if verified is not None:
                overwrites[verified] = discord.PermissionOverwrite(view_channel=True, connect=True)
            name = f"🔊・{member.display_name}"[:100]
            try:
                channel = await guild.create_voice_channel(
                    name=name,
                    category=category,
                    overwrites=overwrites,
                    reason=f"Временная комната для {member}",
                )
                self.temp_voice_channels.add(channel.id)
                self.save_state()
                await member.move_to(channel, reason="Создал временную комнату")
            except discord.HTTPException:
                log.exception("не удалось создать временную комнату для %s", member)
        if before.channel is not None and before.channel.id in self.temp_voice_channels:
            channel = before.channel
            if not channel.members:
                try:
                    await channel.delete(reason="Временная комната пуста")
                except discord.HTTPException:
                    log.warning("не удалось удалить пустую комнату %s", channel.name)
                self.temp_voice_channels.discard(channel.id)
                self.save_state()

    async def on_member_remove(self, member: discord.Member) -> None:
        if member.bot:
            return
        guild = member.guild
        joined = member.joined_at
        duration = "—"
        if joined is not None:
            delta = datetime.now(timezone.utc) - joined
            days, seconds = delta.days, delta.seconds
            duration = f"{days} д {seconds // 3600} ч {(seconds % 3600) // 60} мин"
        roles = ", ".join(role.name for role in member.roles if not role.is_default()) or "—"
        await self.send_modlog(
            "👋 Участник вышел",
            f"{member.mention} ({member.id})\n"
            f"Присоединился: {joined}\n"
            f"Длительность в сервере: {duration}\n"
            f"Роли: {roles}",
            color=0xE74C3C,
        )

    async def on_message_delete(self, message: discord.Message) -> None:
        if message.author is None or message.author.bot or message.guild is None:
            return
        channel = message.channel
        if not isinstance(channel, discord.TextChannel):
            return
        content = message.content or ""
        if not content and not message.attachments:
            return
        attachments = ", ".join(att.filename for att in message.attachments) or "—"
        await self.send_modlog(
            "🗑️ Удалено сообщение",
            f"в **#{channel.name}** от {message.author.mention}",
            color=0x95A5A6,
            fields=[
                ("Автор", f"{message.author} (`{message.author.id}`)", True),
                ("Содержание", content[:1000] or "—", False),
                ("Файлы", attachments, True),
            ],
        )

    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.author is None or before.author.bot or before.guild is None:
            return
        if not isinstance(after.channel, discord.TextChannel):
            return
        if before.content == after.content:
            return
        await self.send_modlog(
            "✏️ Отредактировано сообщение",
            f"в **#{after.channel.name}** от {after.author.mention}",
            color=0xF1C40F,
            fields=[
                ("Было", before.content[:500] or "—", False),
                ("Стало", after.content[:500] or "—", False),
            ],
        )

    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.timed_out_until != after.timed_out_until:
            if after.timed_out_until is None:
                await self.send_modlog(
                    "🔓 Таймаут снят",
                    f"с {after.mention}",
                    color=0x2ECC71,
                )
            else:
                await self.send_modlog(
                    "🔒 Таймаут наложен",
                    f"на {after.mention} до {after.timed_out_until:%d.%m.%Y %H:%M}",
                    color=0xE67E22,
                )

    async def on_error(self, event_method: str, /, *args: Any, **kwargs: Any) -> None:
        log.exception("ошибка в обработчике %s", event_method)

    # --- команды --------------------------------------------------------

    def is_admin(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        perms = interaction.user.guild_permissions
        return perms.administrator or perms.manage_guild

    async def panel_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🔔 Подписки на пинги",
            description=(
                "Выбирай, о чём хочешь получать уведомления. Нажми на кнопку — "
                "роль появится, нажми ещё раз — снимется.\n\n"
                "Это только пинги: доступ к каналам подписки не меняют."
            ),
            color=BRAND_COLOR,
        )
        groups: dict[str, list[dict]] = {}
        for entry in self.config["subscriptions"]:
            groups.setdefault(entry.get("group", "Подписки"), []).append(entry)
        for group, entries in groups.items():
            value = " · ".join(f"{entry.get('emoji', '')} {entry['label']}".strip() for entry in entries)
            embed.add_field(name=group, value=value[:1024], inline=False)
        embed.set_footer(text=PANEL_MARKER)
        return embed

    def verify_embed(self) -> discord.Embed:
        rules = self.resolve_channel("rules")
        rules_text = rules.mention if rules is not None else "**#правила**"
        return discord.Embed(
            title="✅ Доступ к серверу",
            description=(
                f"Прочитай правила в {rules_text}. Если согласен — нажми кнопку ниже.\n\n"
                "После этого откроются каналы Community: отзывы, идеи, помощь, чат, "
                "скины и мемы, баги. Подписки на пинги выбираются отдельно в канале **«роли»**."
            ),
            color=0x2ECC71,
        ).set_footer(text=VERIFY_MARKER)

    async def find_panel_message(
        self, channel: discord.TextChannel, marker: str
    ) -> discord.Message | None:
        try:
            async for message in channel.history(limit=50):
                if message.author.id != self.user.id or not message.embeds:
                    continue
                footer = message.embeds[0].footer.text or ""
                if marker in footer:
                    return message
        except (discord.Forbidden, discord.HTTPException):
            return None
        return None

    async def ensure_panel(self, kind: str) -> tuple[discord.Message, str]:
        """Опубликовать или обновить панель; вернуть сообщение и действие."""
        if kind == "subscriptions":
            channel_key = "roles"
            marker = PANEL_MARKER
            embed = await self.panel_embed()
            view = SubscriptionView(self)
            missing_text = (
                "Не нашёл канал **«роли»**. Запусти `python finish_setup.py --apply` "
                "в папке DiscordCatalog или создай канал и укажи его ID в config.json → channels.roles."
            )
        else:
            channel_key = "rules"
            marker = VERIFY_MARKER
            embed = self.verify_embed()
            view = VerificationView(self)
            missing_text = (
                "Не нашёл канал **«правила»**. Проверь config.json → channels.rules "
                "(можно указать ID канала)."
            )

        channel = self.resolve_channel(channel_key)
        if channel is None or not isinstance(channel, discord.TextChannel):
            raise RuntimeError(missing_text)

        old = (self.state.get("panels") or {}).get(kind) or {}
        message: discord.Message | None = None
        if str(old.get("channel_id")) == str(channel.id) and old.get("message_id"):
            try:
                message = await channel.fetch_message(int(old["message_id"]))
            except (discord.NotFound, discord.Forbidden, ValueError):
                message = None
        if message is None:
            message = await self.find_panel_message(channel, marker)

        try:
            if message is not None:
                await message.edit(embed=embed, view=view)
                action = "обновлена"
            else:
                message = await channel.send(embed=embed, view=view)
                action = "опубликована"
        except discord.Forbidden as exc:
            raise RuntimeError(
                f"Не хватает прав, чтобы писать в {channel.mention}. "
                "Дай боту «Отправлять сообщения» и «Встраивать ссылки»."
            ) from exc
        except discord.HTTPException as exc:
            raise RuntimeError(f"Discord не дал опубликовать панель: {exc}") from exc

        self.set_panel(kind, channel.id, message.id)
        return message, action

    async def publish_panel(self, interaction: discord.Interaction, kind: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            message, action = await self.ensure_panel(kind)
        except RuntimeError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send(f"Панель {action}: {message.jump_url}", ephemeral=True)

    @app_commands.command(name="panel", description="Опубликовать или обновить панель подписок")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def panel_command(self, interaction: discord.Interaction) -> None:
        if not self.is_admin(interaction):
            await interaction.response.send_message("Нужны права «Управление сервером».", ephemeral=True)
            return
        await self.publish_panel(interaction, "subscriptions")

    @app_commands.command(name="verify-panel", description="Опубликовать панель верификации в правилах")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def verify_panel_command(self, interaction: discord.Interaction) -> None:
        if not self.is_admin(interaction):
            await interaction.response.send_message("Нужны права «Управление сервером».", ephemeral=True)
            return
        await self.publish_panel(interaction, "verification")

    @app_commands.command(name="status", description="Показать состояние бота и панелей")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def status_command(self, interaction: discord.Interaction) -> None:
        if not self.is_admin(interaction):
            await interaction.response.send_message("Нужны права «Управление сервером».", ephemeral=True)
            return

        guild = self.guild
        if guild is None:
            await interaction.response.send_message("Бот не видит сервер из config.json.", ephemeral=True)
            return

        missing_roles = [
            entry["role"]
            for entry in self.config["subscriptions"]
            if self.resolve_role(entry["role"]) is None
        ]
        panels = self.state.get("panels") or {}
        panel_lines = []
        for name, title in (("subscriptions", "подписки"), ("verification", "верификация")):
            item = panels.get(name) or {}
            panel_lines.append(f"{title}: {'✅' if item.get('message_id') else '—'}")

        embed = discord.Embed(title="🤖 Состояние бота", color=BRAND_COLOR)
        embed.add_field(name="Сервер", value=f"{guild.name}\n`{guild.id}`", inline=True)
        embed.add_field(name="Участников", value=str(guild.member_count or "—"), inline=True)
        embed.add_field(name="Аптайм", value=human_uptime(time.monotonic() - self.started_at), inline=True)
        embed.add_field(name="Подписки", value=f"{len(self.config['subscriptions'])} ролей", inline=True)
        embed.add_field(name="Инвайты", value=f"{len(self.invite_cache)} в кэше", inline=True)
        embed.add_field(name="Источники", value=f"{len(self.invite_sources)} размечено", inline=True)
        embed.add_field(name="Панели", value="\n".join(panel_lines), inline=False)
        if missing_roles:
            embed.add_field(
                name="⚠️ Не найдены роли",
                value="\n".join(f"• {name}" for name in missing_roles)[:1024],
                inline=False,
            )
        embed.set_footer(text="Perekup • Discord-бот")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------


def main() -> int:
    enable_utf8_console()
    load_env(BASE_DIR / ".env")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    publish_only = "--publish" in sys.argv[1:]
    if publish_only:
        # Для публикации панелей не нужны события участников и привилегированный intent.
        os.environ["MEMBERS_INTENT"] = "false"

    config = load_config()
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        print(
            "ОШИБКА: нет DISCORD_BOT_TOKEN.\n"
            "Скопируй .env.example в .env и вставь токен бота "
            "(Discord Developer Portal → Bot → Reset Token).",
            file=sys.stderr,
        )
        return 1

    state_path = Path(os.environ.get("STATE_FILE", DEFAULT_STATE_FILE))
    state = read_json(state_path, {})
    if not isinstance(state, dict):
        state = {}

    bot = PerekupBot(config, state)
    bot.publish_only = publish_only
    log.info(
        "%s: %d подписок · guild_id=%s",
        "публикация панелей" if publish_only else "запуск",
        len(config["subscriptions"]),
        bot.guild_id or "не задан",
    )
    try:
        bot.run(token, log_handler=None)
    except discord.PrivilegedIntentsRequired:
        log.error(
            "Discord требует включить Server Members Intent. Открой "
            "https://discord.com/developers/applications → своё приложение → Bot → "
            "Privileged Gateway Intents → включи SERVER MEMBERS INTENT → Save, затем снова запусти бота."
        )
        return 1
    except discord.LoginFailure:
        log.error("неверный токен: проверь DISCORD_BOT_TOKEN в .env и не сброшен ли он в портале")
        return 1
    except KeyboardInterrupt:
        log.info("остановлено пользователем")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
