# mp_loyality_bot

Telegram-бот лояльности для селлеров WB/Ozon (MVP “коробка”).
Стек: Python 3.12 + aiogram 3.x + PostgreSQL + Docker Compose. Без Redis/веба.

## Быстрый старт (PROD / локально)

1) Скопируй env:
```bash
cp .env.example .env
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

Смотри `.env.example`.

## Архитектура (черновик)

- `src/loyalty_bot/bot/` — Telegram бот (handlers/routers)
- `src/loyalty_bot/worker/` — воркер рассылок (очередь в PostgreSQL)
- `src/loyalty_bot/db/` — подключение к БД + миграции (SQL-файлы)
- `migrations/` — SQL миграции, применяются на старте контейнеров
