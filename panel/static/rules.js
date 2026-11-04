import {$,$$,main,esc,request,post,action,status,notify,confirmAction,setTitle,guardNavigation,copyText,kv} from './app.js';
const types = [['auto','Авто'],['namespace','Домен и поддомены'],['domain','Точный домен'],['keyword','Ключевое слово'],['ipv4','IPv4 / CIDR'],['ipv6','IPv6 / CIDR'],['raw','Classical rule']];
const options = selected => types.map(([value,label]) => `<option value="${value}"${value === selected ? ' selected' : ''}>${label}</option>`).join('');
let groups = [], gid = '', group, originalGroup, page = [], total = 0, offset = 0, query = '', revision;
const drafts = new Map(), deleted = new Set();
let rowSerial = 0, loading = false, loadSerial = 0, saving = false, importChanged = false;
function dirty() { return drafts.size > 0 || deleted.size > 0 || !!group && (group.name !== originalGroup.name || group.enabled !== originalGroup.enabled); }
function updateDirty() { $('#save-group').disabled = !dirty() || loading || saving; $('#draft-state').textContent = dirty() ? `Несохранённый черновик · строк: ${drafts.size}, удалено: ${deleted.size}` : 'Нет несохранённых изменений'; }
function renderState(state) {
  revision = state || revision;
  const labels = {applied:'Сохранённые правила применены',pending:'Сохранено, но ещё не применено',unknown:'Состояние применения неизвестно. Для активации нажмите «Применить».'};
  const value = revision?.status || 'unknown'; status($('#rules-state'), labels[value] || labels.unknown, value === 'applied' ? 'success' : 'warning');
}
function renderGroups() {
  const search = $('#group-search').value.toLocaleLowerCase();
  const visible = groups.filter(g => g.name.toLocaleLowerCase().includes(search));
  $('#group-list').innerHTML = visible.map(g => `<a class="group-link" href="/rules/${encodeURIComponent(g.id)}"${g.id === gid ? ' aria-current="page"' : ''}><strong>${esc(g.name)}</strong><small>${g.enabled ? 'Включена' : 'Выключена'} · ${g.count} правил</small><small>${esc(Object.entries(g.counts || {}).map(([k,v]) => `${k}: ${v}`).join(' · '))}</small></a>`).join('') || '<p class="empty">Группы не найдены.</p>';
  if (gid && group) {
    const index=groups.findIndex(item=>item.id===gid);
    $('#group-up').disabled=index<=0;
    $('#group-down').disabled=index<0 || index>=groups.length-1;
  }
}
async function loadGroups() { const data = await request('/api/groups'); groups = data.groups; renderGroups(); renderState(data.state); }
function rowHTML(entry, key, index) {
  return `<div class="rule-row${drafts.has(key) ? ' dirty' : ''}" data-key="${esc(key)}"><span class="row-number">${entry.id ? `Правило ${offset + index + 1}` : 'Новое правило'}</span><label>Имя<input data-field="name" value="${esc(entry.name)}" maxlength="256"></label><label>Тип<select data-field="type">${options(entry.type || 'auto')}</select></label><label class="pattern-label">Значение<input data-field="value" value="${esc(entry.value || entry.rule)}" required spellcheck="false"></label><label class="check"><input type="checkbox" data-field="enabled"${entry.enabled !== false ? ' checked' : ''}>Вкл.</label><button type="button" data-delete="${esc(key)}" class="danger" aria-label="Удалить правило ${esc(entry.name || entry.value || entry.rule)}">Удалить</button><p class="row-error" hidden></p></div>`;
}
function renderRows() {
  const rows = page.filter(entry => !deleted.has(entry.id)).map((entry,index) => rowHTML(drafts.get(entry.id) || entry, entry.id, index));
  for (const [key,entry] of drafts) if (!entry.id) rows.push(rowHTML(entry,key,0));
  $('#rule-rows').innerHTML = rows.join('') || '<p class="empty">В этой выборке нет правил. Добавьте правило или импортируйте список.</p>';
  $('#page-count').textContent = total ? `${offset + 1}–${Math.min(offset + 50,total)} из ${total}` : '0 правил';
  $('#previous-page').disabled = offset === 0 || loading; $('#next-page').disabled = offset + 50 >= total || loading;
  updateDirty();
}
async function loadPage(newOffset = offset) {
  const serial = ++loadSerial; loading = true; updateDirty();
  status($('#editor-status'),'Загрузка правил…');
  try {
    const data = await request(`/api/groups/${encodeURIComponent(gid)}?offset=${newOffset}&limit=50&q=${encodeURIComponent(query)}`);
    if (serial !== loadSerial) return;
    if (!group) {
      group = {...data.group}; originalGroup = {...data.group};
      $('#group-name').value = group.name; $('#group-enabled').checked = group.enabled;
      $('#editor-title').textContent = group.name;
      $$('input,select,textarea,button',$('.group-editor')).forEach(control=>{control.disabled=false;});
      renderGroups();
    }
    offset = data.offset; total = data.total; page = data.entries; renderState(data.state); status($('#editor-status'),'');
  } catch (error) { if (serial === loadSerial) status($('#editor-status'),`Не удалось загрузить правила: ${error.message}. Черновик сохранён в этой вкладке.`, 'error'); }
  finally { if (serial === loadSerial) { loading = false; renderRows(); } }
}
async function save() {
  if (saving) return false;
  if (!dirty()) return true;
  const payload = {gid, entries:[...drafts.values()], deleted:[...deleted]};
  if (group.name !== originalGroup.name) payload.name = group.name;
  if (group.enabled !== originalGroup.enabled) payload.enabled = group.enabled ? '1' : '0';
  const editor = $('.group-editor');
  const focused = document.activeElement;
  saving = true; editor.inert = true; editor.setAttribute('aria-busy','true');
  status($('#editor-status'),'Сохранение одной транзакцией…');
  let data;
  try {
    data = await post('/api/groups/save',payload);
  } catch (error) {
    status($('#editor-status'),`Не сохранено: ${error.message}. Исправьте значения и повторите. Черновик не удалён.`, 'error');
    const errors = error.data?.errors || [];
    $$('.rule-row',main).forEach(row => {
      const entry = drafts.get(row.dataset.key);
      const issue = entry && errors.find(x => x.id && x.id === entry.id || x.index === [...drafts.keys()].indexOf(row.dataset.key));
      if (issue || !errors.length && drafts.has(row.dataset.key)) {
        row.classList.add('invalid');
        row.querySelector('.row-error').hidden = false;
        row.querySelector('.row-error').textContent = issue?.error || error.message;
      }
    });
    return false;
  } finally {
    saving = false; editor.inert = false; editor.removeAttribute('aria-busy');
    updateDirty();
    if (focused?.isConnected) focused.focus();
  }
  drafts.clear(); deleted.clear(); group = {...data.group}; originalGroup = {...data.group};
  $('#group-name').value = group.name; $('#editor-title').textContent = group.name;
  renderState(data.state); updateDirty();
  const refreshed = await Promise.allSettled([loadGroups(), loadPage(0)]);
  const error = refreshed.find(result => result.status === 'rejected');
  if (error) status($('#editor-status'),`Сохранено, но список не обновлён: ${error.reason.message}. Повторите загрузку.`, 'warning');
  else if ($('#editor-status').classList.contains('error')) {
    status($('#editor-status'),'Сохранено, но строки не удалось перечитать. Повторите загрузку.','warning');
  } else status($('#editor-status'),'Сохранено. Для активации используйте отдельную кнопку «Применить».','success');
  return true;
}
function editorHTML() {
  return `<section class="group-editor surface"><a class="editor-back" href="/rules">К списку групп</a><h2 id="editor-title">Редактор группы</h2><div class="form-grid"><label>Полное имя группы<input id="group-name" required maxlength="256"></label><label class="check"><input id="group-enabled" type="checkbox">Группа включена</label></div><div class="actions"><button type="button" id="group-up">Выше</button><button type="button" id="group-down">Ниже</button><button type="button" id="copy-group">Копировать группу</button><button type="button" id="delete-group" class="danger">Удалить группу</button></div><p id="editor-status" class="status"></p><div class="toolbar"><label>Поиск по правилам<input id="rule-search" type="search" placeholder="Домен, адрес или имя"></label><button type="button" id="search-rules">Найти</button><button type="button" id="reload-rules">Обновить</button></div><div id="rule-rows"></div><div class="pagination"><button type="button" id="previous-page">Назад</button><p id="page-count"></p><button type="button" id="next-page">Далее</button></div><div class="actions"><button type="button" id="add-rule">Добавить правило</button><button type="button" id="save-group" class="primary" disabled>Сохранить</button><button type="button" id="discard-draft">Отменить черновик</button></div><p id="draft-state" class="muted"></p><details><summary>Импорт списка</summary><p class="muted">Вставьте текущий исходный текст. Сервер проверит дубли и конфликты заново при импорте. Импорт сохраняет правила, но не применяет их.</p><form id="import-form" class="form-stack"><label>Исходный текст<textarea id="import-text" required spellcheck="false" placeholder="Один домен или CIDR в строке"></textarea></label><label>Режим типа<select id="import-detect"><option value="auto">Автоопределение</option><option value="ipv4">Только IPv4 / CIDR</option><option value="namespace">Домен и поддомены</option></select></label><label class="check"><input type="checkbox" id="skip-conflicts" checked>Пропускать конфликты с другими группами</label><div class="actions"><button type="button" id="preview-import">Проверить превью</button><button type="submit" class="primary">Импортировать текущий текст</button></div></form><p id="import-status" class="status"></p><div id="import-preview" class="preview"></div></details></section>`;
}
let overviewItems = [], overviewLoaded = false, overviewSerial = 0;
function renderOverview() {
  const membership = new Map();
  for (const item of overviewItems) {
    const key = (item.rule || item.value).toLowerCase();
    if (!membership.has(key)) membership.set(key,new Set());
    membership.get(key).add(item.group_id);
  }
  const type = $('#overview-type').value, conflictsOnly = $('#overview-conflicts').checked;
  const query = $('#overview-search').value.trim().toLocaleLowerCase();
  const items = overviewItems.filter(item => (!type || item.type === type) &&
    (!query || ['group_name','type','value','rule','name'].map(key=>item[key] || '').join(' ').toLocaleLowerCase().includes(query)) &&
    (!conflictsOnly || membership.get((item.rule || item.value).toLowerCase()).size > 1));
  const index = Number($('#overview-page').dataset.offset || 0);
  $('#overview-count').textContent = overviewLoaded ?
    `${items.length} правил · показано ${Math.min(index + 1,items.length)}–${Math.min(index + 50,items.length)}` :
    'Обзор загружается только по запросу.';
  $('#overview-results').innerHTML = items.slice(index,index+50).map(item => {
    const conflict = membership.get((item.rule || item.value).toLowerCase()).size > 1;
    return `<tr><td><a href="/rules/${encodeURIComponent(item.group_id)}">${esc(item.group_name)}</a></td>
      <td>${esc(item.type)}</td><td>${esc(item.value || item.rule)}</td>
      <td>${item.enabled && item.group_enabled ? 'Вкл.' : 'Выкл.'}${conflict ? ' · Конфликт между группами' : ''}</td></tr>`;
  }).join('') || `<tr><td colspan="4">${overviewLoaded ? 'Совпадений нет.' : 'Нажмите «Загрузить обзор».'}</td></tr>`;
  $('#overview-prev').disabled = index === 0;
  $('#overview-next').disabled = index + 50 >= items.length;
}
async function overview() {
  const serial = ++overviewSerial;
  const data = await request('/api/rules/overview');
  if (serial !== overviewSerial) return;
  overviewItems = data.items || []; overviewLoaded = true;
  renderOverview();
}
export async function mount() {
  setTitle('Правила','Сохранение меняет список. Применение отдельно активирует сохранённые правила.');
  gid = decodeURIComponent(location.pathname.split('/')[2] || ''); guardNavigation(() => dirty() || importChanged);
  main.innerHTML = `<div class="toolbar"><p id="rules-state" class="status">Состояние неизвестно</p><button type="button" id="apply-rules" class="primary">Применить</button></div><div class="rules-layout${gid ? ' has-selection' : ''}"><section class="group-master surface"><h2>Группы</h2><label>Поиск групп<input id="group-search" type="search"></label><form id="create-group" class="form-stack"><label>Новая группа<input name="name" required maxlength="256" placeholder="Полное название"></label><button type="submit">Создать группу</button></form><div id="group-list" class="group-list"><p class="loading">Загрузка групп…</p></div></section>${gid ? editorHTML() : '<section class="group-editor surface"><h2>Выберите группу</h2><p class="muted">Откройте группу слева, чтобы редактировать её правила. Порядок групп определяет приоритет.</p></section>'}</div><section class="surface"><h2>Проверка маршрута</h2><form id="explain-form" class="toolbar"><label>Домен или IP<input name="q" required placeholder="example.com"></label><button type="submit">Объяснить маршрут</button></form><p class="muted">Проверка использует сохранённые правила, не текущий черновик.</p><div id="explain-result" class="status"></div></section><section class="surface"><h2>Обзор правил</h2><form id="overview-form" class="toolbar"><label>Поиск во всех группах<input id="overview-search" type="search"></label><button type="submit">Загрузить обзор</button></form><p id="overview-count" class="muted">Обзор загружается только по запросу.</p><div class="table-wrap"><table><thead><tr><th>Группа</th><th>Тип</th><th>Значение</th><th>Состояние</th></tr></thead><tbody id="overview-results"></tbody></table></div><div class="pagination" id="overview-page" data-offset="0"><button type="button" id="overview-prev" disabled>Назад</button><button type="button" id="overview-next" disabled>Далее</button></div></section>`;
  if (gid) $$('input,select,textarea,button',$('.group-editor')).forEach(control=>{control.disabled=control.id!=='reload-rules';});
  $('#overview-form').insertAdjacentHTML('afterend',`<div class="form-grid">
    <label>Тип в обзоре<select id="overview-type"><option value="">Все типы</option>${options('')}</select></label>
    <label class="check"><input type="checkbox" id="overview-conflicts">Только конфликты между группами</label>
    </div>`);
  for (const id of ['overview-type','overview-conflicts']) $('#'+id).addEventListener('change',() => {
    $('#overview-page').dataset.offset = '0'; renderOverview();
  });
  $('#group-search').addEventListener('input', renderGroups);
  $('#create-group').addEventListener('submit', event => {
    event.preventDefault();
    const form = event.currentTarget;
    action($('button',form), async () => {
      const data = await post('/api/groups/create',{name:new FormData(form).get('name')});
      if ((dirty() || importChanged) && !await confirmAction('Открыть новую группу?', 'Текущий черновик и несохранённый текст импорта будут потеряны. Открыть созданную группу?', 'Открыть')) {
        await loadGroups(); return;
      }
      guardNavigation(() => false);
      location.assign(`/rules/${encodeURIComponent(data.group.id)}`);
    });
  });
  $('#apply-rules').addEventListener('click', event => action(event.currentTarget, async () => {
    if (dirty()) { if (!await confirmAction('Сначала сохранить', 'Есть несохранённые изменения. Сохранить их перед применением?', 'Сохранить')) return; if (!await save()) return; }
    if (!await confirmAction('Применить правила', 'Активировать сохранённые правила и перезапустить mihomo? Кандидат будет проверен сервером.', 'Применить')) return;
    try { const data = await post('/api/rules/apply',{confirm:1}); renderState(data.state); notify('Правила применены'); }
    catch(error) { status($('#rules-state'),`Применение не завершено: ${error.message}${error.data?.rollback ? ` · Восстановление: ${JSON.stringify(error.data.rollback)}` : ''}`,'error'); }
  }));
  $('#explain-form').addEventListener('submit', event => {
    event.preventDefault();
    const query = new FormData(event.currentTarget).get('q');
    action($('button',event.currentTarget), async () => {
      const data = await request(`/api/explain-route?q=${encodeURIComponent(query)}`);
      const steps = data.steps || {}, rule = steps.rule || {}, matchedGroup = steps.group || {}, route = steps.action || {};
      $('#explain-result').innerHTML = kv([
        ['Запрос',steps.request || data.query || query],
        ['Совпадение',data.matched ? 'Найдено' : 'Правило по умолчанию'],
        ['Правило',rule.rule || rule.pattern || 'MATCH'],
        ['Группа',matchedGroup.name || '—'],
        ['Политика',route.policy || '—'],
        ['Узел',route.node || '—'],
        ['Результат',route.detail || '—']
      ]);
    },$('#explain-result'));
  });
  $('#overview-form').addEventListener('submit',event => {event.preventDefault(); $('#overview-page').dataset.offset = '0'; action($('button',event.currentTarget),overview); });
  for (const [id,delta] of [['overview-prev',-50],['overview-next',50]]) $('#'+id).addEventListener('click',() => {
    $('#overview-page').dataset.offset = String(Math.max(0,Number($('#overview-page').dataset.offset)+delta));
    renderOverview();
  });
  await loadGroups();
  if (!gid) return;
  await loadPage(0);
  $('#group-name').addEventListener('input',event => {if(group){group.name=event.target.value;updateDirty();}});
  $('#group-enabled').addEventListener('change',event => {if(group){group.enabled=event.target.checked;updateDirty();}});
  $('#rule-rows').addEventListener('input',event => {
    const input = event.target.closest('[data-field]'); if (!input) return; const row = input.closest('.rule-row'), key = row.dataset.key;
    const entry = {...(drafts.get(key) || page.find(e => e.id === key))}; entry[input.dataset.field] = input.type === 'checkbox' ? input.checked : input.value;
    if (input.dataset.field === 'value' || input.dataset.field === 'type') delete entry.rule;
    drafts.set(key,entry); row.classList.add('dirty'); row.classList.remove('invalid'); row.querySelector('.row-error').hidden = true; updateDirty();
  });
  $('#rule-rows').addEventListener('click',event => { const button = event.target.closest('[data-delete]'); if (!button) return; const key = button.dataset.delete; if (drafts.get(key)?.id || page.some(e => e.id === key)) deleted.add(key); drafts.delete(key); button.closest('.rule-row').remove(); updateDirty(); });
  $('#add-rule').addEventListener('click',() => { const key=`new-${++rowSerial}`; const entry={type:'auto',value:'',name:'',enabled:true}; drafts.set(key,entry); renderRows(); $(`[data-key="${key}"] input`).focus(); });
  $('#save-group').addEventListener('click',async event => {
    await action(event.currentTarget,save,$('#editor-status'));
    updateDirty();
  });
  $('#discard-draft').addEventListener('click',event => action(event.currentTarget,async () => {if(dirty() && !await confirmAction('Отменить черновик','Удалить все несохранённые изменения этой группы?','Отменить изменения')) return; drafts.clear();deleted.clear();group={...originalGroup};$('#group-name').value=group.name;$('#group-enabled').checked=group.enabled;renderRows();}));
  for (const [id,delta] of [['previous-page',-50],['next-page',50]]) $('#'+id).addEventListener('click',()=>loadPage(Math.max(0,offset+delta)));
  const search = () => {query=$('#rule-search').value;loadPage(0);}; $('#search-rules').addEventListener('click',search); $('#rule-search').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();search();}}); $('#reload-rules').addEventListener('click',()=>loadPage());
  for (const [id,delta] of [['group-up',-1],['group-down',1]]) $('#'+id).addEventListener('click',event=>action(event.currentTarget,async()=>{const index=groups.findIndex(g=>g.id===gid),target=index+delta;if(target<0||target>=groups.length)return;const ids=groups.map(g=>g.id);[ids[index],ids[target]]=[ids[target],ids[index]];const data=await post('/api/groups/reorder',{ids});renderState(data.state);await loadGroups();}));
  $('#delete-group').addEventListener('click',event=>action(event.currentTarget,async()=>{if(!await confirmAction('Удалить группу',`Удалить «${group?.name || gid}» и все её правила? Черновик также будет потерян.`,'Удалить'))return;await post('/api/groups/delete',{gid,confirm:1});guardNavigation(()=>false);location.assign('/rules');}));
  $('#copy-group').addEventListener('click',event=>action(event.currentTarget,async()=>{if(dirty()){notify('Копируется сохранённая версия, без черновика.');}const data=await request(`/api/rules/overview?q=`);await copyText(data.items.filter(item=>item.group_id===gid).map(item=>item.rule||item.value).join('\n'));}));
  let previewSerial=0;
  $('#import-text').addEventListener('input',()=>{importChanged=true;previewSerial++;$('#import-preview').textContent='Текст изменён. Обновите превью; импорт использует именно текущий текст.';});
  $('#import-detect').addEventListener('change',()=>{previewSerial++;$('#import-preview').textContent='Режим изменён. Обновите превью.';});
  $('#preview-import').addEventListener('click',event=>action(event.currentTarget,async()=>{
    const serial=++previewSerial,text=$('#import-text').value,mode=$('#import-detect').value;status($('#import-status'),'Проверка исходного текста…');
    let detected, overviewData;
    try { [detected,overviewData]=await Promise.all([post('/api/detect',{text,detect:mode}),request('/api/rules/overview')]); }
    catch(error) { if(serial!==previewSerial)return; throw error; }
    if(serial!==previewSerial)return;
    const seen=new Set(), existing=new Set(overviewData.items.filter(x=>x.group_id===gid).map(x=>(x.rule||x.value).toLowerCase())), other=new Set(overviewData.items.filter(x=>x.group_id!==gid).map(x=>(x.rule||x.value).toLowerCase()));
    const items=(detected.items||[]).map(item=>{const key=(item.rule||item.value).toLowerCase();const classification=seen.has(key)||existing.has(key)?'Дубль':other.has(key)?'Конфликт':'Новое';seen.add(key);return {...item,classification};});
    const counts = items.reduce((result,item) => {result[item.classification] = (result[item.classification] || 0) + 1; return result;},{});
    $('#import-preview').innerHTML=`<p class="muted">${items.length} распознано · новых ${counts['Новое']||0} · дублей ${counts['Дубль']||0} · конфликтов ${counts['Конфликт']||0} · невалидно ${detected.invalid||0}. Сервер повторно проверит текущий текст при импорте.</p><div class="table-wrap"><table><thead><tr><th>Значение</th><th>Тип</th><th>Статус</th></tr></thead><tbody>${items.slice(0,200).map(item=>`<tr><td>${esc(item.value||item.rule)}</td><td>${esc(item.type)}</td><td>${esc(item.classification)}</td></tr>`).join('')}</tbody></table></div>${items.length>200?'<p>Показаны первые 200 строк. Будет отправлен весь исходный текст.</p>':''}`;status($('#import-status'),'Превью готово. Изменения ещё не сохранены.');
  },$('#import-status')));
  $('#import-form').addEventListener('submit',event=>{event.preventDefault();action($('button[type=submit]',event.currentTarget),async()=>{
    if(dirty()){if(!await confirmAction('Сначала сохранить','Чтобы импорт не смешивался с черновиком, сначала сохранить правки?','Сохранить'))return;if(!await save())return;}
    const text=$('#import-text').value,detect=$('#import-detect').value,skip_conflicts=$('#skip-conflicts').checked?'1':'0';
    const data=await post('/api/groups/import',{gid,text,detect,skip_conflicts});
    if ($('#import-text').value === text) importChanged=false;
    renderState(data.state);
    await Promise.allSettled([loadGroups(),loadPage(0)]);
    status($('#import-status'),`Импортировано: ${typeof data.imported==='number'?data.imported:JSON.stringify(data.imported)} · пропущено: ${typeof data.skipped==='number'?data.skipped:JSON.stringify(data.skipped)} · невалидно: ${typeof data.invalid==='number'?data.invalid:JSON.stringify(data.invalid)}. Правила ещё не применены.`,'success');
  },$('#import-status'));});
}
