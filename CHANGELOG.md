# Changelog

История версий **xttp-stack** (панель).  
UI читает этот файл с GitHub (`main` / `CHANGELOG.md`).

Формат: [Keep a Changelog](https://keepachangelog.com/) (упрощённо).

## [0.18.4] - 2026-07-26

### Features
- `install.sh`: автозагрузка mihomo + xray в `/usr/local/bin` (amd64/arm64), проверка до конца install
- `install.sh`: hostname → `/etc/hosts` (без `sudo: unable to resolve host`)
- `install.sh`: мягкий apt (не падает сразу при сбое зеркала, если tools уже есть)
- Env: `XTTP_SKIP_BINARIES=1`, `XTTP_FORCE_BINARIES=1`

## [0.18.3] - 2026-07-26

### Features
- Changelog хранится в `CHANGELOG.md` на GitHub и подгружается в UI (чип версии)
- API `GET /api/changelog` с кешем; fallback на локальный файл репозитория

### Bug Fixes
- Обновление панели: git sync без upstream, отложенный restart, toast/alert поверх Settings

## [0.18.2] - 2026-07-26

### Features
- Чип «проверка обновления панели»: сравнение с `version-manifest.json` на GitHub
- Обновление панели из UI через `update.sh` (git pull + install)

### Bug Fixes
- Модалка обновления поверх Settings (z-index, pointer-events, document-delegation)
- Свежая проверка GitHub (API contents + force, без stale CDN)

## [0.18.1] - 2026-07-26

### Features
- Fleet auto-update: `version-manifest.json`, `check-and-update.sh`, systemd timer
- Журнал операций: инициатор «автообновление (timer)»
- Кнопка «Проверить обновления сейчас» (флот)

### Bug Fixes
- Сигнал panel-update только по semver манифеста (не git behind)
- systemd: корректный `Environment` для initiator с пробелами

## [0.18.0] - 2026-07-26

### Features
- Публичный репозиторий xttp-stack (panel, groups examples, install/update)
- Git-версионирование `groups.json` из UI (`XTTP_GIT_COMMIT`)

### Bug Fixes
- Ядро: клик по чипам версий — спиннер, toast, debug, document-delegation

## [0.17.2]
### Features
- Ядро: клик по чипам mihomo/xray/panel — проверка обновлений (GitHub / changelog)
- Ядро: обновление mihomo/xray с бэкапом, healthcheck и откатом; panel — вручную

### Bug Fixes
- Убран чип Ubuntu/kernel из блока версий


## [0.17.1]
### Features
- Ядро: блок «Версии компонентов» (mihomo / xray / panel / os), кеш 8 мин


## [0.17.0]
### Features
- Ядро: TUN-статус (read-only) + sniffing toggle
- Ядро: автообновление geo-баз, интервал (часы), «Обновить сейчас»


## [0.16.9]
### Bug Fixes
- Changelog: версии 0.15.x–0.16.x разнесены по отдельным пунктам (не в одном CURRENT)


## [0.16.8]
### Bug Fixes
- Настройки: mesh за scrim + скрыт topbar/список (без имён сервисов)


## [0.16.7]
### Bug Fixes
- Настройки: ambient mesh виден за полупрозрачным scrim (первая итерация)


## [0.16.6]
### Features
- Ambient mesh-фон панели восстановлен/усилен (под карточками, z-index 0)


## [0.16.5]
### Bug Fixes
- Ядро: tooltip «?» fixed + flip/clamp в viewport (портал в body, не обрезается)


## [0.16.4]
### Bug Fixes
- Ядро: карточки grid по контенту (align-items:start — не тянуть «Уровень логов»)
- Ядро: tooltip «?» с читаемым текстом (.core-tip-pop)


## [0.16.3]
### Bug Fixes
- Настройки: scrim/карточка 100% solid #12141a (без rgba alpha)
- Ядро: tooltip «?» не наезжает на соседние карточки


## [0.16.2]
### Bug Fixes
- Настройки/модалки: плотный scrim + solid surface (логи не просвечивают)
- Tooltip «?» и changelog: непрозрачный фон


## [0.16.1]
### Bug Fixes
- Ядро: GET /api/core больше не падает (порты / Failed to fetch)
- Ядро: пояснения к mode / log-level / find-process + порты списком


## [0.16.0]
### Features
- Настройки → <strong>Ядро</strong>: mode / log-level / allow-lan / IPv6 / delay / TCP (mihomo API, без MetaCube)
- Обзор всех правил (иконка списка) — фильтр и переход к группе
- MetaCube chip убран из topbar


## [0.15.3]
### Bug Fixes
- Логи: новые записи сверху (обратный хронопорядок)


## [0.15.2]
### Bug Fixes
- Вкладки Соединения/Логи: добавлены click-handlers


## [0.15.1]
### Features
- Hotfix: кнопки снова кликабельны (TDZ searchInput ломал весь JS)
- Сегмент: Правила | Статистика | Соединения | Логи
- Соединения: live + kill; Логи: stream /logs + фильтр уровня


## [0.14.18]
### Features
- Тест: MetaCube UI отключён (external-ui), API :9090 и панель 9080 работают


## [0.14.17]
### Bug Fixes
- Правила: список не пропадает после быстрого Rules⇄Stats (без hide-timeout; gcard opacity:1)


## [0.14.16]
### Bug Fixes
- Статистика: детерминированные time-bucket’ы (1ч/5м) — форма истории не «плывёт» между тиками


## [0.14.15]
### Bug Fixes
- Правила/Статистика: нет пустого списка после быстрого переключения вкладок (race hide timeout)


## [0.14.14]
### Bug Fixes
- Статистика: live-график плавно «течёт» (rAF-окно + lerp tip, без 1Hz мигания)


## [0.14.13]
### Bug Fixes
- Changelog: wheel/touch скролл списка версий (portal + overflow-y + overscroll)
- Страница под дропдауном больше не перехватывает прокрутку


## [0.14.12]
### Bug Fixes
- Mobile: toggle правил снова ~46×26 (не раздутый 70px+)
- Mobile: sticky topbar непрозрачный, список не просвечивает


## [0.14.11]
### Features
- Mobile: правила как 2-строчные карточки (тип+toggle / полный паттерн)
- Mobile: компактные icon-btn справа (save / explain / +)
- Mobile: «N правил» + теги в одну линию под именем группы
- Mobile: тап по паттерну → toast «Копировать»


## [0.14.10]
### Bug Fixes
- iPhone: changelog открывается (portal + onclick + backdrop)
- iPhone: кнопка changelog кликабельна (pointer-events)
- iPhone: имена групп снова видны (grid-карточки)
- iPhone: табы и кнопки toolbar на всю ширину

### Features
- Мобильная вёрстка: компактная шапка, toolbar, карточки, safe-area


## [0.14.0]
### Features
- Статистика: история графика на сервере (~1 ч, переживает F5)
- График: линии без петель (монотонный bezier)
- Статистика: плавный график ~60 fps (rAF, bezier, soft-fill)
- Статистика: табы периода Live / 5 мин / 1 час / Сессия
- Чекбоксы линий download/upload; tooltip ↓/↑ при наведении

### Bug Fixes
- Статистика: табы периода реально меняют окно графика (ось по времени)


## [0.13.4]
### Bug Fixes
- Настройки: pill вкладок не перекашивается при открытии со Статистики
- Разделены .stab[data-stab] и view-switch .stab[data-view]


## [0.13.3]
### Bug Fixes
- Статистика: opaque sticky-заголовок таблицы при скролле
- Подпись метрики: «срабатываний правил всего»


## [0.13.2]
### Bug Fixes
- Настройки: нет RGB-артефакта заголовка при открытии со Статистики
- Пауза live-графика и isolation/opacity-only анимация модалки


## [0.13.1]
### Bug Fixes
- Статистика: легенда на canvas, колонка «Доля», скролл таблицы
- Карточки по-русски; подпись вместо RAM 0 B


## [0.13.0]
### Features
- Режимы Правила / Статистика (segmented control)
- Дашборд: скорость, трафик, соединения, hits по группам, live-график
- Поиск в Статистике фильтрует таблицу; состояние в localStorage


## [0.12.9]
### Bug Fixes
- Поиск: фильтр строк по паттерну не сбрасывается из-за data-filter группы


## [0.12.8]
### Bug Fixes
- Импорт: починен SyntaxError в .join(newline) — кнопки снова кликабельны


## [0.12.7]
### Features
- Toast после действий через flash-cookie, без ?msg= в URL

### Bug Fixes
- Чистый URL после импорта / apply / других POST


## [0.12.6]
### Features
- Импорт: чекбокс «Не импортировать дубли и конфликты»
- При включённом — на бэк только «новые», кнопка со счётчиком
- Дубли/конфликты в превью: opacity + «будет пропущено»


## [0.12.5]
### Features
- Защита groups.json: lock на load/mutate/save
- Автобэкап groups.json (до 40 копий в groups-backups)
- Блокировка обнуления и catastrophic shrink списка
- При apply: stale yaml → quarantine, не hard-delete
- При битом JSON — restore из последнего бэкапа

### Bug Fixes
- Гонка ThreadingHTTPServer больше не перетирает группы


## [0.12.4]
### Features
- Фильтр конфликтов: внутри группы только конфликтные строки + «N из M»


## [0.12.3]
### Bug Fixes
- Фильтр конфликтов: скрытие групп, focus, сброс


## [0.12.2]
### Bug Fixes
- Пустой conflict-badge: .pill перебивал [hidden]


## [0.12.1]
### Bug Fixes
- Конфликт: акцент только на 1-й ячейке; сводка в футере


## [0.12.0]
### Features
- Конфликты правил: иконка группы, строки, фильтр


## [0.11.4]
### Bug Fixes
- Импорт: убран type-select, статичный бейдж типа


## [0.11.3]
### Bug Fixes
- Импорт: дубль/конфликт — бейдж + left border, без заливки


## [0.11.2]
### Features
- Импорт: сводка, дубли/конфликты, правка типа, «Режим типа»


## [0.11.1]
### Bug Fixes
- Explain route: полоска/кружки/MATCH, статус BLOCK


## [0.11.0]
### Features
- Explain route — домен/IP → правило → группа → PROXY/DIRECT


## [0.10.3]
### Features
- Операции: статус-карточки, цвет точек журнала, auto-refresh лога


## [0.10.2]
### Features
- авто «Проверить связь» при запуске и на вкладке Подключение


## [0.10.1]
### Bug Fixes
- group_add / rename / toggle в activity log


## [0.10.0]
### Features
- Операции: activity log, restart xray/mihomo, live svc, auto-ping


## [0.9.1]
### Features
- soft group hover


## [0.9.0]
### Bug Fixes
- topbar без «полосы»


## [0.8.9]
### Features
- glass topbar (недостаточно — см. 0.9.0)


## [0.8.8]
### Features
- ambient mesh-фон панели


## [0.8.7]
### Features
- UX polish: stripe, иконки, теги, поиск, empty-states


## [0.8.6]
### Bug Fixes
- выход: cookie-only, Basic Auth не держит сессию


## [0.8.5]
### Bug Fixes
- настройки: шапка + вкладки отдельно от скролла (без наезда контента)


## [0.8.4]
### Features
- «Настройки» + × закреплены при скролле модалки


## [0.8.3]
### Features
- ping: подписи TCP vs туннель, warm-up для HTTP-probe


## [0.8.2]
### Features
- плавные вкладки, прозрачный scroll, статус «Не проверено»
- подписи: Подключение / Замена конфигурации


## [0.8.1]
### Features
- motion redesign + versioning с <code>0.1.0</code>


## [0.8.0]
### Features
- Настройки: пользователи (admin), xttp статус / ping / speedtest
- замена ноды по <code>vless://</code> + backup/rollback


## [0.7.5]
### Features
- прозрачный scrollbar в changelog dropdown


## [0.7.4]
### Features
- ссылка <strong>MetaCube</strong>
- changelog dropdown по версии (как MetaCube)


## [0.7.3]
### Features
- брендинг <strong>xttp panel</strong>
- автоскрытие toast


## [0.7.2]
### Bug Fixes
- чистый URL после logout
- <code>replaceState</code> на логине


## [0.7.1]
### Features
- полноэкранный mesh-фон логина


## [0.7.0]
### Features
- своя форма входа (вместо Basic Auth)
- cookie-сессия 7 дней


## [0.6.4]
### Features
- удаление/toggle группы → apply + restart mihomo


## [0.6.3]
### Bug Fixes
- PRG-редирект: URL не залипает на action-path


## [0.6.2]
### Features
- поиск по всем группам (имя + паттерны)


## [0.6.1]
### Features
- RuleSet имена: <code>g_Telegram</code>…
- rules: GEOIP private + groups + MATCH


## [0.6.0]
### Features
- «+» в шапке группы, AJAX add/delete
- авто-detect IPV4 / NAMESPACE


## [0.5.0]
### Features
- группы свёрнуты по умолчанию
- убран PROXY-бейдж, без RAW-подписок


## [0.4.0]
### Features
- карточки групп MagiTrickle-style
- импорт списка с превью типов


## [0.3.0]
### Features
- модель групп + <code>groups.json</code>
- classical rule-providers


## [0.2.0]
### Features
- тёмная тема UI
- ручные правила + remote lists


## [0.1.0]
### Features
- первый lists UI для mihomo gateway
- apply + restart · listed → PROXY, else DIRECT


