import {api, panelFetch} from './system.js';

function checked(data) {
  if (!data || data.ok !== true) {
    const error = new Error(data?.error || 'Не удалось выполнить запрос');
    error.data = data;
    throw error;
  }
  return data;
}

export async function saveGroup(gid, changes) {
  const payload = {...changes, gid};
  if (changes.entries) payload.entries = JSON.stringify(changes.entries);
  if (changes.deleted) payload.deleted = JSON.stringify(changes.deleted);
  return checked(await api('/api/groups/save', payload));
}

export async function createGroup(name) {
  return checked(await api('/api/groups/create', {name}));
}

export async function deleteGroup(gid) {
  return checked(await api('/api/groups/delete', {gid, confirm: '1'}));
}

export async function importGroup(gid, text, detect, skipConflicts) {
  return checked(await api('/api/groups/import', {
    gid, text, detect, skip_conflicts: skipConflicts ? '1' : '0'
  }));
}

export async function applyRules() {
  return checked(await api('/api/rules/apply', {confirm: '1'}));
}

export async function detect(text, mode = 'auto') {
  return checked(await api('/api/detect', {text, detect: mode}));
}

export async function explain(query) {
  const response = await panelFetch('/api/explain-route?q=' + encodeURIComponent(query), {
    credentials: 'same-origin'
  });
  return checked(await response.json());
}
