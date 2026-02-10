# scripts/

Скрипты, чтобы не путаться с docker compose, project-name (-p) и env-файлами клиентов.

## Установка
```bash
chmod +x scripts/*.sh
```

## centralctl.sh
```bash
./scripts/centralctl.sh ps
./scripts/centralctl.sh logs payment_hub
./scripts/centralctl.sh restart payment_hub
```

## clientctl.sh
```bash
./scripts/clientctl.sh ps demo
./scripts/clientctl.sh up demo
./scripts/clientctl.sh logs demo bot
./scripts/clientctl.sh logs demo worker
./scripts/clientctl.sh restart demo
./scripts/clientctl.sh env demo
./scripts/clientctl.sh exec demo python -c "import os; print(os.getenv('INSTANCE_ID'))"
```

## newclient.sh
```bash
./scripts/newclient.sh client2
nano /root/clients/client2/.env
./scripts/clientctl.sh up client2
```
