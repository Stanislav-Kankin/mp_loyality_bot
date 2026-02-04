# mp_loyality_bot

Telegram-бот лояльности для селлеров WB/Ozon (MVP “коробка”).
Стек: Python 3.12 + aiogram 3.x + PostgreSQL + Docker Compose. Без Redis/веба.

## Быстрый старт (TEST по умолчанию)

1) Создай .env для TEST:
```bash
cp .env.test.example .env
```

2) Подними контейнеры:
```bash
docker compose up -d --build
```

3) Логи:
```bash
docker compose logs -f --tail=200 bot worker
```

## Переменные окружения

- TEST: см. `.env.test.example`
- PROD: см. `.env.prod.example`


## Архитектура (черновик)

- `src/loyalty_bot/bot/` — Telegram бот (handlers/routers)
- `src/loyalty_bot/worker/` — воркер рассылок (очередь в PostgreSQL)
- `src/loyalty_bot/db/` — подключение к БД + миграции (SQL-файлы)
- `migrations/` — SQL миграции, применяются на старте контейнеров

## FastAPI (опционально)

Сейчас MVP работает без веба. В будущем можно добавить небольшой FastAPI-сервис для:
- трекинга кликов через HTTP-redirect (честная статистика),
- внешних интеграций (WB/Ozon),
- webhook-уведомлений.

Но на MVP это не нужно — всё управление делаем в боте.

## Контуры TEST / PROD

### TEST (текущая директория)

В этой директории держим **тестовый** контур.

Запуск:
```bash
docker compose up -d --build
```

Логи:
```bash
docker compose logs -f --tail=200 bot worker
```

### PROD (в отдельной директории)

Рекомендуется держать PROD **в отдельной папке**, чтобы:
- не смешивать `.env` и токены,
- не смешивать volume Postgres,
- можно было безопасно тестировать изменения.

Создание prod-директории (на сервере):
```bash
cd ~
cp -a mp_loyality_bot mp_loyality_bot_prod
cd mp_loyality_bot_prod
cp .env.prod.example .env
```

Далее **в `.env`** (prod) руками проставляешь:
- `BOT_TOKEN` (боевой)
- `PAYMENT_PROVIDER_TOKEN` (боевой)
- `ADMIN_TG_IDS`
- `POSTGRES_PASSWORD` и `DATABASE_DSN`

Запуск PROD через отдельный compose-файл (использует volume `pg_data_prod`):
```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Логи PROD:
```bash
docker compose -f docker-compose.prod.yml logs -f --tail=200 bot worker
```

Остановка PROD:
```bash
docker compose -f docker-compose.prod.yml down
```
