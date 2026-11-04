import {$,$$,main,esc,request,post,action,status,confirmAction,setTitle,scopedPoll,bytes,time,kv,session,guardNavigation} from './app.js';
const yes = value => value == null ? 'Неизвестно' : value ? 'Да' : 'Нет';
const checkbox = (name,label) => `<label class="check"><input type="checkbox" name="${name}">${label}</label>`;
const select = (name,label,values) => `<label>${label}<select name="${name}">${values.map(v=>`<option value="${v}">${v}</option>`).join('')}</select></label>`;
const feedback = () => $('#system-status');
function stale(node,error){if(error.name!=='AbortError')status(node,`Не удалось обновить данные: ${error.message}. Предыдущие значения могут быть устаревшими.`,'error');}
async function core(){
  setTitle('Ядро','Runtime-настройки mihomo. Порты и TUN показаны из работающего ядра.');
  main.innerHTML=`<section class="surface"><div class="toolbar"><h2>Работающее ядро</h2><button type="button" id="core-refresh">Обновить</button></div><p id="system-status" class="status">Загрузка…</p><form id="core-form"><fieldset id="core-fields" disabled><legend>Настройки runtime</legend><div class="form-grid">${select('mode','Режим',['rule','global','direct'])}${select('log-level','Уровень журнала',['silent','error','warning','info','debug'])}${select('find-process-mode','Поиск процесса',['off','strict','always'])}${checkbox('allow-lan','Разрешить LAN (allow-lan)')}${checkbox('ipv6','IPv6')}${checkbox('unified-delay','Единый расчёт задержки (unified-delay)')}${checkbox('tcp-concurrent','Параллельные TCP-подключения')}${checkbox('sniffing','Определение назначения (sniffing)')}${checkbox('geo-auto-update','Автообновление geo-баз')}<label>Интервал geo, часы<input type="number" name="geo-update-interval" min="1" max="168" value="24" required></label></div><div class="actions"><button type="submit" class="primary">Применить runtime-настройки</button></div></fieldset></form><p class="muted">Это настройки работающего ядра; они не сохраняют черновик правил. После перезапуска конфигурация определяется серверным файлом.</p></section><section class="surface"><h2>Порты</h2><div id="core-ports"><p class="loading">Загрузка…</p></div></section><section class="surface"><h2>TUN</h2><p class="muted">Текущие значения доступны только для чтения; изменение TUN требует серверной конфигурации.</p><div id="core-tun"></div></section><section class="surface"><h2>Geo-базы</h2><div id="geo-sources"></div><div class="actions"><button type="button" id="geo-update">Обновить geo-базы сейчас</button></div><p id="geo-status" class="status"></p></section>`;
  let changed=false;guardNavigation(()=>changed);
  function populate(data){const c=data.configs||{},form=$('#core-form');for(const input of $$('input,select',form)){if(input.type==='checkbox')input.checked=!!c[input.name];else if(c[input.name]!=null)input.value=String(c[input.name]);}$('#core-fields').disabled=false;changed=false;$('#core-ports').innerHTML=kv(Object.entries(data.ports||{}).map(([key,value])=>[key,Number(value)>0?value:'Выключен']));const tun=data.tun||c.tun||{};$('#core-tun').innerHTML=kv([['Включён',yes(tun.enable)],['Устройство',tun.device],['Стек',tun.stack],['Автомаршрут',yes(tun['auto-route'])],['Авто-редирект',yes(tun['auto-redirect'])],['IPv4',Array.isArray(tun['inet4-address'])?tun['inet4-address'].join(', '):tun['inet4-address']],['IPv6',Array.isArray(tun['inet6-address'])?tun['inet6-address'].join(', '):tun['inet6-address']],['MTU',tun.mtu]]);$('#geo-sources').innerHTML=kv(Object.entries(data.geox_url||c['geox-url']||{}));$('#geo-update').disabled=data.geo_update_api===false;status(feedback(),`Получено из mihomo · ${new Date().toLocaleTimeString('ru-RU')}`);}
  async function load() {
    const fields=$('#core-fields'),previous=fields.disabled;
    fields.disabled=true;
    try { populate(await request('/api/core/configs')); }
    catch(error) { fields.disabled=previous; throw error; }
  }
  $('#core-form').addEventListener('input',()=>{changed=true;status(feedback(),'Runtime-настройки изменены, но ещё не применены.','warning');});
  $('#core-refresh').addEventListener('click',event=>action(event.currentTarget,async()=>{if(changed&&!await confirmAction('Обновить настройки','Несохранённые значения формы будут потеряны. Загрузить из ядра?','Загрузить'))return;await load();},feedback()));
  $('#core-form').addEventListener('submit',event=>{
    event.preventDefault();
    const form=event.currentTarget;
    action($('button[type=submit]',form),async()=>{
      if(!await confirmAction('Применить runtime','Изменить настройки работающего mihomo? Изменение LAN может повлиять на доступ.','Применить'))return;
      const data={};
      for(const input of $$('input,select',form))data[input.name]=input.type==='checkbox'?(input.checked?'true':'false'):input.value;
      $('#core-fields').disabled=true;
      try {
        populate(await post('/api/core/configs',data));
        status(feedback(),'Runtime-настройки применены и перечитаны из ядра.','success');
      } finally { $('#core-fields').disabled=false; }
    },feedback());
  });
  $('#geo-update').addEventListener('click',event=>action(event.currentTarget,async()=>{if(!await confirmAction('Обновить geo-базы','Загрузить актуальные geoip/geosite через mihomo?','Обновить'))return;status($('#geo-status'),'Загрузка geo-баз…');const data=await post('/api/core/geo-update');status($('#geo-status'),data.message||'Geo-базы обновлены.','success');},$('#geo-status')));
  try{await load();}catch(error){stale(feedback(),error);}
}
async function connection(){
  setTitle('Подключение','Проверка xray и SOCKS, VLESS и восстановление сохранённых конфигураций.');
  main.innerHTML=`<section class="surface"><div class="toolbar"><h2>Состояние подключения</h2><button type="button" id="connection-refresh">Обновить</button></div><p id="system-status" class="status">Загрузка…</p><div id="connection-status"></div><div class="actions"><button type="button" id="connection-ping">Проверить соединение</button><button type="button" id="speedtest">Измерить скорость</button></div><p class="muted">Проверка выполняет TCP и HTTP через SOCKS. Тест скорости скачивает около 5 МБ через xray.</p><div id="probe-result" class="status"></div><div id="speed-result" class="status"></div><p id="auto-ping-info" class="muted"></p></section><section class="surface"><h2>VLESS-конфигурация</h2><p class="muted">Ссылка содержит секрет. Не передавайте её другим. Превью ничего не устанавливает; применение проверяет текущую ссылку и перезапускает xray.</p><form id="vless-form" class="form-stack"><label>Текущая VLESS-ссылка<textarea id="vless" name="vless" required spellcheck="false" autocomplete="off" placeholder="vless://…"></textarea></label><div class="actions"><button type="button" id="vless-preview">Проверить превью</button><button type="submit" class="primary">Применить текущую ссылку</button></div></form><p id="vless-status" class="status"></p><div id="vless-result"></div></section><section class="surface"><div class="toolbar"><h2>Резервные копии xray</h2><button type="button" id="backups-refresh">Обновить список</button></div><p class="muted">Восстановление заменит конфигурацию и перезапустит xray.</p><div id="backups"></div><p id="backup-status" class="status"></p></section>`;
  let previewSerial=0,vlessDirty=false;guardNavigation(()=>vlessDirty);
  function showStatus(data) {
    const o=data.outbound||{};
    $('#connection-status').innerHTML=kv([
      ['xray',data.xray_active?'Работает':'Остановлен'],
      ['mihomo',data.mihomo_active?'Работает':'Остановлен'],
      ['Endpoint',o.address?`${o.address}:${o.port}`:'—'],
      ['Протокол',o.protocol],['Транспорт',o.network],['Безопасность',o.security],
      ['UUID',o.uuid_short],['SNI',o.sni],['Public key',o.public_key_short],
      ['shortId',o.short_id],['Path / mode',`${o.path||'—'} / ${o.mode||'—'}`],
      ['SOCKS',data.socks],['Цепочка mihomo',data.mihomo_proxy]
    ]);
    status(feedback(),`Состояние получено ${new Date().toLocaleTimeString('ru-RU')}`);
  }
  async function loadStatus(signal){const data=await request('/api/xttp/status',{signal});if(!signal?.aborted)showStatus(data);}
  async function loadBackups(){const data=await request('/api/xttp/backups');$('#backups').innerHTML=(data.backups||[]).map(name=>`<div class="connection-head connection-item"><span>${esc(name)}</span><button type="button" data-restore="${esc(name)}">Восстановить</button></div>`).join('')||'<p class="empty">Резервных копий пока нет.</p>';}
  async function ping(signal){const data=await post('/api/xttp/ping',{}, {signal});if(signal?.aborted)return;$('#probe-result').className=`status ${data.up?'success':'error'}`;$('#probe-result').innerHTML=`<h3>${data.up?'Соединение доступно':'Проверка соединения не пройдена'}</h3>${kv([['Время',new Date().toLocaleTimeString('ru-RU')],['Endpoint',data.endpoint],['TCP',data.tcp?.ok?`${data.tcp.latency_ms??'—'} мс`:data.tcp?.error||'Ошибка'],['HTTP через SOCKS',data.http?.ok?`${data.http.latency_ms??data.http.ms??'—'} мс`:data.http?.error||'Ошибка'],['xray',yes(data.xray_active)],['mihomo',yes(data.mihomo_active)]])}`;}
  $('#connection-refresh').addEventListener('click',event=>action(event.currentTarget,loadStatus,feedback()));$('#backups-refresh').addEventListener('click',event=>action(event.currentTarget,loadBackups,$('#backup-status')));
  $('#connection-ping').addEventListener('click',event=>action(event.currentTarget,()=>ping(),$('#probe-result')));
  $('#speedtest').addEventListener('click',event=>action(event.currentTarget,async()=>{status($('#speed-result'),'Измерение скорости…');const data=await post('/api/xttp/speedtest');$('#speed-result').innerHTML=kv([['Скорость',`${data.mbps} Мбит/с`],['Объём',bytes(data.bytes)],['Длительность',`${data.seconds} с`],['HTTP',data.http_code]]);},$('#speed-result')));
  $('#vless').addEventListener('input',()=>{previewSerial++;vlessDirty=!!$('#vless').value;$('#vless-result').replaceChildren();status($('#vless-status'),'Ссылка изменена. Превью больше не актуально.');});
  $('#vless-preview').addEventListener('click',event=>action(event.currentTarget,async()=>{
    const serial=++previewSerial,source=$('#vless').value;
    let data;
    try { data=await post('/api/xttp/preview',{vless:source}); }
    catch(error) { if(serial!==previewSerial)return; throw error; }
    if(serial!==previewSerial)return;
    $('#vless-result').innerHTML=kv(Object.entries(data.preview||{}));
    status($('#vless-status'),'Превью текущей ссылки. Конфигурация не изменена.');
  },$('#vless-status')));
  $('#vless-form').addEventListener('submit',event=>{event.preventDefault();action($('button[type=submit]',event.currentTarget),async()=>{if(!await confirmAction('Применить VLESS','Проверить и применить текущую ссылку с перезапуском xray? Работающее соединение изменится.','Применить'))return;const source=$('#vless').value;status($('#vless-status'),'Проверка и активация конфигурации…');const data=await post('/api/xttp/apply',{vless:source,confirm:1});if(data.http_ok===false||data.http?.ok===false){status($('#vless-status'),'Конфигурация записана, но проверка HTTP не пройдена. Соединение не подтверждено.','error');}else{status($('#vless-status'),`Конфигурация применена${data.endpoint?' · '+data.endpoint:''}${data.backup?' · резервная копия '+data.backup:''}`,'success');if($('#vless').value===source){$('#vless').value='';vlessDirty=false;}}await loadStatus();await loadBackups();},$('#vless-status'));});
  $('#backups').addEventListener('click',event=>{
    const button=event.target.closest('[data-restore]');
    if(!button)return;
    action(button,async()=>{
      if(!await confirmAction('Восстановить xray',`Применить резервную копию ${button.dataset.restore} и перезапустить xray?`,'Восстановить'))return;
      const data=await post('/api/xttp/rollback',{name:button.dataset.restore,confirm:1});
      const ready=data.http?.ok;
      status($('#backup-status'),ready===true?'Резервная копия восстановлена; HTTP-проверка пройдена.':
        ready===false?'Восстановление выполнено, но HTTP-проверка не пройдена.':
        'Резервная копия восстановлена. Результат HTTP-проверки неизвестен; проверьте соединение.',
        ready===true?'success':ready===false?'error':'warning');
      await loadStatus();
    },$('#backup-status'));
  });
  await Promise.all([loadStatus().catch(error=>stale(feedback(),error)),loadBackups().catch(error=>stale($('#backup-status'),error))]);
  try{const data=await request('/api/prefs');const prefs=data.prefs||{};$('#auto-ping-info').textContent=prefs.auto_ping?`Автопроверка каждые ${prefs.auto_ping_sec} с, только пока эта страница видима. Изменить — в «Операции».`:'Автопроверка выключена. Настройка — в «Операции».';if(prefs.auto_ping)scopedPoll(async signal=>{try{await ping(signal);}catch(error){stale($('#probe-result'),error);}},Math.max(15,Number(prefs.auto_ping_sec)||45)*1000,{immediate:false});}catch(error){$('#auto-ping-info').textContent=`Настройки автопроверки недоступны: ${error.message}`;}
}
async function users(){
  setTitle('Пользователи','Все пользователи имеют права администратора. Изменение пароля завершает существующие сессии.');
  main.innerHTML=`<section class="surface"><div class="toolbar"><h2>Учётные записи</h2><button type="button" id="users-refresh">Обновить</button></div><p id="system-status" class="status">Загрузка…</p><div id="users"></div></section><section class="surface"><h2>Добавить пользователя</h2><form id="user-add" class="form-stack"><div class="form-grid"><label>Имя пользователя<input name="username" pattern="[A-Za-z0-9_.\\-]{2,32}" minlength="2" maxlength="32" autocomplete="off" required></label><label>Пароль, минимум 6 символов<input name="password" type="password" minlength="6" autocomplete="new-password" required></label></div><p class="muted">Допустимы латинские буквы, цифры, точка, дефис и подчёркивание.</p><button type="submit" class="primary">Создать пользователя</button></form></section>`;
  let accounts=[];
  async function load(){const data=await request('/api/users');accounts=data.users||[];$('#users').innerHTML=accounts.map((u,index)=>{const self=u.username===(data.me||session.user?.username);return `<article class="user-item" data-user="${esc(u.id)}"><h3>${esc(u.username)}${self?' (вы)':''}</h3><p class="muted">${u.enabled?'Активен':'Заблокирован'} · Последний вход: ${esc(time(u.last_login))}</p><div class="actions"><button type="button" data-toggle${self?' disabled':''}>${u.enabled?'Заблокировать':'Разблокировать'}</button><button type="button" data-delete class="danger"${self?' disabled':''}>Удалить</button></div><details><summary>Изменить пароль ${esc(u.username)}</summary><form data-password class="form-stack"><label for="password-${index}">Новый пароль, минимум 6 символов</label><input id="password-${index}" name="password" type="password" minlength="6" autocomplete="new-password" required><p class="muted">${self?'После сохранения потребуется войти заново.':'Все сессии этого пользователя будут завершены.'}</p><button type="submit">Сохранить новый пароль</button></form></details></article>`;}).join('')||'<p class="empty">Пользователи не найдены.</p>';status(feedback(),`Получено ${accounts.length} учётных записей.`);}
  $('#users-refresh').addEventListener('click',event=>action(event.currentTarget,load,feedback()));
  $('#user-add').addEventListener('submit',event=>{event.preventDefault();const form=event.currentTarget;action($('button',form),async()=>{await post('/api/users/add',Object.fromEntries(new FormData(form)));form.reset();await load();status(feedback(),'Пользователь создан.','success');},feedback());});
  $('#users').addEventListener('click',event=>{const button=event.target.closest('[data-toggle],[data-delete]');if(!button)return;const u=accounts.find(item=>item.id===button.closest('[data-user]').dataset.user),remove=button.hasAttribute('data-delete');action(button,async()=>{if(!await confirmAction(remove?'Удалить пользователя':u.enabled?'Заблокировать пользователя':'Разблокировать пользователя',`${remove?'Удалить':u.enabled?'Заблокировать':'Разблокировать'} ${u.username}?${remove||u.enabled?' Его сессии будут завершены.':''}`,remove?'Удалить':'Подтвердить'))return;await post(`/api/users/${remove?'delete':'toggle'}`,{id:u.id});await load();},feedback());});
  $('#users').addEventListener('submit',event=>{const form=event.target.closest('[data-password]');if(!form)return;event.preventDefault();const id=form.closest('[data-user]').dataset.user;action($('button',form),async()=>{if(!await confirmAction('Сменить пароль','Сохранить новый пароль и завершить прежние сессии пользователя?','Сохранить'))return;const data=await post('/api/users/password',{id,password:new FormData(form).get('password')});form.reset();if(data.relogin){guardNavigation(()=>false);location.assign('/login');return;}status(feedback(),'Пароль изменён, прежние сессии завершены.','success');},feedback());});
  try{await load();}catch(error){stale(feedback(),error);}
}
async function activity(){
  setTitle('Операции','Сервисы, автопроверка подключения и журнал действий панели.');
  main.innerHTML=`<section class="surface"><div class="toolbar"><h2>Сервисы</h2><button type="button" id="services-refresh">Обновить</button></div><p id="system-status" class="status">Загрузка…</p><div id="services"></div><div class="actions"><button type="button" data-restart="mihomo">Перезапустить mihomo</button><button type="button" data-restart="xray">Перезапустить xray</button><button type="button" id="logout-mobile">Выйти из панели</button></div></section><section class="surface"><h2>Автопроверка подключения</h2><form id="prefs-form" class="form-stack"><label class="check"><input type="checkbox" name="auto_ping">Проверять подключение автоматически</label><label>Интервал, секунды<input name="auto_ping_sec" type="number" min="15" max="300" required value="45"></label><p class="muted">Проверки выполняются на странице «Подключение», только когда она видима. Это не политика системного таймера обновлений.</p><button type="submit">Сохранить предпочтения</button></form><p id="prefs-status" class="status"></p></section><section class="surface"><div class="toolbar"><h2>Журнал действий</h2><button type="button" id="activity-refresh">Обновить журнал</button></div><p id="activity-status" class="status"></p><ol id="activity" class="activity-list"></ol></section>`;
  let known='';
  async function services(signal){const data=await request('/api/services/status',{signal});if(signal?.aborted)return;$('#services').innerHTML=kv([['mihomo',data.mihomo?'Работает':'Остановлен'],['xray',data.xray?'Работает':'Остановлен'],['Панель',data.lists?'Работает':'Неизвестно']]);status(feedback(),`Обновлено ${new Date().toLocaleTimeString('ru-RU')}`);}
  async function journal(signal){const data=await request('/api/activity',{signal});if(signal?.aborted)return;const signature=JSON.stringify(data.items);if(signature!==known){$('#activity').innerHTML=(data.items||[]).map(item=>`<li><small>${esc(time(item.ts||item.time||item.at))} · ${esc(item.user||item.actor||'system')} · ${item.ok===false?'Ошибка':'Событие'}</small><strong>${esc(item.action||item.event||'')}</strong><p>${esc(item.detail||item.message||'')}</p></li>`).join('')||'<li class="empty">Действий пока нет.</li>';known=signature;}status($('#activity-status'),`Обновлено ${new Date().toLocaleTimeString('ru-RU')}`);}
  const poll=scopedPoll(async signal=>{await Promise.all([services(signal).catch(error=>stale(feedback(),error)),journal(signal).catch(error=>stale($('#activity-status'),error))]);},10000);
  $('#services-refresh').addEventListener('click',event=>action(event.currentTarget,()=>services(),feedback()));$('#activity-refresh').addEventListener('click',event=>action(event.currentTarget,()=>journal(),$('#activity-status')));
  $$('[data-restart]').forEach(button=>button.addEventListener('click',()=>action(button,async()=>{if(!await confirmAction('Перезапустить сервис',`Перезапустить ${button.dataset.restart}? Текущие соединения могут прерваться.`,'Перезапустить'))return;await post('/api/services/restart',{service:button.dataset.restart,confirm:1});await services();await journal();},feedback())));
  $('#logout-mobile').addEventListener('click',event=>action(event.currentTarget,async()=>{if(!await confirmAction('Выйти из панели','Завершить текущую сессию?','Выйти'))return;await post('/logout');location.assign('/login');},feedback()));
  const prefsForm=$('#prefs-form');$('#prefs-form').addEventListener('submit',event=>{event.preventDefault();action($('button',prefsForm),async()=>{await post('/api/prefs/set',{auto_ping:prefsForm.elements.auto_ping.checked?'1':'0',auto_ping_sec:prefsForm.elements.auto_ping_sec.value});status($('#prefs-status'),'Предпочтения сохранены. Новая политика действует при следующем открытии страницы «Подключение».','success');},$('#prefs-status'));});
  try{const data=await request('/api/prefs');prefsForm.elements.auto_ping.checked=!!data.prefs?.auto_ping;prefsForm.elements.auto_ping_sec.value=data.prefs?.auto_ping_sec||45;}catch(error){stale($('#prefs-status'),error);}
}
async function updates(){
  setTitle('Обновления','Проверка ничего не устанавливает. Каждое обновление запускается только после подтверждения.');
  main.innerHTML=`<section class="surface"><div class="toolbar"><h2>Компоненты</h2><button type="button" id="versions-refresh">Обновить версии</button></div><p id="system-status" class="status">Загрузка версий…</p><p id="source-mode" class="muted"></p><p id="timer-policy" class="notice">Политика timer пока неизвестна.</p><div id="versions"></div><div class="actions"><button type="button" id="fleet-update">Проверить и обновить весь стек</button></div><p class="muted">Обновление панели не применяет правила. Ядра обновляются отдельными заданиями. Существующая политика timer не меняется этой страницей.</p></section><section class="surface"><h2>Задание обновления</h2><p class="muted">Идентификатор и конечный результат сохраняются на сервере и доступны после перезапуска панели.</p><form id="operation-lookup" class="toolbar"><label>Идентификатор задания<input name="id" id="operation-id" required autocomplete="off"></label><button type="submit">Открыть задание</button></form><div id="operation" class="operation"><p class="empty">Задание не выбрано.</p></div></section><section class="surface"><h2>История изменений панели</h2><button type="button" id="changelog-load">Загрузить журнал версий</button><p id="changelog-status" class="status"></p><div id="changelog"></div></section>`;
  const remote=session.capabilities?.remote_panel_update===true;$('#source-mode').textContent=remote?'Источник панели: Git. Доступно управляемое обновление.':'Источник панели: local-only. Удалённое обновление панели недоступно; локальный исходный код нужно доставить вручную.';
  let versions = {}, operationPoll, operationData, lookupGeneration = 0;
  const checks = new Map(), checking = new Set();
  const labels = {panel:'Панель',mihomo:'mihomo',xray:'xray'};
  function renderVersions() {
    for (const component of ['panel','mihomo','xray']) {
      let row = $(`[data-component="${component}"]`);
      if (!row) {
        row = document.createElement('article');
        row.className = 'version-row'; row.dataset.component = component;
        row.innerHTML = `<h3>${labels[component]} <span data-version class="muted"></span></h3>
          <p data-version-error class="muted"></p>
          <div class="actions"><button type="button" data-check="${component}">Проверить обновление</button>
          <button type="button" data-install="${component}" class="primary" disabled>Обновить</button></div>
          <p data-check-status class="status"></p>
          <details data-notes hidden><summary>Примечания выпуска</summary><pre class="notes"></pre></details>`;
        $('#versions').append(row);
      }
      const version = versions[component] || {}, check = checks.get(component);
      $('[data-version]',row).textContent = version.version || version.display || 'Версия неизвестна';
      $('[data-version-error]',row).textContent = version.error || '';
      $('[data-check]',row).disabled = checking.has(component);
      const install = $('[data-install]',row);
      install.disabled = checking.has(component) || !check?.update_available || check?.manual_only || component === 'panel' && !remote;
      install.textContent = check?.latest ? `Обновить до ${check.latest}` : 'Обновить';
      const message = checking.has(component) ? 'Проверяется…' : !check ? 'Проверка ещё не выполнена.' :
        check.error || (check.update_available ? `Доступна ${check.latest} · установлена ${check.current}` :
          check.up_to_date ? `Актуальная версия ${check.current}` : `Проверено: ${check.current || 'неизвестно'} → ${check.latest || 'неизвестно'}`);
      status($('[data-check-status]',row),message,check?.error ? 'error' : '');
      $('[data-notes]',row).hidden = !check?.notes;
      $('pre',row).textContent = check?.notes || '';
    }
  }
  async function loadVersions() {
    const data = await request('/api/core/versions?force=1');
    versions = data; renderVersions();
    const timer = data.fleet_timer;
    $('#timer-policy').textContent = timer ?
      `Системный timer: автозапуск ${timer.enabled == null ? 'неизвестен' : timer.enabled ? 'включён' : 'выключен'} · состояние ${timer.active == null ? 'неизвестно' : timer.active ? 'активен' : 'неактивен'}${timer.error ? ' · '+timer.error : ''}. Политика не изменена.` :
      'Состояние системного timer недоступно. Политика не изменена.';
    status(feedback(),`Версии получены ${new Date().toLocaleTimeString('ru-RU')}`);
  }
  async function check(component) {
    if (checking.has(component)) return;
    checking.add(component); renderVersions();
    try {
      const data = await request(`/api/core/versions/check?component=${component}&force=1`);
      checks.set(component,data); return data;
    } catch (error) {
      checks.set(component,{error:error.message}); throw error;
    } finally { checking.delete(component); renderVersions(); }
  }
  function renderOperation(op){
    const names={queued:'Принято, ожидает запуска',running:'Выполняется',succeeded:'Завершено успешно',failed:'Завершено с ошибкой'};
    const rollback=op.rollback;let rollbackText='Неизвестно';
    if(typeof rollback==='string')rollbackText=({not_attempted:'Не выполнялось',succeeded:'Предыдущая версия восстановлена',failed:'Восстановление не удалось'})[rollback]||rollback;
    else if(rollback)rollbackText=JSON.stringify(rollback);
    $('#operation').innerHTML=`<h3>${esc(names[op.status]||op.status)}</h3>${op.status==='queued'||op.status==='running'?'<progress aria-label="Задание выполняется"></progress>':''}${kv([['Идентификатор',op.id],['Компонент',op.component],['Этап',op.stage],['Создано',time(op.created_at)],['Обновлено',time(op.updated_at)],['Восстановление',rollbackText]])}${op.error?`<p class="status error" role="alert">${esc(op.error)}</p>`:''}${op.result!=null?`<h3>Фактический результат</h3><pre>${esc(typeof op.result==='string'?op.result:JSON.stringify(op.result,null,2))}</pre>`:''}<p class="status ${op.status==='failed'?'error':op.status==='succeeded'?'success':''}">${op.status==='succeeded'?'Сервер подтвердил конечный результат.':op.status==='failed'?'Задание завершилось с ошибкой. Результат восстановления показан отдельно.':'Принятие задания не означает успех. Ожидание конечного результата.'}</p>`;
  }
  function watch(id){
    operationPoll?.stop(); operationData=undefined; const generation=++lookupGeneration; $('#operation-id').value=id;
    try{localStorage.setItem('xttp-operation-id',id);}catch{}
    const url=new URL(location.href);url.searchParams.set('operation',id);history.replaceState(null,'',url);
    operationPoll=scopedPoll(async signal=>{
      try {
        const data=await request(`/api/operations/${encodeURIComponent(id)}`,{signal});
        if(signal.aborted||generation!==lookupGeneration)return;
        operationData=data.operation; renderOperation(operationData);
        if(['succeeded','failed'].includes(operationData.status)){
          operationPoll?.stop(); loadVersions().catch(error=>stale(feedback(),error));
        }
      } catch(error) {
        if(error.name==='AbortError'||generation!==lookupGeneration)return;
        if(error.status===404){
          operationPoll?.stop();
          $('#operation').innerHTML=`<p class="status error">Задание ${esc(id)} не найдено. Проверьте идентификатор и повторите запрос.</p>`;
          return;
        }
        const message=`Связь с панелью потеряна: ${error.message}. Это не результат обновления. Ожидание восстановления API; задание ${id} сохранено.`;
        if(!operationData)$('#operation').innerHTML=`<p class="status warning">${esc(message)}</p>`;
        else {renderOperation(operationData);$('#operation').insertAdjacentHTML('beforeend',`<p class="status warning">${esc(message)}</p>`);}
      }
    },2000);
  }
  async function start(component) {
    if(component==='panel'&&!remote)throw new Error('Удалённое обновление панели недоступно для local-only источника.');
    const target = component === 'fleet' ? '' : checks.get(component)?.latest || '';
    if(!await confirmAction('Запустить обновление',`Обновить ${component==='fleet'?'весь стек':component}${target?' до '+target:''}? Сервисы могут перезапуститься. Конечный результат появится в задании. Политика timer не изменится.`,'Обновить'))return;
    const data=await post('/api/updates/start',{component,confirm:1,...(target?{target_version:target}:{})});
    status(feedback(),`Задание ${data.operation_id} принято. Обновление ещё не завершено.`);
    watch(data.operation_id); $('#operation').scrollIntoView({block:'nearest'});
  }
  $('#versions-refresh').addEventListener('click',event=>action(event.currentTarget,loadVersions,feedback()));
  $('#versions').addEventListener('click',event=>{
    const button=event.target.closest('[data-check],[data-install]');
    if(!button)return;
    action(button,async()=>{
      const component=button.dataset.check||button.dataset.install;
      if(button.dataset.check)await check(component);
      else {
        if(!checks.get(component)?.update_available)throw new Error('Сначала проверьте доступность обновления.');
        await start(component);
      }
    },feedback());
  });
  $('#fleet-update').disabled=!remote;$('#fleet-update').addEventListener('click',event=>action(event.currentTarget,async()=>{await Promise.all(['panel','mihomo','xray'].map(component=>check(component)));await start('fleet');},feedback()));
  $('#operation-lookup').addEventListener('submit',event=>{event.preventDefault();const id=$('#operation-id').value.trim();if(!/^[A-Za-z0-9_-]{1,128}$/.test(id)){status(feedback(),'Некорректный идентификатор задания.','error');return;}watch(id);});
  $('#changelog-load').addEventListener('click',event=>action(event.currentTarget,async()=>{const data=await request('/api/changelog?force=1');const items=data.items||[];$('#changelog').innerHTML=items.map(item=>`<details><summary>${esc(item.display||item.version)} · ${esc(item.date)}${item.current?' · текущая':''}</summary>${(item.sections||[]).map(section=>`<h3>${esc(section.title)}</h3><ul>${(section.items||[]).map(text=>`<li>${esc(text)}</li>`).join('')}</ul>`).join('')}</details>`).join('')||'<p class="empty">Журнал версий пуст.</p>';status($('#changelog-status'),'Журнал загружен.');},$('#changelog-status')));
  try{await loadVersions();}catch(error){stale(feedback(),error);}
  let restored=new URL(location.href).searchParams.get('operation');if(!restored)try{restored=localStorage.getItem('xttp-operation-id');}catch{}if(restored&&/^[A-Za-z0-9_-]{1,128}$/.test(restored))watch(restored);
  await Promise.allSettled(['panel','mihomo','xray'].map(component=>check(component)));
}
export async function mount(){const page=location.pathname.split('/')[2];if(page==='core')return core();if(page==='connection')return connection();if(page==='users')return users();if(page==='activity')return activity();if(page==='updates')return updates();throw new Error('Страница системы не найдена.');}
