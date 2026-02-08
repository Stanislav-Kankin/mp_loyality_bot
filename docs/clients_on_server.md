# Running clients on a single server

This repo supports hosting many branded client instances on one server.

## One-time: create shared network for Central metrics

```bash
docker network create mp_central_net || true
```

Central stack and all client stacks join this network, so `central_postgres` is resolvable.

## Central (SuperAdmin + central_postgres)

From repo dir:

```bash
cp .env.superadmin.example .env.superadmin
# edit tokens/ids if needed
SUPERADMIN_ENV_FILE=.env.superadmin docker compose -f docker-compose.central.yml up -d --build
```

## Client instance

Create a client env in `/root/clients/<client_slug>/.env`.

Start the client from repo dir with an explicit project name:

```bash
CLIENT_ENV_FILE=/root/clients/<client_slug>/.env \
CENTRAL_NET_NAME=mp_central_net \
docker compose -f docker-compose.client.yml -p <client_slug> up -d --build
```

Logs:

```bash
docker compose -f docker-compose.client.yml -p <client_slug> logs -f --tail=200 bot worker
```

Stop:

```bash
docker compose -f docker-compose.client.yml -p <client_slug> down
```

Delete client полностью (контейнеры + его volumes):

```bash
docker compose -f docker-compose.client.yml -p <client_slug> down -v --remove-orphans
```

> Важно: удалится только volume этого клиента (prefix от `-p <client_slug>`). Central volume не трогаем.

## Notes
- Each client gets its own compose project name (`-p`), therefore its own containers and volumes.
- All clients share the Central metrics DB via `CENTRAL_DATABASE_DSN` and the shared network.
