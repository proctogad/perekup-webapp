# PerekupDisBot — Discord-бот сервера

Бот для сервера **GTA5RP | Перекуп Калькулятор**: подписки на пинги кнопками,
верификация, приветствие новичков и метки «откуда пришёл».

Код лежит в GitHub, но **сам бот работает не на GitHub**: Discord-боту нужно
постоянное подключение к gateway. GitHub Actions для этого не подходит
(запуск по расписанию, лимит времени, нет постоянного процесса). Нужен
Railway / Render / VPS / домашний ПК — подробности ниже.

---

## Что умеет

| Функция | Где | Как работает |
| --- | --- | --- |
| Панель подписок | `#роли` | 14 кнопок: нажал — роль выдалась, нажал ещё раз — снялась |
| Верификация | `#правила` | Кнопка «Я прочитал правила» выдаёт `✅ Знает правила` и убирает `👋 Новичок` |
| Приветствие | `#welcom` | Упоминает новичка, вешает `👋 Новичок`, показывает шаги и источник входа |
| Источник входа | — | Смотрит, по какой инвайт-ссылке пришёл: `📱 Из Telegram`, `🖥️ Из EXE`, `🌐 С сайта` |
| Команды | — | `/panel`, `/verify-panel`, `/status` — только для админов |

Подписки: Новости, Медиа, Обновления, Уставка, Мероприятия, Отзывы, Идеи,
Чат, Скины, Баги, Войс-1…4. Полный список и названия ролей — в `config.json`.

---

## Файлы

| Файл | Что это |
| --- | --- |
| `bot.py` | весь бот: кнопки, верификация, приветствие, инвайты, команды |
| `config.json` | ID каналов, названия ролей и список подписок |
| `invites.json` | создаётся скриптом `create_invites.py`: код ссылки → роль источника |
| `state.json` | ID сообщений с панелями (создаётся автоматически, в git не попадает) |
| `.env` | токен и настройки (создаётся из `.env.example`, в git не попадает) |
| `requirements.txt` | зависимость: `discord.py` |
| `Dockerfile`, `Procfile` | для деплоя на Railway / Render / VPS |
| `start.bat` | запуск на Windows двойным щелчком |

---

## Быстрый старт на компьютере

### 1. Включить Server Members Intent

Без этого не работают приветствие, роль «Новичок» и источник входа.

1. <https://discord.com/developers/applications> → своё приложение.
2. Слева **Bot**.
3. Блок **Privileged Gateway Intents** → включить **SERVER MEMBERS INTENT**.
4. **Save Changes**.

Заодно проверь токен: **Reset Token** → **Copy**. Старый токен после сброса
перестаёт работать — не забудь обновить `.env`.

### 2. Установить зависимости

```powershell
cd H:\Repozit\PerekupDisBot
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Создать `.env`

```powershell
Copy-Item .env.example .env
notepad .env
```

Вставь токен:

```env
DISCORD_BOT_TOKEN=твой-токен
MEMBERS_INTENT=true
```

`DISCORD_GUILD_ID` можно не указывать — он уже есть в `config.json`.

### 4. Запустить

```powershell
python bot.py
```

Или двойным щелчком по `start.bat`. В логе должно появиться:

```
вошёл как Perekup#1234
сервер: GTA5RP | Перекуп Калькулятор · участников: 42
```

### 5. Опубликовать панели

На сервере (от аккаунта с правом «Управление сервером»):

```
/panel          → панель подписок в #роли
/verify-panel   → панель верификации в #правила
/status         → проверка: роли, инвайты, панели
```

Если Server Members Intent пока не включён, панели можно опубликовать
без него — одной командой, после чего бот завершится:

```powershell
python bot.py --publish
```

Приветствие, роль «Новичок» и источник входа при этом работать не будут,
пока не включишь интент и не запустишь бота обычным `python bot.py`.

Кнопки работают и после перезапуска бота: они постоянные (persistent view).
Если панель потерялась после переезда на другой хостинг — просто выполни
`/panel` ещё раз, бот найдёт старое сообщение и обновит его.

---

## Как это связано с DiscordCatalog

В папке `DiscordCatalog` есть скрипты, которые делают остальную настройку:

```powershell
cd H:\Repozit\DiscordCatalog

# 1. Закрыть Community для новичков, создать #роли, заполнить описания каналов
python finish_setup.py --guild-name "Перекуп Калькулятор" --apply

# 2. Создать 4 бессрочные инвайт-ссылки и файл invites.json для бота
python create_invites.py --guild-name "Перекуп Калькулятор" --apply

# 3. Создать вебхук для новостей об обновлениях (по желанию)
python post_update.py --guild-name "Перекуп Калькулятор" --create-webhook --apply
```

`create_invites.py` по умолчанию пишет `invites.json` прямо в эту папку.
Ссылки затем вставляются в Telegram-бота, EXE и на сайт.

---

## Деплой 24/7

### Вариант A. Railway (проще всего)

1. Запушь репозиторий в GitHub (папка `PerekupDisBot` должна быть в репозитории).
2. <https://railway.app> → **New Project** → **Deploy from GitHub repo**.
3. Выбери репозиторий. В **Settings → Source** укажи **Root Directory** = `PerekupDisBot`.
4. Railway сам увидит `Dockerfile`. Если нет — **Builder** = `Dockerfile`.
5. **Variables**:
   - `DISCORD_BOT_TOKEN` — токен;
   - `DISCORD_GUILD_ID` — `1559945024114794586`;
   - `MEMBERS_INTENT` — `true`.
6. **Deploy**. В логах — `вошёл как ...`.
7. Один раз выполни `/panel` и `/verify-panel` на сервере.

Плюсы: не нужен свой сервер, деплой из GitHub по кнопке. Минусы: бесплатный
пробный период ограничен, для 24/7 нужен платный план (несколько долларов
в месяц). Для Discord-бота нужен именно постоянный процесс, а не web-сервис
с засыпанием.

### Вариант B. Render

1. <https://render.com> → **New** → **Background Worker**.
2. Подключи GitHub-репозиторий, **Root Directory** = `PerekupDisBot`,
   **Runtime** = `Docker`.
3. Добавь те же переменные окружения.
4. Выбери платный план: бесплатные web-сервисы засыпают, а background worker
   на бесплатном плане недоступен.

### Вариант C. VPS (полный контроль)

```bash
sudo apt update && sudo apt install -y git python3-venv
sudo git clone https://github.com/proctogad/perekup-webapp.git /opt/perekup
cd /opt/perekup/PerekupDisBot
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt
sudo cp .env.example .env
sudo nano .env          # вставь токен
.venv/bin/python bot.py # проверка
```

Автозапуск через systemd — `/etc/systemd/system/perekup-bot.service`:

```ini
[Unit]
Description=Perekup Discord Bot
After=network-online.target

[Service]
WorkingDirectory=/opt/perekup/PerekupDisBot
ExecStart=/opt/perekup/PerekupDisBot/.venv/bin/python -u bot.py
Restart=always
RestartSec=5
User=perekup

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now perekup-bot
sudo journalctl -u perekup-bot -f
```

### Вариант D. Домашний ПК

Запускай `start.bat` и оставляй ПК включённым. Чтобы бот поднимался сам,
добавь `start.bat` в **Планировщик заданий Windows** с триггером «При входе
в систему». Минус: компьютер должен быть онлайн 24/7.

### А GitHub?

- **Код** — да, GitHub отлично подходит: история, резервная копия, деплой
  на Railway/Render прямо из репозитория.
- **Запуск бота** — нет. GitHub Actions даёт максимум 6 часов на job, не
  держит постоянное WebSocket-подключение и не годится для gateway-бота.
- Actions можно использовать для **уведомлений о релизах** через вебхук
  (см. `DiscordCatalog/examples/github-release-webhook.yml`) — это отдельная
  задача, не запуск бота.

---

## Настройка `config.json`

Каналы можно указывать ID (числом строкой) или именем. Надёжнее ID: имя
можно переименовать, ID не меняется.

```json
"channels": {
  "welcome": "1559945025607962628",
  "rules": "1559946990165426266",
  "roles": "роли",
  "updates": "1559946867335241728"
}
```

- `welcome` — куда писать приветствие;
- `rules` — канал с правилами и кнопкой верификации;
- `roles` — канал с панелью подписок (создаёт `finish_setup.py`);
- `updates` — канал обновлений (для `post_update.py`, бот его не использует).

`roles.unverified` / `roles.verified` — названия ролей новичка и верификации.
`roles.sources` — роли источника входа; ключи должны совпадать с `key` в
`invites.json` (`telegram`, `exe`, `site`).

`subscriptions` — кнопки панели. У каждой: `key` (уникальный), `role`
(точное имя роли), `label` (подпись кнопки), `emoji`, `group` (заголовок
блока в описании панели). Максимум 25 кнопок, по 5 в ряд.

---

## Инвайты и источник входа

`create_invites.py` создаёт четыре бессрочные ссылки:

| Ключ | Роль | Куда вставить |
| --- | --- | --- |
| `telegram` | `📱 Из Telegram` | кнопка в Telegram-боте / Mini App |
| `exe` | `🖥️ Из EXE` | ссылка в EXE-приложении |
| `site` | `🌐 С сайта` | кнопка на сайте |
| `default` | без роли | запасная ссылка для всех остальных |

Бот при входе сравнивает счётчики использований инвайтов и выдаёт роль по
коду из `invites.json`. Если ссылку пересоздали — запусти
`create_invites.py --apply` ещё раз: он проверит старые коды и создаст
только недостающие.

Требования: право **«Управление сервером»** (для чтения инвайтов) и
включённый **Server Members Intent**.

---

## Частые проблемы

| Симптом | Причина и решение |
| --- | --- |
| `PrivilegedIntentsRequired` при запуске | включи **SERVER MEMBERS INTENT** в портале и перезапусти |
| `LoginFailure` / `401` | неверный или сброшенный токен — обнови `.env` |
| Кнопки не реагируют | бот не запущен или упал; смотри логи. После смены `config.json` выполни `/panel` заново |
| Роль не выдаётся | роль бота должна стоять **выше** выдаваемой; право «Управление ролями» |
| Нет приветствия | выключен Server Members Intent или бот не видит `#welcom` |
| Источник входа не определяется | нет `invites.json`, нет права «Управление сервером» или человек зашёл по ссылке, созданной до запуска бота (кэш обновится после перезапуска) |
| Панель пропала после деплоя | файловая система хостинга эфемерная — выполни `/panel` ещё раз |
| `state.json` не сохраняется | на некоторых хостингах диск только для чтения; панель всё равно можно переопубликовать командой |

---

## Безопасность

- `.env` и `state.json` — в `.gitignore`. Токен не коммить.
- Если токен засветился (чат, скриншот, лог) — **Reset Token** в портале,
  старый сразу перестанет работать.
- `invites.json` содержит только публичные ссылки-приглашения, секретов там нет.
- Боту не нужны права администратора для работы панелей, но на этом сервере
  у роли `Perekup` уже есть Administrator — так проще, пока бот один.
