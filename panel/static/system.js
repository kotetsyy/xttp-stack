const nativeFetch = globalThis.fetch.bind(globalThis);
let sessionData = null;
let sessionRequest = null;
let pending = 0;
let authNotified = false;

async function parsed(response) {
  let data;
  try { data = await response.json(); }
  catch (_) { data = {ok:false, error:'Сервер вернул не JSON · HTTP ' + response.status}; }
  if (!data || typeof data !== 'object' || Array.isArray(data)) data = {ok:false, error:'Некорректный ответ сервера'};
  return {...data, ok:response.ok && data.ok === true, http_status:response.status};
}

function expired() {
  sessionData = null;
  if (!authNotified && typeof globalThis.dispatchEvent === 'function') {
    authNotified = true;
    globalThis.dispatchEvent(new Event('panel-auth-required'));
  }
}

export async function getSession() {
  if (sessionData) return sessionData;
  if (!sessionRequest) {
    sessionRequest = (async () => {
      const response = await nativeFetch('/api/session', {credentials:'same-origin', cache:'no-store', headers:{Accept:'application/json'}});
      const data = await parsed(response);
      if (response.status === 401) expired();
      if (data.ok && data.csrf) { sessionData = data; authNotified = false; }
      return data;
    })().finally(() => { sessionRequest = null; });
  }
  return sessionRequest;
}

export function isBusy() { return pending > 0; }

export async function panelFetch(input, init = {}) {
  const request = typeof Request !== 'undefined' && input instanceof Request ? input : null;
  const url = new URL(request ? request.url : input, globalThis.location.href);
  if (url.origin !== globalThis.location.origin) throw new Error('Panel API requires the same origin');
  const method = (init.method || request?.method || 'GET').toUpperCase();
  const mutation = !['GET','HEAD','OPTIONS'].includes(method);
  const headers = new Headers(init.headers || request?.headers);
  headers.set('Accept', 'application/json');
  if (mutation) pending++;
  try {
    if (mutation) {
      const session = await getSession();
      if (!session.ok || !session.csrf) {
        return Response.json({ok:false, error:session.error || 'Нужен повторный вход; черновики сохранены в открытой странице'}, {status:session.http_status >= 400 ? session.http_status : 401});
      }
      headers.set('X-CSRF-Token', session.csrf);
    }
    const response = await nativeFetch(input, {...init, method, headers, credentials:'same-origin'});
    if (response.status === 401) expired();
    return response;
  } finally { if (mutation) pending--; }
}

export async function api(path, data = {}) {
  pending++;
  try {
    const body = new URLSearchParams(data);
    const response = await panelFetch(path, {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body:body.toString()});
    return await parsed(response);
  } finally { pending--; }
}

export async function readOperation(id, signal) {
  const response = await panelFetch('/api/operations/' + encodeURIComponent(id), {cache:'no-store', signal});
  const data = await parsed(response);
  if (!data.ok) throw Object.assign(new Error(data.error || 'Не удалось получить задание'), {status:response.status});
  if (!data.operation) throw new Error('Ответ не содержит задания');
  return data.operation;
}

export function watchOperation(id, onChange, onError) {
  const controller = new AbortController();
  let timer = null;
  let stopped = false;
  async function tick() {
    if (stopped) return;
    try {
      const operation = await readOperation(id, controller.signal);
      if (stopped) return;
      onChange(operation);
      if (['succeeded','failed'].includes(operation.status)) { stop(); return; }
    } catch (error) {
      if (stopped || error.name === 'AbortError') return;
      onError(error);
      if ([401,404].includes(error.status)) { stop(); return; }
    }
    if (!stopped) timer = setTimeout(tick, 2000);
  }
  function stop() {
    stopped = true;
    clearTimeout(timer);
    controller.abort();
  }
  tick();
  return {stop};
}

export async function startUpdate(component, targetVersion) {
  if (!['panel','mihomo','xray'].includes(component) || !/^v?\d+(?:\.\d+){1,3}(?:[-+][A-Za-z0-9.-]+)?$/.test(targetVersion || '')) {
    return {ok:false, error:'Нужен компонент и точная опубликованная версия'};
  }
  return api('/api/updates/start', {component, target_version:targetVersion, confirm:1});
}
