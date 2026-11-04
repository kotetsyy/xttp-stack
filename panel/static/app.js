export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
export const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const main = $('#main');
export let session;
let csrf = '';
let dirtyGuard = () => false;
let leaving = false;
const pendingActions = new WeakSet();
export function guardNavigation(check) { dirtyGuard = check; }
export function setTitle(title, description = '') { $('#page-title').textContent = title; $('#page-description').textContent = description; document.title = `${title} — xttp`; }
export function status(node, text, kind = '') { node.textContent = text; node.className = `status ${kind}`; node.setAttribute('role', kind === 'error' ? 'alert' : 'status'); }
export function notify(text, error = false) {
  const item = document.createElement('div'); item.className = `notification${error ? ' error' : ''}`;
  const message = document.createElement('span'); message.textContent = text;
  const close = document.createElement('button'); close.type = 'button'; close.textContent = 'Закрыть'; close.addEventListener('click', () => item.remove());
  item.append(message, close); $('#notifications').append(item);
  if (!error) setTimeout(() => item.remove(), 9000);
}
function offerReload(version) {
  const node = $('#bundle-notice');
  if (!version || version === document.body.dataset.version || !node || node.dataset.version === version) return;
  node.dataset.version = version; node.hidden = false;
  node.replaceChildren();
  const message = document.createElement('p');
  message.textContent = `Сервер панели обновлён до ${version}; в этой вкладке открыт интерфейс ${document.body.dataset.version}. Ответ выполненного действия не изменён. Сохраните черновик перед обновлением страницы.`;
  const button = document.createElement('button');
  button.type = 'button'; button.textContent = 'Обновить страницу';
  button.addEventListener('click', async () => {
    if (dirtyGuard() && !await confirmAction('Несохранённый черновик', 'Обновление страницы удалит несохранённые значения. Отмените и сначала сохраните их либо явно продолжите.', 'Обновить и потерять черновик')) return;
    leaving = true; location.reload();
  });
  node.append(message,button);
}
export async function request(path, {method = 'GET', data, signal} = {}) {
  const headers = {'Accept':'application/json'};
  const options = {method, credentials:'same-origin', cache:'no-store', headers, signal};
  if (method !== 'GET') {
    if (!csrf) throw new Error('Сессия ещё не загружена. Обновите страницу.');
    headers['X-CSRF-Token'] = csrf;
    headers['Content-Type'] = 'application/x-www-form-urlencoded;charset=UTF-8';
    options.body = new URLSearchParams(Object.entries(data || {}).map(([k,v]) => [k, typeof v === 'object' ? JSON.stringify(v) : String(v)]));
  }
  const response = await fetch(path, options);
  offerReload(response.headers.get('X-XTTP-Version'));
  if (response.status === 401) { leaving = true; location.assign('/login'); throw new Error('Сессия завершена. Войдите снова.'); }
  let result;
  try { result = await response.json(); } catch { throw new Error(`Сервер вернул неверный ответ (${response.status}). Повторите запрос.`); }
  if (!response.ok || result.ok === false) {
    const recovery = result.rollback == null ? '' : ` · Восстановление: ${typeof result.rollback === 'string' ? result.rollback : JSON.stringify(result.rollback)}`;
    const error = new Error((result.error || `Ошибка HTTP ${response.status}`) + recovery);
    error.data = result; error.status = response.status; throw error;
  }
  return result;
}
export const post = (path, data, options = {}) => request(path, {...options, method:'POST', data});
export async function action(button, task, output) {
  if (button.disabled || pendingActions.has(button)) return;
  pendingActions.add(button);
  button.setAttribute('aria-busy', 'true'); button.setAttribute('aria-disabled', 'true');
  try { return await task(); }
  catch (error) {
    if (error.name !== 'AbortError') {
      if (output) status(output, error.message, 'error');
      else notify(error.message, true);
    }
  } finally {
    pendingActions.delete(button);
    button.removeAttribute('aria-busy'); button.removeAttribute('aria-disabled');
  }
}
export function confirmAction(title, message, label = 'Подтвердить') {
  const dialog = $('#confirm-dialog');
  if (dialog.open) return Promise.resolve(false);
  const focused = document.activeElement;
  $('#confirm-title').textContent = title; $('#confirm-message').textContent = message; $('#confirm-action').textContent = label;
  dialog.returnValue = ''; dialog.showModal();
  return new Promise(resolve => dialog.addEventListener('close', () => { focused?.isConnected && focused.focus(); resolve(dialog.returnValue === 'confirm'); }, {once:true}));
}
export function scopedPoll(task, milliseconds = 2000, {immediate = true} = {}) {
  let timer, running = false, stopped = false, suspended = false, controller;
  async function tick() {
    clearTimeout(timer);
    if (stopped || suspended || document.hidden || running) return;
    running = true; controller = new AbortController();
    try { await task(controller.signal); } catch (error) { if (error.name !== 'AbortError') notify(error.message, true); }
    finally { running = false; if (!stopped && !suspended && !document.hidden) timer = setTimeout(tick, milliseconds); }
  }
  function visibility() { clearTimeout(timer); if (document.hidden) controller?.abort(); else tick(); }
  function suspend() { suspended = true; clearTimeout(timer); controller?.abort(); }
  function resume(event) { if (event.persisted) { suspended = false; tick(); } }
  function stop() {
    stopped = true; clearTimeout(timer); controller?.abort();
    document.removeEventListener('visibilitychange', visibility);
    window.removeEventListener('pagehide', suspend);
    window.removeEventListener('pageshow', resume);
  }
  document.addEventListener('visibilitychange', visibility);
  window.addEventListener('pagehide', suspend);
  window.addEventListener('pageshow', resume);
  if (immediate) tick(); else timer = setTimeout(tick, milliseconds);
  return {stop, refresh:tick};
}
export function bytes(value) { let n = Number(value); if (!Number.isFinite(n)) return '—'; const units = ['Б','КиБ','МиБ','ГиБ','ТиБ']; let u = 0; while (Math.abs(n) >= 1024 && u < units.length - 1) { n /= 1024; u++; } return `${n.toLocaleString('ru-RU',{maximumFractionDigits:u ? 1 : 0})} ${units[u]}`; }
export function time(value) { if (!value) return '—'; const date = new Date(typeof value === 'number' ? value * 1000 : value); return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('ru-RU'); }
export function kv(rows) { return `<dl>${rows.map(([label,value]) => `<dt>${esc(label)}</dt><dd>${esc(value ?? '—')}</dd>`).join('')}</dl>`; }
export async function copyText(text) {
  const focused = document.activeElement;
  if (navigator.clipboard && window.isSecureContext) {
    try { await navigator.clipboard.writeText(text); notify('Скопировано'); return; }
    catch { /* Native selection below also works when clipboard permission is denied. */ }
  }
  const input = document.createElement('textarea');
  input.value = text; input.readOnly = true;
  input.setAttribute('aria-label','Текст для копирования');
  main.append(input); input.focus(); input.select();
  let copied = false;
  try { copied = document.execCommand('copy'); } catch { /* Keep the real selection for manual copying. */ }
  if (copied) { input.remove(); focused?.isConnected && focused.focus(); notify('Скопировано'); }
  else { input.scrollIntoView({block:'center'}); notify('Выделен текст. Нажмите Ctrl+C или используйте меню копирования.'); }
}
function navigation() {
  const section = location.pathname.split('/')[1];
  $$('[data-section]').forEach(link => { if (link.dataset.section === section) link.setAttribute('aria-current','page'); });
  const pages = section === 'monitoring' ? [['traffic','Трафик'],['connections','Соединения'],['logs','Логи']] : section === 'system' ? [['core','Ядро'],['connection','Подключение'],['updates','Обновления'],['users','Пользователи'],['activity','Операции']] : [];
  if (pages.length) { const nav = $('#section-nav'); nav.hidden = false; nav.innerHTML = pages.map(([path,label]) => `<a href="/${section}/${path}"${location.pathname === `/${section}/${path}` ? ' aria-current="page"' : ''}>${label}</a>`).join(''); }
}
window.addEventListener('beforeunload', event => { if (!leaving && dirtyGuard()) { event.preventDefault(); event.returnValue = ''; } });
window.addEventListener('pageshow', event => { if (event.persisted) leaving = false; });
document.addEventListener('click', async event => {
  const link = event.target.closest('a[href]');
  if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || link.target || !dirtyGuard()) return;
  if (new URL(link.href).href === location.href || link.getAttribute('href').startsWith('#')) return;
  event.preventDefault(); if (await confirmAction('Несохранённые изменения', 'Покинуть редактор и потерять несохранённые изменения?', 'Покинуть')) { leaving = true; location.assign(link.href); }
});
async function checkEntryUpdates() {
  const checks = await Promise.allSettled(['panel','mihomo','xray'].map(component => request(`/api/core/versions/check?component=${component}`)));
  const offers = checks.filter(x => x.status === 'fulfilled' && x.value.update_available).map(x => `${x.value.component} ${x.value.latest}`);
  const failures = checks.filter(x => x.status === 'rejected');
  if (offers.length || failures.length) {
    const node = $('#update-offer'); node.hidden = false;
    node.innerHTML = `${offers.length ? `Доступно обновление: ${esc(offers.join(', '))}. ` : ''}${failures.length ? `Не удалось проверить ${failures.length} компонент(а). ` : ''}<a href="/system/updates">Проверить и выбрать обновление</a>. Ничего не установлено автоматически.`;
  }
}
async function start() {
  navigation();
  try {
    session = await request('/api/session'); csrf = session.csrf;
    $('#logout').addEventListener('click', event => action(event.currentTarget, async () => { if (dirtyGuard() && !await confirmAction('Выход', 'Несохранённые изменения будут потеряны. Выйти?', 'Выйти')) return; await post('/logout'); leaving = true; location.assign('/login'); }));
    const section = location.pathname.split('/')[1];
    if (section === 'rules') await (await import('./rules.js')).mount();
    else if (section === 'monitoring') await (await import('./monitoring.js')).mount();
    else if (section === 'system') await (await import('./system.js')).mount();
    else throw new Error('Страница не найдена. Откройте раздел в навигации.');
    if (section !== 'system' || !location.pathname.endsWith('/updates')) checkEntryUpdates().catch(error => { const node = $('#update-offer'); node.hidden = false; node.textContent = `Проверка обновлений недоступна: ${error.message}. Повторите её в разделе «Обновления».`; });
  } catch (error) { main.innerHTML = `<div class="status error" role="alert">${esc(error.message)}</div><button type="button" id="retry-page">Повторить загрузку</button>`; $('#retry-page').addEventListener('click', () => location.reload()); }
}
start();
