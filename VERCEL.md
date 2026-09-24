# Развёртывание на Vercel с Supabase

Проект работает на Vercel как одна FastAPI Function. Telegram отправляет обновления через webhook, а внешний cron-сервис каждый час вызывает защищённый endpoint рассылки.

## 1. Подготовка Supabase

1. Создайте проект в Supabase и сохраните пароль базы данных.
2. Откройте **Connect** в панели проекта.
3. Для Vercel скопируйте строку **Transaction pooler** с портом `6543`. Она имеет примерно такой вид:

   ```text
   postgresql://postgres.PROJECT_REF:PASSWORD@POOLER_HOST:6543/postgres
   ```

Если пароль содержит `@`, `:`, `/`, `?` или `#`, URL-кодируйте его. Не используйте Supabase API URL и anon/service-role key вместо строки PostgreSQL.

Приложение автоматически преобразует `postgresql://` в `postgresql+psycopg://`, включает обязательный SSL, полностью отключает prepared statements, использует `NullPool` и выполняет независимые запросы в autocommit-режиме. Массовое сохранение курсов выполняется одним многострочным `INSERT`, без несовместимого с Transaction pooler `executemany`.

## 2. Создание ключей

В активированном виртуальном окружении установите единственный файл зависимостей и создайте ключ шифрования:

```powershell
pip install -r requirements.txt
python -m scripts.generate_key
```

Сохраните полученный `APP_ENCRYPTION_KEY`. После сохранения Money-X токена менять этот ключ нельзя: существующий токен перестанет расшифровываться.

Создайте также две разные случайные строки длиной не менее 32 символов. Команду можно выполнить дважды:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

- `TELEGRAM_WEBHOOK_SECRET` — проверка запросов Telegram;
- `CRON_SECRET` — проверка запросов внешнего cron-сервиса.

## 3. Создание схемы в Supabase вручную

1. В Supabase откройте **SQL Editor → New query**.
2. Откройте локальный файл `migrations/001_initial.sql`, скопируйте его целиком и вставьте в редактор.
3. Нажмите **Run**. Внизу должно появиться сообщение об успешном выполнении без ошибок.
4. Откройте **Table Editor** и убедитесь, что появились таблицы `settings`, `secrets`, `delivery_runs`, `rate_snapshots`, `admin_alerts` и `pending_actions`.

SQL идемпотентен: его можно запускать повторно после обновления файла. Он сохраняет существующие рабочие данные, добавляет недостающие объекты и включает RLS без публичных политик. Приложение не содержит команды автоматической миграции и не меняет схему при старте.

## 4. Настройка проекта Vercel

1. Загрузите проект в приватный репозиторий GitHub, GitLab или Bitbucket.
2. В Vercel выберите **Add New → Project** и импортируйте репозиторий.
3. Не задавайте Build Command и Output Directory. Vercel автоматически обнаружит FastAPI-приложение `app` в `api/index.py`.
4. Добавьте следующие переменные для окружения **Production**:

   | Переменная | Значение |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | токен от BotFather |
   | `ADMIN_TELEGRAM_ID` | ваш числовой Telegram ID |
   | `TELEGRAM_GROUP_ID` | отрицательный ID группы, например `-1001234567890` |
   | `TELEGRAM_WEBHOOK_SECRET` | отдельная случайная строка |
   | `CRON_SECRET` | отдельная случайная строка для cron |
   | `APP_ENCRYPTION_KEY` | ключ из шага 2 |
   | `DATABASE_URL` | Supabase **Transaction pooler**, порт `6543` |
   | `APP_BASE_URL` | стабильный production URL без завершающего `/` |

5. Выполните production deployment. Если `APP_BASE_URL` стал известен только после первого деплоя, добавьте его и сделайте Redeploy.

Функция закреплена в регионе Frankfurt (`fra1`), рядом с Supabase `eu-central-1`. Это также исключает стандартный запуск Vercel из региона США (`iad1`).

Python 3.14 задаётся файлом `.python-version`. `vercel.json` ограничивает функцию 60 секундами и не содержит встроенного расписания.

## 5. Проверка приложения

Откройте:

```text
https://YOUR_PROJECT.vercel.app/api/health
```

Рабочий ответ:

```json
{"status":"ok","missing":[],"database":"ok"}
```

Если получено `misconfigured`, добавьте перечисленные в `missing` переменные. Если получено `degraded`, перепроверьте `DATABASE_URL`, пароль, порт `6543` и состояние проекта Supabase.

## 6. Регистрация Telegram webhook

На доверенном локальном компьютере укажите в `.env` те же значения `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` и `APP_BASE_URL`, затем выполните:

```powershell
python -m scripts.register_webhook
```

Добавьте бота в группу из `TELEGRAM_GROUP_ID` с правом отправлять сообщения. Команды выполняйте только в личном чате с ботом.

Настройте авторизацию Money-X одним из способов:

- в личном чате отправьте `/auth_set` и следуйте подсказкам;
- либо локально подключитесь к той же Supabase базе с тем же `APP_ENCRYPTION_KEY` и выполните `python -m scripts.set_auth`.

После этого проверьте `/auth_status`, `/status` и `/check`. Команда `/check` должна быть отправлена в личном чате, а отчёт должен появиться только в группе.

## 7. Внешний cronjob

Создайте в выбранном cron-сервисе HTTP-задачу:

- URL: `https://YOUR_PROJECT.vercel.app/api/hourly/02`;
- метод: `GET`;
- расписание: каждый час в минуту `02`;
- заголовок: `Authorization: Bearer ВАШ_CRON_SECRET`;
- timeout: максимально доступный, желательно не менее 60 секунд;
- автоматические повторы: выключены, потому что каждый HTTP-вызов запускает новую рассылку.

Сначала используйте кнопку ручного запуска задачи. Успешный ответ имеет `status` со значением `sent`. Ответ `401` означает неверный заголовок, `failed` — ошибку Money-X или Telegram, а `busy` — уже выполняющуюся проверку.

Часовой пояс cron-сервиса не влияет на ежечасовое расписание: важна только минута `02`.

## 8. Что проверять после деплоя

1. `/api/health` возвращает `status: ok`.
2. `/auth_status` подтверждает действующую сессию Money-X.
3. `/check` отправляет полный отчёт в нужную группу.
4. Команды, введённые в группе, игнорируются.
5. Каждый последовательный ручной запуск cronjob возвращает `sent` и создаёт новую рассылку.
6. В Vercel **Observability → Logs** отсутствуют токены, cookie и адреса кошельков.

Официальные справочные материалы:

- https://vercel.com/docs/functions/runtimes/python
- https://vercel.com/kb/guide/ship-a-fastapi-app-on-vercel
- https://supabase.com/docs/guides/database/connecting-to-postgres
- https://supabase.com/docs/guides/troubleshooting/using-sqlalchemy-with-supabase-FUqebT
