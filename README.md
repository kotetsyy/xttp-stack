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
   (created from examples if missing), **or** after start paste `vless://` in **Настройки → Подключение**.
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

- **Правила** (`/rules`): the original 0.18.3 frontend from Git `a2c0fe7`, with narrow accessibility, readability and action-label improvements. Its layout, fonts, palette and motion remain. Groups expand independently in the same document; their original inline tables are restored, without the replacement paginated editor or separate group pages.
- **Сохранить правки** submits changed rows as one validated transaction with one backup. Failed or invalid saves retain edits. Saving does **not** restart a core; **Применить** separately confirms activation of saved rules and requires unsaved edits to be resolved first.
- **Статистика / Соединения / Логи**: original upper-tab transitions, four metrics, live canvas chart, periods, connection controls and searchable tail/live logs. History transfers incrementally with epoch reset; monitoring pauses behind blocking modals and in a hidden document.
- **Настройки**: the original modal and Core / Users / Connection / Operations tabs, horizontally scrollable on narrow screens. Core switches expose their existing text labels and visible keyboard focus. Escape closes Settings and returns focus to its trigger, without dismissing a nested blocking dialog or clearing drafts. Rules drafts remain underneath; runtime/VLESS drafts remain in their controls across tab changes and reopening. Refresh confirms discarding runtime edits. Runtime fields stay disabled until the first successful core snapshot; runtime edits take effect only after **Применить**. Ports and TUN remain read-only.
- **Обновления**: component chips in **Настройки → Ядро** open the original confirmation modal. Checking never installs. Accepted/running is not success: the durable ID, stage, terminal result and recovery status are shown in that modal and resume after reopening or panel restart. Entry checks only offer a newer published panel release; they do not change timer policy.

The panel loads one immutable frontend bundle before listening. Successful panel-update completion reloads only after the draft guard; a server-version header never silently discards edits or converts a completed write into an error. Logout and changing your own password warn about drafts before revoking the session.
API mutations require a session-bound `X-CSRF-Token` from `/api/session`; browser requests must be same-origin.
Form bodies are bounded to 2 MiB and a 10-second read deadline. Keep HTTP access on a trusted network or behind HTTPS.

### Block / BitTorrent

**Настройки → Block** controls Xray's native `protocol: ["bittorrent"]` rejection rule. This is basic protocol recognition, not universal torrent prevention: Mihomo `DIRECT` traffic bypasses Xray, and encryption, obfuscation or unsupported BitTorrent variants can evade recognition.

Enabling prepends the managed `xttp-block-bittorrent` rule before ordinary routes, uses a `blackhole` outbound without changing the default outbound, and enables payload sniffing on traffic inbounds. Previously inactive destination overrides are not activated; existing active domain overrides remain. API-control inbounds are excluded.

Changes require confirmation, validate the candidate with the installed Xray binary, and use the existing activation and recovery path. Only Xray is restarted; its current connections are interrupted. Disabling removes only the managed rule: manual rules, the reusable blackhole outbound and sniffing remain in place.

The UI separately reports the **saved configuration** and whether the **Xray service is running**. Neither is proof that every traffic path is protected. Refresh is read-only; an unavailable native binary or configuration disables the switch and displays the reason. `GET /api/block` reads state; `POST /api/block/bittorrent` uses the normal authenticated, same-origin, CSRF-protected form API with `enabled=1|0` and `confirm=1`.

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

Binary upgrades for mihomo/xray: **Настройки → Ядро**, choose a component chip and confirm. Installer, manual updater, UI workers and timer share one OS update lock.

If fetch or fast-forward fails, the updater stops before installing files.
Older installations may have local `update rules: …` commits: preserve those commits
and back up live rules before manually reconciling the checkout. The updater does not discard local work.

### Upgrading to 0.19.3

Use the persistent checkout named by the service's `XTTP_GIT_REPO`; replace `/opt/xttp-stack` in examples if yours differs.
The exact 0.18.5 SSH updater can perform this transition: the new unit starts the complete source tree, not a copied singleton `app.py`.
Existing 0.18.5 credentials, sessions, rules, core configs and LAN-bind drop-ins are preserved. Only the panel restarts.

For installations **older than 0.18.5**, do not use their old Update button or updater: they can still reset local Git work.
Stop the timer, let any active update finish, and back up device data and the old checkout. Use a clean sibling checkout:

```bash
sudo systemctl stop xttp-update.timer
# Back up /etc/mihomo, /etc/xray, /etc/mihomo-lists, units/drop-ins and the old checkout.
sudo git clone https://github.com/kotetsyy/xttp-stack.git /opt/xttp-stack-0.19.3
# Verify VERSION and version-manifest.json contain 0.19.3 or later.
sudo bash /opt/xttp-stack-0.19.3/scripts/update.sh
```

Keep that checkout: the service now runs its `panel/app.py` and adjacent frontend.
Restore the timer's **previous** policy only after checking login and **Настройки → Ядро**; do not enable a previously disabled timer.
Unrelated non-Git install targets are refused without deletion. An intentional non-Git source installation is local-only and cannot remotely update its panel.
`XTTP_INSTALL_ROOT` selects the persistent tree; `XTTP_BIN_DIR` values other than `/usr/local/bin` are rejected before mutation.

A failed panel activation restores the prior complete clean source and units, then verifies readiness. Recovery deliberately detaches the checkout at the old commit while retaining branch history.
Concurrent tracked edits are never discarded: recovery reports failure and retains private backup material for manual repair instead of claiming success.
Recovery checks the pre-update service version, including when the updater runs from a newer sibling checkout. An unidentified prior version is not reported as verified recovery.
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
# The UI checks components in Настройки → Ядро; it does not start an unconfirmed fleet update.
```

Интервал по умолчанию: **каждые 30 минут** (`OnUnitActiveSec=30min`).  
Переопределение: `systemctl edit xttp-update.timer`.

При открытии авторизованной панели выполняется проверка версии из опубликованного `main`.
Только новая версия открывает предложение обновления; само открытие панели ничего не устанавливает.
Для установки нужно нажать «Обновить» и подтвердить действие. Совпадающая версия не вызывает диалог.
«Проверить обновления сейчас» в **Настройки → Ядро** только проверяет версии и предлагает подтверждение выбранного компонента; это не запуск всего флота.
Принятие задания не означает завершение: смотрите его конечный результат и отдельный статус восстановления в исходном окне обновления. Закрытие окна не отменяет worker; повторное открытие возобновляет наблюдение за ID.

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

Также: **Настройки → Операции → Журнал**; состояние выбранного задания — в окне обновления компонента в **Настройки → Ядро**.

## Checks

```bash
python3 -B -m unittest discover -s tests -v
for script in scripts/*.sh; do bash -n "$script" || exit; done
```

Tests isolate credentials, generated rules, binary archives, and Git history in temporary directories.
They do not update real services. Run fresh-install, interrupted preparation, exact 0.18.5 upgrade and real systemd worker/recovery smoke checks on a disposable Linux environment. Native ARM64 execution requires an ARM64 host; archive-selection tests alone do not prove it.

## Changelog

История версий панели — файл **CHANGELOG.md** в корне репозитория (GitHub).

Чип версии в верхней панели открывает исходный changelog через `GET /api/changelog` по запросу.
Чтобы добавить запись при релизе: допишите секцию ## [x.y.z] - YYYY-MM-DD сверху файла, поднимите VERSION в panel/app.py и panel в version-manifest.json, commit + push.
