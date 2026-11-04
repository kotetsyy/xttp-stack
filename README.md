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
panel/templates/             # shared page shell and login
panel/static/                # CSS and native JavaScript modules (no build step)
groups/groups.json           # routing groups/rules (versioned — not secret)
configs/examples/            # config templates WITHOUT secrets
systemd/*.service            # unit templates
scripts/install.sh           # first install
scripts/update.sh            # validated fast-forward + panel-only activation
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
- **download** the exact `version-manifest.json` mihomo/xray tags into `/usr/local/bin/` (amd64/arm64) if missing
  - skip: `XTTP_SKIP_BINARIES=1`  
  - re-download: `XTTP_FORCE_BINARIES=1`
- retain a persistent source checkout; the service starts `panel/app.py` beside its templates/static files
- create missing example configs and device-local groups, and install systemd units
- enable a newly installed fleet timer without starting it; preserve an existing timer's enabled/running policy
- generate and validate providers for an authorized initial mihomo seed; interrupted publication resumes safely
- add hostname to `/etc/hosts` (avoids `sudo: unable to resolve host`)

Then:

1. **Edit secrets** in `/etc/mihomo/config.yaml` and `/etc/xray/config.json`  
   (created from examples if missing), **or** after start paste `vless://` in **Система → Подключение**.
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

## Panel workflows

- **Правила** (`/rules`): searchable groups, an addressable `/rules/<id>` editor, paginated rules, import, route explanation and cross-group conflict overview. Desktop uses master/detail; mobile opens the selected group with a return link.
- **Сохранить** validates all dirty rows and deletions as one transaction with one backup. Drafts survive a failed save. Saving does **not** restart a core; **Применить** separately confirms activation of saved rules and requires unsaved edits to be resolved first.
- **Мониторинг** (`/monitoring/traffic`, `/connections`, `/logs` under that prefix): traffic chart/data and periods, keyed connection controls, searchable tail/live logs. History transfers incrementally; polling stops when its page is hidden.
- **Система**: separate pages for runtime core settings, VLESS/backups, updates, all-admin users and activity/preferences. Ports and TUN are read-only status; supported runtime switches remain editable.
- **Обновления** (`/system/updates`): checking does not install. Installation needs confirmation; the durable operation ID, stage, terminal result and verified rollback remain inspectable after panel restart or page reload. Entry checks only offer updates; they do not change timer policy.

The panel loads one immutable frontend bundle before listening. An older open tab offers an explicit reload after a server-version change, without converting a successful write into an error or silently discarding drafts.
API mutations require a session-bound `X-CSRF-Token` from `/api/session`; browser requests must be same-origin.
Form bodies are bounded to 2 MiB and a 10-second read deadline. Keep HTTP access on a trusted network or behind HTTPS.

## Update (code only; device-local rules are preserved)

On each machine with a git checkout:

```bash
cd /opt/xttp-stack
sudo bash scripts/update.sh
```

This will:

- validate the complete candidate and fast-forward a clean tracked checkout; refuse divergence instead of resetting it
- run the panel and all frontend files from that checkout; preserve local systemd drop-ins and timer policy
- restart **only** the panel (`mihomo-lists`) and check its actual service endpoint, version and assets
- **not** overwrite secret configs under `/etc/mihomo/config.yaml` or `/etc/xray/config.json`
- **never** replace an existing `/etc/mihomo/rule-providers/groups.json`, even when bundled rules are newer

Binary upgrades for mihomo/xray: **Система → Обновления**, separately confirmed. Installer, manual updater, UI workers and timer share one OS update lock.

If fetch or fast-forward fails, the updater stops before installing files.
Older installations may have local `update rules: …` commits: preserve those commits
and back up live rules before manually reconciling the checkout. The updater does not discard local work.

### Upgrading to 0.19.0

Use the persistent checkout named by the service's `XTTP_GIT_REPO`; replace `/opt/xttp-stack` in examples if yours differs.
The exact 0.18.5 SSH updater can perform this transition: the new unit starts the complete source tree, not a copied singleton `app.py`.
Existing 0.18.5 credentials, sessions, rules, core configs and LAN-bind drop-ins are preserved. Only the panel restarts.

For installations **older than 0.18.5**, do not use their old Update button or updater: they can still reset local Git work.
Stop the timer, let any active update finish, and back up device data and the old checkout. Use a clean sibling checkout:

```bash
sudo systemctl stop xttp-update.timer
# Back up /etc/mihomo, /etc/xray, /etc/mihomo-lists, units/drop-ins and the old checkout.
sudo git clone https://github.com/kotetsyy/xttp-stack.git /opt/xttp-stack-0.19.0
# Verify VERSION and version-manifest.json contain 0.19.0 or later.
sudo bash /opt/xttp-stack-0.19.0/scripts/update.sh
```

Keep that checkout: the service now runs its `panel/app.py` and adjacent frontend.
Restore the timer's **previous** policy only after checking login and `/system/updates`; do not enable a previously disabled timer.
Unrelated non-Git install targets are refused without deletion. An intentional non-Git source installation is local-only and cannot remotely update its panel.
`XTTP_INSTALL_ROOT` selects the persistent tree; `XTTP_BIN_DIR` values other than `/usr/local/bin` are rejected before mutation.

A failed panel activation restores the prior complete clean source and units, then verifies readiness. Recovery deliberately detaches the checkout at the old commit while retaining branch history.
Concurrent tracked edits are never discarded: recovery reports failure and retains private backup material for manual repair instead of claiming success.
Config/provider and Xray activations likewise validate candidates before publication and verify restoration on failure.
Xray configs and credential backups are 0600; the secret-backup directory is 0700. Startup hardens existing managed files without changing bytes or restarting cores.

For older missing providers, back up and inspect the config before using **Правила → Применить**. `--prepare-config` only resumes authorized initial seed/journal state; it does not overwrite an arbitrary existing config.
With `XTTP_SKIP_BINARIES=1`, missing native binaries still need installing and native validation before starting services:

```bash
sudo /usr/local/bin/mihomo -t -d /etc/mihomo -f /etc/mihomo/config.yaml
```

An interrupted fresh install keeps its seed marker/publication journal. Rerun the installer to resume; a modified pending config is not silently overwritten.
Legacy default passwords are rotated on startup; read `bootstrap.password` locally if migrating such an installation.

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
# or in the panel: Система → Обновления → «Проверить и обновить весь стек»
```

Интервал по умолчанию: **каждые 30 минут** (`OnUnitActiveSec=30min`).  
Переопределение: `systemctl edit xttp-update.timer`.

При открытии авторизованной панели выполняется проверка версии из опубликованного `main`.
Только новая версия открывает предложение обновления; само открытие панели ничего не устанавливает.
Для установки нужно нажать «Обновить» и подтвердить действие. Совпадающая версия не вызывает диалог.
«Проверить и обновить весь стек» создаёт сохраняемое задание и может обновить ядра и панель.
Принятие задания не означает завершение: смотрите его конечный результат и отдельный статус восстановления в **Система → Обновления**.

### Что делает check-and-update.sh

1. Получает точный манифест из fetched-коммита, не меняя рабочие файлы; ошибка fetch не подменяется старым манифестом.
2. Создаёт сохраняемую fleet-операцию; Python worker владеет общей межпроцессной блокировкой без вложенного shell-flock.
3. Проверяет реальные mihomo/xray и API/SOCKS; при проблеме отказывается от обновления и сохраняет причину.
4. Обновляет точные native-версии из манифеста с backup, ограниченным ожиданием готовности и проверяемым восстановлением.
5. Панель: валидирует неизменяемый Git-коммит, выполняет fast-forward и активирует полный пакет в независимом от панели cgroup.
6. Сохраняет общий конечный результат: сбой любого компонента остаётся ошибкой, даже если другой компонент обновился.
7. Записывает инициатора `автообновление (timer)` или пользователя; ID задания доступен после перезапуска панели.

### Статус

```bash
journalctl -u xttp-update.service -n 50 --no-pager
systemctl list-timers xttp-update.timer
```

Также: **Система → Операции → Журнал**; состояние конкретного задания — **Система → Обновления**.

## Checks

```bash
python3 -B -m unittest discover -s tests -v
for script in scripts/*.sh; do bash -n "$script" || exit; done
```

Tests isolate credentials, generated rules, binary archives, and Git history in temporary directories.
They do not update real services. Run fresh-install, interrupted preparation, exact 0.18.5 upgrade and real systemd worker/recovery smoke checks on a disposable Linux environment. Native ARM64 execution requires an ARM64 host; archive-selection tests alone do not prove it.

## Changelog

История версий панели — файл **CHANGELOG.md** в корне репозитория (GitHub).

**Система → Обновления** загружает его через `GET /api/changelog` по запросу.
Чтобы добавить запись при релизе: допишите секцию ## [x.y.z] - YYYY-MM-DD сверху файла, поднимите VERSION в panel/app.py и panel в version-manifest.json, commit + push.
