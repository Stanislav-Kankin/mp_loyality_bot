# Deploy on one server: central + multiple client instances

## Folder layout (your target)

- /root/mp_loyality_bot_prod  (single code repo)
- /root/clients/<client_slug>/  (per client: env + logs)
- /root/central/  (superadmin env + logs)

You already created /root/clients/client1_andrew.

---

## 1) Central (SuperAdmin + central_postgres)

### Prepare folder
```
mkdir -p /root/central/logs
cd /root/central
cp /root/mp_loyality_bot_prod/.env.superadmin.example .env.superadmin
```
Fill `.env.superadmin` (tokens, DSN secrets). Do NOT commit secrets.

### Start (from repo)
```
cd /root/mp_loyality_bot_prod
docker compose -p central -f docker-compose.central.yml --env-file /root/central/.env.superadmin up -d --build
docker compose -p central -f docker-compose.central.yml ps
docker compose -p central -f docker-compose.central.yml logs -f --tail=200 superadmin central_postgres
```

> Note: by default central compose reuses existing volume `mp_loyality_bot_central_pg_data`.
> If you want a fresh central DB, set `CENTRAL_PG_VOLUME_NAME=central_pg_data` in `/root/central/.env.superadmin`
> and create it explicitly: `docker volume create central_pg_data`.

---

## 2) Client instance (brand mode)

### Prepare folder
```
mkdir -p /root/clients/client1_andrew/logs
cd /root/clients/client1_andrew
cp /root/mp_loyality_bot_prod/env.example.client .env
```
Edit `.env`:
- BOT_TOKEN
- PAYMENT_PROVIDER_TOKEN
- ADMIN_TG_IDS
- POSTGRES_PASSWORD + DATABASE_DSN
- CENTRAL_DATABASE_DSN (points to central metrics DB via network; see below)
- INSTANCE_ID / INSTANCE_NAME

### Start (from repo, but project name = client slug)
```
cd /root/mp_loyality_bot_prod
docker compose -p client1_andrew -f docker-compose.client.yml --env-file /root/clients/client1_andrew/.env up -d --build
docker compose -p client1_andrew -f docker-compose.client.yml ps
docker compose -p client1_andrew -f docker-compose.client.yml logs -f --tail=200 bot worker postgres
```

Volumes will be unique automatically: `client1_andrew_pg_data`.

---

## 3) Networking: central reachability

Easiest rule: put clients and central into a shared external docker network and connect both projects to it.
If you haven't yet, create network once:
```
docker network create mp_shared
```

Then we add `networks:` to both compose files in the next patch step (if you need cross-project DNS like `central_postgres`).
If you prefer a simpler MVP right now: set CENTRAL_DATABASE_DSN to point to central_postgres by IP or publish port.
But shared network is cleaner.

---

## 4) Stop / remove

Stop one client:
```
cd /root/mp_loyality_bot_prod
docker compose -p client1_andrew -f docker-compose.client.yml down
```

Stop central:
```
cd /root/mp_loyality_bot_prod
docker compose -p central -f docker-compose.central.yml down
```

Data is kept in volumes unless you remove volumes explicitly.
