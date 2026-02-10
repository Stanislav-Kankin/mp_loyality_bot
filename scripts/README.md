# scripts/

Набор маленьких bash-скриптов, чтобы не путаться с командами docker compose и env-файлами.

## Установка
Из корня репозитория:

```bash
chmod +x scripts/*.sh
```

## clientctl.sh
Управление любым клиентским инстансом (bot + worker + postgres) через один интерфейс.

Примеры:

```bash
./scripts/clientctl.sh ps test_client2
./scripts/clientctl.sh up test_client2
./scripts/clientctl.sh rebuild test_client2
./scripts/clientctl.sh logs test_client2 bot
./scripts/clientctl.sh logs test_client2 worker
./scripts/clientctl.sh restart test_client2
./scripts/clientctl.sh env test_client2
./scripts/clientctl.sh exec test_client2 python -c "import os; print(os.getenv('INSTANCE_ID'))"
```

## centralctl.sh
Управление central стеком (central_postgres + superadmin + payment_hub).

Примеры:

```bash
./scripts/centralctl.sh ps
./scripts/centralctl.sh up
./scripts/centralctl.sh logs payment_hub
./scripts/centralctl.sh restart payment_hub
```

## newclient.sh
Создать нового клиента из шаблона env:

```bash
./scripts/newclient.sh my_new_client
# или с другим шаблоном
./scripts/newclient.sh my_new_client --from /root/clients/client1_andrew/.env
```

По умолчанию шаблон: `/root/clients/test_client2/.env`.

После создания обязательно зайти в env и заменить:
- BOT_TOKEN
- INSTANCE_NAME
- ADMIN_TG_IDS
- прайсы (если нужно)
- HUB_BOT_USERNAME (если отличается)
