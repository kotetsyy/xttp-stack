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
- `users.json`, `sessions.json`, `bootstrap.password`, `.env`, `*.pem` / `*.key`

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

`install.sh` will:

- install packages (`python3`, `curl`, `git`, …)
- **download** `mihomo` + `xray` into `/usr/local/bin/` (amd64/arm64) if missing  
  - skip: `XTTP_SKIP_BINARIES=1`  
  - re-download: `XTTP_FORCE_BINARIES=1`
- install panel, example configs, groups, systemd units (+ fleet timer enabled, not started)
- generate provider YAMLs for a newly created mihomo config before first start; existing configs are preserved
- add hostname to `/etc/hosts` (avoids `sudo: unable to resolve host`)

Then:

1. **Edit secrets** in `/etc/mihomo/config.yaml` and `/etc/xray/config.json`  
   (created from examples if missing), **or** after start paste `vless://` in the panel (Settings → Connection).
2. Start services:

```bash
sudo systemctl start mihomo xray mihomo-lists
sudo systemctl enable --now xttp-update.timer   # optional fleet auto-update
```

3. Open an SSH tunnel from your computer:

```bash
ssh -N -L 9080:127.0.0.1:9080 <user>@<host>
```

Open `http://127.0.0.1:9080`. The panel binds to loopback by default.
Log in as `admin`; read its unique initial password on the server:

```bash
sudo cat /etc/mihomo-lists/bootstrap.password
```

The password file is created on first panel start and removed when the admin password is changed.
Change the initial password after login. Password changes revoke every session for that user;
logout revokes the current session, including copies of its cookie.

For HTTPS access, keep the backend on loopback and use a trusted HTTPS reverse proxy.
Set `Environment=XTTP_COOKIE_SECURE=1` in a `systemctl edit mihomo-lists` override
when browsers connect exclusively through HTTPS. Do not set this for the HTTP SSH-tunnel URL.
`XTTP_HOST` can explicitly change the bind address; do not expose the HTTP backend on an untrusted network.

Services are **enabled** but **not started** until configs/secrets are ready.

## Update (code only; device-local rules are preserved)

On each machine with a git checkout:

```bash
cd /opt/xttp-stack
sudo bash scripts/update.sh
```

This will:

- fetch and fast-forward the code checkout; refuse divergent history instead of resetting it
- reinstall `app.py` and units
- restart **only** the panel (`mihomo-lists`)
- **not** overwrite secret configs under `/etc/mihomo/config.yaml` or `/etc/xray/config.json`
- **never** replace an existing `/etc/mihomo/rule-providers/groups.json`, even when bundled rules are newer

Binary upgrades for mihomo/xray: panel → **Settings → Core → version chips**, or manual download.

If fetch or fast-forward fails, the updater stops before installing files.
Older installations may have local `update rules: …` commits: preserve those commits
and back up live rules before manually reconciling the checkout. The updater does not discard local work.

### Upgrading to 0.18.5

Do not use an older panel's Update button or its old `update.sh` for the first transition:
those deployed versions can still hard-reset their checkout. After 0.18.5 is published,
stop the update timer, let any running update finish, and back up both live rules and the old checkout.
Install the new updater from a clean sibling checkout, leaving the old checkout untouched:

```bash
sudo systemctl stop xttp-update.timer
# Wait for any running xttp-update.service to finish; back up live rules and the old checkout.
sudo git clone https://github.com/kotetsyy/xttp-stack.git /opt/xttp-stack-0.18.5
sudo bash /opt/xttp-stack-0.18.5/scripts/update.sh
```

Verify the new checkout contains release 0.18.5 or later before running its updater.
The installed panel and fleet unit use that new checkout; the old checkout remains available for recovery.
Restart the timer only after verifying the panel through the SSH tunnel.

- Establish the SSH tunnel before updating: direct network access to port 9080 is no longer the default.
- Existing custom passwords are preserved; a legacy `admin/changeme` password is replaced at startup.
  Read the replacement from `/etc/mihomo-lists/bootstrap.password`.
- Old signed-cookie sessions are invalidated once; log in again.
- If an earlier installation has missing provider YAMLs, back up the managed mihomo config,
  then use the panel's Apply action or `sudo python3 /opt/mihomo-lists/app.py --prepare-config`.
  This regenerates the panel-managed providers/rules section; it is not an importer for arbitrary custom configs.
- With `XTTP_SKIP_BINARIES=1`, initial generation works without mihomo.
  Install mihomo and run `mihomo -t -d /etc/mihomo -f /etc/mihomo/config.yaml` before starting it.

An interrupted fresh install keeps `config.yaml.xttp-prepare.sha256` and resumes preparation on rerun.
If the pending config was edited, installation stops without rewriting it.
Review/complete provider generation before manually removing that marker.

If session JSON is malformed, it is quarantined as `sessions.json.invalid.*` and old sessions stay invalid.
Credentials are retained and valid users can log in again; check the service journal for the quarantine path.

## Device-local routing rules

`/etc/mihomo/rule-providers/groups.json` is the source of truth for each device.
Panel saves use atomic replacement and rotating backups in `/etc/mihomo-lists/groups-backups/`;
they do not modify the Git checkout or create commits.

The repository's `groups/groups.json` supplies initial rules only when the live file is absent.
Changing bundled rules or updating panel code does not distribute routing changes to existing devices.
There is no `XTTP_GIT_COMMIT` or `XTTP_SYNC_GROUPS` mode.

## License

MIT — see [LICENSE](LICENSE).  
mihomo and Xray are third-party projects with their own licenses.


## Как выкатить обновление на всех

1. Обновите код в репозитории. Правила действующих устройств остаются локальными.
2. Правите корневой **`version-manifest.json`** (версии panel / mihomo / xray + `updated_at`).
3. `git commit` + `git push` в `main`.
4. На каждом устройстве таймер `xttp-update.timer` (до 30 мин) подхватит манифест и применит обновления.

Сигнал флоту = **push `version-manifest.json` в main**, не только тег.  
Поле `panel` сравнивается с `VERSION` в коде панели (semver); `mihomo`/`xray` — с установленными бинарниками.
Версии mihomo/xray в манифесте — точные цели обновления, не сигнал скачать `latest`.
Ядра выбираются под архитектуру устройства: amd64 или arm64.

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

## Checks

```bash
python3 -B -m unittest discover -s tests -v
bash -n scripts/install.sh scripts/update.sh scripts/check-and-update.sh
```

Tests isolate credentials, generated rules, binary archives, and Git history in temporary directories.
They do not update real services. Full installer/systemd and native ARM64 smoke checks still require a disposable Linux host.

## Changelog

История версий панели — файл **CHANGELOG.md** в корне репозитория (GitHub).

Чип **v…** в topbar панели подгружает его через GET /api/changelog.
Чтобы добавить запись при релизе: допишите секцию ## [x.y.z] - YYYY-MM-DD сверху файла, поднимите VERSION в panel/app.py и panel в version-manifest.json, commit + push.
