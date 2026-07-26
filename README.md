# xttp-stack

Self-hosted **mihomo** (Clash Meta) + **xray** (VLESS Reality) stack with a dark web panel for routing groups, stats, connections, logs, and core settings — **without MetaCube UI**.

| Component | Role |
|-----------|------|
| **mihomo** | Rules, TUN, mixed-port, API `:9090` |
| **xray** | VLESS Reality outbound (socks `127.0.0.1:11090`) |
| **panel** (`mihomo-lists`) | Web UI on `:9080` |

## Repository layout

```
panel/app.py                 # panel application
groups/groups.json           # routing groups/rules (versioned — not secret)
configs/examples/            # config templates WITHOUT secrets
systemd/*.service            # unit templates
scripts/install.sh           # first install
scripts/update.sh            # git pull + restart panel
```

## ⚠️ Secrets — do not commit

**Never** put real configs with filled-in secrets into git:

- `/etc/xray/config.json` — UUID, Reality keys, shortId  
- `/etc/mihomo/config.yaml` — if you add secrets  
- `users.json`, `session.secret`, `.env`, `*.pem` / `*.key`  

Use only:

- `configs/examples/mihomo.config.example.yaml`  
- `configs/examples/xray.config.example.json`  

`.gitignore` blocks common secret paths. **Private repos are not a substitute** for a proper ignore list.

## Fresh install

```bash
# as root on Ubuntu/Debian
git clone https://github.com/kotetsyy/xttp-stack.git /opt/xttp-stack
cd /opt/xttp-stack
sudo bash scripts/install.sh
```

Then:

1. Install binaries to `/usr/local/bin/mihomo` and `/usr/local/bin/xray`  
   (from [MetaCubeX/mihomo](https://github.com/MetaCubeX/mihomo/releases) and [XTLS/Xray-core](https://github.com/XTLS/Xray-core/releases)).
2. **Edit secrets** in `/etc/mihomo/config.yaml` and `/etc/xray/config.json`  
   (created from examples if missing).
3. Start services:

```bash
sudo systemctl start mihomo xray mihomo-lists
```

4. Open `http://<host>:9080` — default bootstrap user `admin` / `changeme` (change immediately).

`install.sh` **enables** units but **does not start** them until you finish configs.

## Update (code + groups)

On each machine with a git checkout:

```bash
cd /opt/xttp-stack
sudo bash scripts/update.sh
```

This will:

- `git pull` (panel + `groups/groups.json` + examples)
- reinstall `app.py` and units
- restart **only** the panel (`mihomo-lists`)
- **not** overwrite secret configs under `/etc/mihomo/config.yaml` or `/etc/xray/config.json`

Binary upgrades for mihomo/xray: panel → **Settings → Core → version chips**, or manual download.

Optional: `XTTP_SYNC_GROUPS=0 sudo ./scripts/update.sh` to skip copying repo groups over the live file.

## Groups versioning (git)

`groups/groups.json` is committed in this repo (rules/CIDR/domains — not secrets).

On a machine where the panel runs with:

```text
Environment=XTTP_GIT_REPO=/opt/xttp-stack
Environment=XTTP_GIT_COMMIT=1
```

saving rules in the UI also schedules a **background** `git commit` of `groups/groups.json` (message like `update rules: …`).  
If git fails, the panel save still succeeds — disk file is source of truth; git is extra history.

Disable: `XTTP_GIT_COMMIT=0`.

## License

MIT — see [LICENSE](LICENSE).  
mihomo and Xray are third-party projects with their own licenses.


## Как выкатить обновление на всех

1. Обновите код/группы в репозитории.
2. Правите корневой **`version-manifest.json`** (версии panel / mihomo / xray + `updated_at`).
3. `git commit` + `git push` в `main`.
4. На каждом устройстве таймер `xttp-update.timer` (до 30 мин) подхватит манифест и применит обновления.

Сигнал флоту = **push `version-manifest.json` в main**, не только тег.

## Автообновление флота

### Включение на устройстве

```bash
# репозиторий должен жить в /opt/xttp-stack (git clone)
sudo systemctl enable --now xttp-update.timer
# первый ручной прогон:
sudo systemctl start xttp-update.service
# или из панели: Настройки → Ядро → «Проверить обновления сейчас»
```

Интервал по умолчанию: **каждые 30 минут** (`OnUnitActiveSec=30min`).  
Переопределение: `systemctl edit xttp-update.timer`.

### Что делает check-and-update.sh

1. `flock` — не запускается параллельно  
2. Health (mihomo + xray + API/socks) — при сбое **пропуск** с записью в журнал  
3. `git fetch` + чтение `version-manifest.json` с `origin/main`  
4. mihomo/xray: та же логика, что кнопка «Обновить» на чипах (backup/restart/healthcheck/rollback)  
5. panel: при отставании — существующий `scripts/update.sh`  
6. Журнал UI: инициатор `автообновление (timer)` или `ручной (<user>)`

### Статус

```bash
journalctl -u xttp-update.service -n 50 --no-pager
systemctl list-timers xttp-update.timer
```

Также: панель → **Операции → Журнал** (записи fleet_*).
