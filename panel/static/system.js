import {main as appMain,esc,request as apiRequest,action as performAction,status,confirmAction,setTitle as setPageTitle,scopedPoll as createPoll,bytes,time,kv,session,hasUnsavedChanges,logout,goToLogin} from './app.js';

export function mount(target = appMain, selectedPage = location.pathname.split('/')[2]) {
const main = target;
const $ = (selector, root = main) => root.querySelector(selector);
const $$ = (selector, root = main) => [...root.querySelectorAll(selector)];
const controller = new AbortController(), polls = [];
let active = true, busy = 0, checkDraft = () => false;
const guardNavigation = check => { checkDraft = check; };
const setTitle = (title, description) => { if (target === appMain) setPageTitle(title, description); };
async function request(path, options = {}) {
  if (!active) throw new DOMException('Страница закрыта', 'AbortError');
  const data = await apiRequest(path, {...options, signal:options.signal || controller.signal});
  if (!active) throw new DOMException('Страница закрыта', 'AbortError');
  return data;
}
const post = (path, data, options = {}) => request(path, {...options, method:'POST', data});
const action = (button, task, output) => performAction(button, async () => {
  busy++;
  try { return await task(); } finally { busy--; }
}, output);
function scopedPoll(task, milliseconds, options = {}) {
  const poll = createPoll(task, milliseconds, {...options, root:main}); polls.push(poll);
  if (!active) poll.stop();
  return poll;
}
const yes = value => value == null ? 'Неизвестно' : value ? 'Да' : 'Нет';
const checkbox = (name,label,help) => `<div class="core-toggle-row"><div><label class="ct-lab" for="core-${name}">${label}</label><div class="ct-sub" id="core-${name}-help">${help}</div></div><label class="switch"><input type="checkbox" id="core-${name}" name="${name}" aria-label="${label}" aria-describedby="core-${name}-help"><span aria-hidden="true"></span></label></div>`;
const select = (name,label,values,help) => `<div class="core-field"><label for="core-${name}">${label}</label><select id="core-${name}" name="${name}" aria-describedby="core-${name}-help">${values.map(v=>`<option value="${v}">${v}</option>`).join('')}</select><div class="ct-sub" id="core-${name}-help">${help}</div></div>`;
const feedback = () => $('#system-status');
function stale(node,error){if(error.name!=='AbortError')status(node,`Не удалось обновить данные: ${error.message}. Предыдущие значения могут быть устаревшими.`,'error');}
async function core(){
  setTitle('Ядро','Runtime-настройки mihomo. Порты и TUN показаны из работающего ядра.');
  main.innerHTML=`<section class="surface">
    <p class="muted settings-intro">Runtime-настройки mihomo. Меняются сразу через API ядра — MetaCube не нужен.</p>
    <p id="system-status" class="status">Загрузка…</p>
    <div id="core-versions" class="core-versions" aria-label="Версии компонентов"></div>
    <p id="core-version-status" class="status"></p>
    <div class="actions"><a href="/system/updates">Проверка и обновление компонентов</a><button type="button" id="core-refresh">Обновить</button></div>
    <div class="core-ports-head"><div class="ct-lab">Порты</div><div class="ct-sub">Только просмотр: 0 или пусто = выключен</div></div>
    <div id="core-ports" class="core-ports" aria-live="polite"><p class="loading">Загрузка портов…</p></div>
    <form id="core-form"><fieldset id="core-fields" class="core-fields" aria-label="Runtime-настройки mihomo" disabled>
      <div class="core-grid">
        ${select('mode','Режим',['rule','global','direct'],'Rule — по правилам. Global — всё в прокси. Direct — всё напрямую.')}
        ${select('log-level','Уровень логов',['silent','error','warning','info','debug'],'Silent — без логов. Error/Warning — только сбои. Info — важное. Debug — максимум деталей.')}
        ${select('find-process-mode','Find process',['off','strict','always'],'Off — не искать процесс. Strict — точно (медленнее). Always — всегда пытаться.')}
      </div>
      <div class="core-toggles">
        ${checkbox('allow-lan','Allow LAN','Разрешить доступ с устройств в локальной сети.')}
        ${checkbox('ipv6','IPv6','Обрабатывать IPv6-трафик в ядре.')}
        ${checkbox('unified-delay','Unified delay','Одинаковый способ замера задержки для нод.')}
        ${checkbox('tcp-concurrent','TCP concurrent','Параллельные TCP-подключения — быстрее, но выше нагрузка.')}
      </div>
      <div class="core-section">
        <h3>TUN-интерфейс</h3>
        <p class="ct-sub">Только просмотр — runtime-статус mihomo. Изменение TUN требует серверной конфигурации.</p>
        <div id="core-tun" aria-live="polite"></div>
      </div>
      <div class="core-toggles">
        ${checkbox('sniffing','Определение протокола (sniffing)','Определяет реальный протокол по содержимому пакета, а не только по порту — повышает точность маршрутизации.')}
        ${checkbox('geo-auto-update','Автообновление geo-баз','Периодически подтягивает свежие geoip/geosite-базы для точной гео-маршрутизации.')}
      </div>
      <div class="core-geo-interval"><label for="core-geo-interval">Интервал обновления geo, часов</label><input id="core-geo-interval" type="number" name="geo-update-interval" min="1" max="168" value="24" required></div>
      <div id="geo-sources" class="core-geo-src"></div>
      <div class="actions"><button type="button" id="geo-update">Обновить geo-базы сейчас</button></div>
      <p id="geo-status" class="status"></p>
      <p class="ct-sub">Это настройки работающего ядра, не черновик правил. После перезапуска конфигурация определяется серверным файлом.</p>
      <div class="actions"><button type="submit" class="primary">Применить</button></div>
    </fieldset></form>
  </section>`;
  let changed=false;guardNavigation(()=>changed);
  function populate(data){
    const c=data.configs||{},form=$('#core-form');
    for(const input of $$('input,select',form)){if(input.type==='checkbox')input.checked=!!c[input.name];else if(c[input.name]!=null)input.value=String(c[input.name]);}
    $('#core-fields').disabled=false;changed=false;
    const portLabels={'port':'HTTP','socks-port':'SOCKS','mixed-port':'Mixed','redir-port':'Redirect','tproxy-port':'TProxy'};
    $('#core-ports').innerHTML=Object.entries(data.ports||{}).map(([key,value])=>`<div class="core-port"><div class="cp-n" title="${esc(key)}">${esc(portLabels[key]||key)}</div><div class="cp-l${Number(value)>0?'':' is-off'}">${Number(value)>0?esc(value):'Выключен'}</div></div>`).join('')||'<p class="muted">Данные о портах отсутствуют.</p>';
    const tun=data.tun||c.tun||{};
    $('#core-tun').innerHTML=kv([['Включён',yes(tun.enable)],['Устройство',tun.device],['Стек',tun.stack],['Автомаршрут',yes(tun['auto-route'])],['Авто-редирект',yes(tun['auto-redirect'])],['IPv4',Array.isArray(tun['inet4-address'])?tun['inet4-address'].join(', '):tun['inet4-address']],['IPv6',Array.isArray(tun['inet6-address'])?tun['inet6-address'].join(', '):tun['inet6-address']],['MTU',tun.mtu]]);
    $('#geo-sources').innerHTML=kv(Object.entries(data.geox_url||c['geox-url']||{}));
    $('#geo-update').disabled=data.geo_update_api===false;
    status(feedback(),`Получено из mihomo · ${new Date().toLocaleTimeString('ru-RU')}`);
  }
  async function loadVersions(){
    const data=await request('/api/core/versions');
    $('#core-versions').innerHTML=[['panel','Панель'],['mihomo','mihomo'],['xray','xray']].map(([key,label])=>{
      const version=data[key]||{};
      return `<a class="core-ver-chip${version.error?' bad':version.ok?' ready':''}" href="/system/updates" title="${esc(version.error||'Открыть проверку и обновление компонентов')}">${label} · ${esc(version.version||version.display||'Версия неизвестна')}</a>`;
    }).join('');
    const errors=['panel','mihomo','xray'].filter(key=>data[key]?.error).map(key=>`${key}: ${data[key].error}`);
    status($('#core-version-status'),errors.join(' · '),errors.length?'warning':'');
  }
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
  await Promise.all([load().catch(error=>stale(feedback(),error)),loadVersions().catch(error=>stale($('#core-version-status'),error))]);
}
async function connection(){
  setTitle('Подключение','Проверка xray и SOCKS, VLESS и восстановление сохранённых конфигураций.');
  main.innerHTML=`<section class="surface">
    <div class="toolbar"><h2>Подключение</h2><button type="button" id="connection-refresh">Обновить</button></div>
    <p id="system-status" class="status">Загрузка…</p>
    <div id="connection-status"></div>
    <div class="metrics connection-metrics" aria-label="Последние измерения подключения">
      <div class="metric"><strong id="connection-tcp">Не измерено</strong><span>TCP до ноды</span></div>
      <div class="metric"><strong id="connection-http">Не измерено</strong><span>Туннель через SOCKS</span></div>
      <div class="metric"><strong id="connection-speed">Не измерено</strong><span>Speedtest</span></div>
    </div>
    <div class="actions"><button type="button" class="primary" id="connection-ping">Проверить связь</button><button type="button" id="speedtest">Speedtest (~5 МБ)</button></div>
    <p class="ct-sub">TCP — только открытие порта ноды. HTTP — полный путь SOCKS → xray → нода → интернет. Speedtest скачивает около 5 МБ через xray.</p>
    <div id="probe-result" class="status"></div><div id="speed-result" class="status"></div>
    <p id="auto-ping-info" class="muted"></p>
    <div class="core-section">
      <h3>Замена конфигурации</h3>
      <p class="muted">Вставьте <code>vless://…</code> — резервная копия, проверка, запись xray и перезапуск. Ссылка содержит секрет: не передавайте её другим. Превью ничего не устанавливает.</p>
      <form id="vless-form" class="form-stack">
        <label for="vless">VLESS-ссылка<textarea id="vless" name="vless" required spellcheck="false" autocomplete="off" placeholder="vless://…"></textarea></label>
        <div class="actions"><button type="button" id="vless-preview">Превью</button><button type="submit" class="primary">Применить текущую ссылку</button></div>
      </form>
      <p id="vless-status" class="status"></p><div id="vless-result"></div>
    </div>
    <div class="core-section">
      <div class="toolbar"><h3>Резервные копии xray</h3><button type="button" id="backups-refresh">Обновить список</button></div>
      <p class="ct-sub">Восстановление заменит конфигурацию и перезапустит xray.</p>
      <div id="backups"></div><p id="backup-status" class="status"></p>
    </div>
  </section>`;
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
  async function ping(signal){const data=await post('/api/xttp/ping',{}, {signal});if(signal?.aborted)return;$('#connection-tcp').textContent=data.tcp?.ok?`${data.tcp.latency_ms??'—'} мс`:'Ошибка';$('#connection-http').textContent=data.http?.ok?`${data.http.latency_ms??data.http.ms??'—'} мс`:'Ошибка';$('#probe-result').className=`status ${data.up?'success':'error'}`;$('#probe-result').innerHTML=`<h3>${data.up?'Соединение доступно':'Проверка соединения не пройдена'}</h3>${kv([['Время',new Date().toLocaleTimeString('ru-RU')],['Endpoint',data.endpoint],['TCP',data.tcp?.ok?`${data.tcp.latency_ms??'—'} мс`:data.tcp?.error||'Ошибка'],['HTTP через SOCKS',data.http?.ok?`${data.http.latency_ms??data.http.ms??'—'} мс`:data.http?.error||'Ошибка'],['xray',yes(data.xray_active)],['mihomo',yes(data.mihomo_active)]])}`;}
  $('#connection-refresh').addEventListener('click',event=>action(event.currentTarget,loadStatus,feedback()));$('#backups-refresh').addEventListener('click',event=>action(event.currentTarget,loadBackups,$('#backup-status')));
  $('#connection-ping').addEventListener('click',event=>action(event.currentTarget,()=>ping(),$('#probe-result')));
  $('#speedtest').addEventListener('click',event=>action(event.currentTarget,async()=>{status($('#speed-result'),'Измерение скорости…');const data=await post('/api/xttp/speedtest');$('#connection-speed').textContent=`${data.mbps} Мбит/с`;$('#speed-result').innerHTML=kv([['Скорость',`${data.mbps} Мбит/с`],['Объём',bytes(data.bytes)],['Длительность',`${data.seconds} с`],['HTTP',data.http_code]]);},$('#speed-result')));
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
  main.innerHTML=`<section class="surface">
    <p class="muted settings-intro">Все пользователи — администраторы. Нельзя заблокировать или удалить себя и последнего администратора.</p>
    <div class="toolbar"><h2>Учётные записи</h2><button type="button" id="users-refresh">Обновить</button></div>
    <p id="system-status" class="status">Загрузка…</p><div id="users"></div>
    <div class="core-section">
      <h3>Добавить пользователя</h3>
      <form id="user-add" class="form-stack">
        <div class="form-grid"><label>Логин<input name="username" pattern="[A-Za-z0-9_.\\-]{2,32}" minlength="2" maxlength="32" autocomplete="off" required placeholder="operator"></label><label>Пароль<input name="password" type="password" minlength="6" autocomplete="new-password" required placeholder="Минимум 6 символов"></label></div>
        <p class="ct-sub">Логин: 2–32 символа — латинские буквы, цифры, точка, дефис или подчёркивание.</p>
        <div class="actions"><button type="submit" class="primary">Создать пользователя</button></div>
      </form>
    </div>
  </section>`;
  let accounts=[];
  async function load(){const data=await request('/api/users');accounts=data.users||[];$('#users').innerHTML=accounts.map((u,index)=>{const self=u.username===(data.me||session.user?.username);return `<article class="user-item" data-user="${esc(u.id)}"><div class="connection-head user-row"><div><h3>${esc(u.username)}${self?' (вы)':''}</h3><p class="muted">${u.enabled?'Активен':'Заблокирован'} · Вход: ${esc(time(u.last_login))}</p></div><div class="actions"><button type="button" data-toggle${self?' disabled':''}>${u.enabled?'Заблокировать':'Разблокировать'}</button><button type="button" data-delete class="danger"${self?' disabled':''}>Удалить</button></div></div><details><summary>Изменить пароль ${esc(u.username)}</summary><form data-password class="form-stack"><div class="form-grid"><label for="password-${index}">Новый пароль<input id="password-${index}" name="password" type="password" minlength="6" autocomplete="new-password" placeholder="Минимум 6 символов" required></label><div class="actions"><button type="submit">Сохранить пароль</button></div></div><p class="ct-sub">${self?'После сохранения потребуется войти заново.':'Все сессии этого пользователя будут завершены.'}</p></form></details></article>`;}).join('')||'<p class="empty">Пользователи не найдены.</p>';status(feedback(),`Получено ${accounts.length} учётных записей.`);}
  $('#users-refresh').addEventListener('click',event=>action(event.currentTarget,load,feedback()));
  $('#user-add').addEventListener('submit',event=>{event.preventDefault();const form=event.currentTarget;action($('button',form),async()=>{await post('/api/users/add',Object.fromEntries(new FormData(form)));form.reset();await load();status(feedback(),'Пользователь создан.','success');},feedback());});
  $('#users').addEventListener('click',event=>{const button=event.target.closest('[data-toggle],[data-delete]');if(!button)return;const u=accounts.find(item=>item.id===button.closest('[data-user]').dataset.user),remove=button.hasAttribute('data-delete');action(button,async()=>{if(!await confirmAction(remove?'Удалить пользователя':u.enabled?'Заблокировать пользователя':'Разблокировать пользователя',`${remove?'Удалить':u.enabled?'Заблокировать':'Разблокировать'} ${u.username}?${remove||u.enabled?' Его сессии будут завершены.':''}`,remove?'Удалить':'Подтвердить'))return;await post(`/api/users/${remove?'delete':'toggle'}`,{id:u.id});await load();},feedback());});
  $('#users').addEventListener('submit',event=>{const form=event.target.closest('[data-password]');if(!form)return;event.preventDefault();const id=form.closest('[data-user]').dataset.user;action($('button',form),async()=>{const warning=id===session.user?.id&&hasUnsavedChanges()?' Несохранённые изменения в правилах или настройках будут потеряны.':'';if(!await confirmAction('Сменить пароль','Сохранить новый пароль и завершить прежние сессии пользователя?'+warning,'Сохранить'))return;const data=await post('/api/users/password',{id,password:new FormData(form).get('password')});form.reset();if(data.relogin){guardNavigation(()=>false);goToLogin();return;}status(feedback(),'Пароль изменён, прежние сессии завершены.','success');},feedback());});
  try{await load();}catch(error){stale(feedback(),error);}
}
async function activity(){
  setTitle('Операции','Сервисы, автопроверка подключения и журнал действий панели.');
  main.innerHTML=`<section class="surface">
    <p class="muted settings-intro">Сервисы, auto-ping и журнал действий: apply, restart, VLESS, пользователи.</p>
    <div class="toolbar"><h2>Сервисы</h2><button type="button" id="services-refresh">Обновить</button></div>
    <p id="system-status" class="status">Загрузка…</p><div id="services" class="metrics"></div>
    <div class="actions"><button type="button" data-restart="xray">Restart xray</button><button type="button" data-restart="mihomo">Restart mihomo</button><button type="button" id="logout-mobile">Выйти из панели</button></div>
    <div class="core-section">
      <form id="prefs-form" class="form-stack">
        ${checkbox('auto_ping','Auto-ping ноды','Проверять подключение автоматически на странице «Подключение», только пока она видима.')}
        <div class="core-geo-interval"><label for="auto-ping-sec">Интервал, секунд</label><input id="auto-ping-sec" name="auto_ping_sec" type="number" min="15" max="300" required value="45"><button type="submit">Сохранить</button></div>
        <p class="ct-sub">Это не политика системного таймера обновлений.</p>
      </form><p id="prefs-status" class="status"></p>
    </div>
    <div class="core-section">
      <div class="toolbar"><h3>Журнал</h3><button type="button" id="activity-refresh">Обновить журнал</button></div>
      <p id="activity-status" class="status"></p><ol id="activity" class="activity-list"></ol>
    </div>
  </section>`;
  let known='';
  async function services(signal){const data=await request('/api/services/status',{signal});if(signal?.aborted)return;$('#services').innerHTML=[['xray',data.xray?'Работает':'Остановлен'],['mihomo',data.mihomo?'Работает':'Остановлен'],['Панель',data.lists?'Работает':'Неизвестно']].map(([label,value])=>`<div class="metric"><strong>${value}</strong><span>${label}</span></div>`).join('');status(feedback(),`Обновлено ${new Date().toLocaleTimeString('ru-RU')}`);}
  async function journal(signal){const data=await request('/api/activity',{signal});if(signal?.aborted)return;const signature=JSON.stringify(data.items);if(signature!==known){$('#activity').innerHTML=(data.items||[]).map(item=>`<li><small>${esc(time(item.ts||item.time||item.at))} · ${esc(item.user||item.actor||'system')} · ${item.ok===false?'Ошибка':'Событие'}</small><strong>${esc(item.action||item.event||'')}</strong><p>${esc(item.detail||item.message||'')}</p></li>`).join('')||'<li class="empty">Действий пока нет.</li>';known=signature;}status($('#activity-status'),`Обновлено ${new Date().toLocaleTimeString('ru-RU')}`);}
  const poll=scopedPoll(async signal=>{await Promise.all([services(signal).catch(error=>stale(feedback(),error)),journal(signal).catch(error=>stale($('#activity-status'),error))]);},10000);
  $('#services-refresh').addEventListener('click',event=>action(event.currentTarget,()=>services(),feedback()));$('#activity-refresh').addEventListener('click',event=>action(event.currentTarget,()=>journal(),$('#activity-status')));
  $$('[data-restart]').forEach(button=>button.addEventListener('click',()=>action(button,async()=>{if(!await confirmAction('Перезапустить сервис',`Перезапустить ${button.dataset.restart}? Текущие соединения могут прерваться.`,'Перезапустить'))return;await post('/api/services/restart',{service:button.dataset.restart,confirm:1});await services();await journal();},feedback())));
  $('#logout-mobile').addEventListener('click',event=>logout(event.currentTarget,feedback()));
  const prefsForm=$('#prefs-form');$('#prefs-form').addEventListener('submit',event=>{event.preventDefault();action($('button',prefsForm),async()=>{await post('/api/prefs/set',{auto_ping:prefsForm.elements.auto_ping.checked?'1':'0',auto_ping_sec:prefsForm.elements.auto_ping_sec.value});status($('#prefs-status'),'Предпочтения сохранены. Новая политика действует при следующем открытии страницы «Подключение».','success');},$('#prefs-status'));});
  try{const data=await request('/api/prefs');prefsForm.elements.auto_ping.checked=!!data.prefs?.auto_ping;prefsForm.elements.auto_ping_sec.value=data.prefs?.auto_ping_sec||45;}catch(error){stale($('#prefs-status'),error);}
}
async function updates(){
  setTitle('Обновления','Проверка ничего не устанавливает. Каждое обновление запускается только после подтверждения.');
  main.innerHTML=`<section class="surface">
    <div class="toolbar"><h2>Компоненты</h2><button type="button" id="versions-refresh">Обновить версии</button></div>
    <p id="system-status" class="status">Загрузка версий…</p><p id="source-mode" class="muted"></p>
    <p id="timer-policy" class="notice">Политика timer пока неизвестна.</p><div id="versions"></div>
    <div class="actions"><button type="button" id="fleet-update">Проверить и обновить весь стек</button></div>
    <p class="ct-sub">Обновление панели не применяет правила. Ядра обновляются отдельными заданиями. Существующая политика timer не меняется этой страницей.</p>
    <div class="core-section">
      <h3>Задание обновления</h3>
      <p class="muted">Идентификатор и конечный результат сохраняются на сервере и доступны после перезапуска панели.</p>
      <form id="operation-lookup" class="toolbar"><label for="operation-id">Идентификатор задания<input name="id" id="operation-id" required autocomplete="off"></label><button type="submit">Открыть задание</button></form>
      <div id="operation" class="operation"><p class="empty">Задание не выбрано.</p></div>
    </div>
    <div class="core-section">
      <h3>История изменений панели</h3><button type="button" id="changelog-load">Загрузить журнал версий</button>
      <p id="changelog-status" class="status"></p><div id="changelog"></div>
    </div>
  </section>`;
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
    if (!active) return;
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
  try{await loadVersions();}catch(error){if(error.name==='AbortError'||!active)return;stale(feedback(),error);}
  if (!active) return;
  let restored=new URL(location.href).searchParams.get('operation');if(!restored)try{restored=localStorage.getItem('xttp-operation-id');}catch{}if(restored&&/^[A-Za-z0-9_-]{1,128}$/.test(restored))watch(restored);
  await Promise.allSettled(['panel','mihomo','xray'].map(component=>check(component)));
}
const ready = (async () => {
  if(selectedPage==='core')return core();
  if(selectedPage==='connection')return connection();
  if(selectedPage==='users')return users();
  if(selectedPage==='activity')return activity();
  if(selectedPage==='updates')return updates();
  throw new Error('Страница системы не найдена.');
})();
return {
  ready,
  hasDraft:() => checkDraft(),
  isBusy:() => busy > 0,
  dispose() { active = false; controller.abort(); for (const poll of polls) poll.stop(); }
};
}
