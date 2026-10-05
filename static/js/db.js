// Локальная база (IndexedDB) — модель данных v2.
// v1: cultures, tasks, notes, ai_cache.
// v2: + locations, tags, plant_tags, sowings, harvests, photos,
//     identifications, species_custom (пользовательские виды каталога).
// Весь пользовательский контент остаётся на устройстве.

import { DEFAULT_LOCATIONS, SYSTEM_TAGS, findSpecies } from './catalog.js';

const DB_NAME = 'ai-botanik';
const DB_VERSION = 2;

let dbPromise = null;

function uuid() {
  return (crypto.randomUUID && crypto.randomUUID()) ||
    'id-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
}

function openDB() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains('cultures')) db.createObjectStore('cultures', { keyPath: 'id' });
      if (!db.objectStoreNames.contains('tasks')) {
        const s = db.createObjectStore('tasks', { keyPath: 'id' });
        s.createIndex('cultureId', 'cultureId');
      }
      if (!db.objectStoreNames.contains('notes')) {
        const s = db.createObjectStore('notes', { keyPath: 'id' });
        s.createIndex('cultureId', 'cultureId');
      }
      if (!db.objectStoreNames.contains('ai_cache')) db.createObjectStore('ai_cache', { keyPath: 'key' });
      // ── v2 ──
      if (!db.objectStoreNames.contains('locations')) db.createObjectStore('locations', { keyPath: 'id' });
      if (!db.objectStoreNames.contains('tags')) db.createObjectStore('tags', { keyPath: 'code' });
      if (!db.objectStoreNames.contains('plant_tags')) {
        const s = db.createObjectStore('plant_tags', { keyPath: 'id' });
        s.createIndex('plantId', 'plantId');
        s.createIndex('tagId', 'tagId');
      }
      if (!db.objectStoreNames.contains('sowings')) {
        const s = db.createObjectStore('sowings', { keyPath: 'id' });
        s.createIndex('plantId', 'plantId');
      }
      if (!db.objectStoreNames.contains('harvests')) {
        const s = db.createObjectStore('harvests', { keyPath: 'id' });
        s.createIndex('plantId', 'plantId');
      }
      if (!db.objectStoreNames.contains('photos')) {
        const s = db.createObjectStore('photos', { keyPath: 'id' });
        s.createIndex('plantId', 'plantId');
      }
      if (!db.objectStoreNames.contains('identifications')) db.createObjectStore('identifications', { keyPath: 'id' });
      if (!db.objectStoreNames.contains('species_custom')) db.createObjectStore('species_custom', { keyPath: 'k' });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return dbPromise;
}

function promisify(req) {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function run(store, mode, fn) {
  const db = await openDB();
  const t = db.transaction(store, mode);
  return promisify(fn(t.objectStore(store)));
}

// ── Миграция v1 → v2 (однократно) ─────────────────────────

// Справочники (локации/теги) обновляются при каждом запуске:
// имена по умолчанию могут меняться между версиями приложения
export async function ensureRefData() {
  for (const loc of DEFAULT_LOCATIONS) {
    await run('locations', 'readwrite', (s) => s.put(loc));
  }
  for (const tag of SYSTEM_TAGS) {
    const existing = await run('tags', 'readonly', (s) => s.get(tag.code));
    if (!existing) await run('tags', 'readwrite', (s) => s.put(tag));
  }
}

export async function migrateToV2() {
  await ensureRefData();
  if (localStorage.getItem('schema_v2') === 'done') return;

  const typeToLoc = { OPEN_GROUND: 'loc-open', GREENHOUSE: 'loc-green', SEEDLING: 'loc-seed' };
  const cultures = await getCultures();
  for (const c of cultures) {
    if (c.schema === 2) continue;
    const sp = findSpecies(c.name);
    const updated = { ...c };

    if (c.isIndoor) updated.locationId = 'loc-room';
    else if (c.plantingType && typeToLoc[c.plantingType]) updated.locationId = typeToLoc[c.plantingType];
    else updated.locationId = c.locationId || null;

    if (sp) {
      updated.speciesKey = sp.k;
      updated.groupCode = sp.g;
      updated.subgroupCode = sp.s;
      updated.latinName = sp.latin || null;
    } else {
      updated.speciesKey = null; updated.groupCode = null; updated.subgroupCode = null; updated.latinName = null;
    }
    updated.status = 'ACTIVE';
    updated.source = c.source || 'MANUAL';
    updated.schema = 2;

    // старые даты посева/высадки переезжают в сезонные события
    if (c.sowingDate) {
      await putSowing({ id: uuid(), plantId: c.id, date: c.sowingDate, seasonYear: Number(c.sowingDate.slice(0, 4)), method: 'SEEDLINGS', quantity: c.quantity || null, note: null });
    }
    if (c.transplantDate) {
      await putSowing({ id: uuid(), plantId: c.id, date: c.transplantDate, seasonYear: Number(c.transplantDate.slice(0, 4)), method: 'DIRECT', quantity: c.quantity || null, note: 'высадка' });
    }
    delete updated.plantingType;
    delete updated.sowingDate;
    delete updated.transplantDate;
    delete updated.quantity;
    delete updated.isIndoor;
    await putCulture(updated);
  }
  localStorage.setItem('schema_v2', 'done');
}

// ── Культуры ──────────────────────────────────────────────

export async function getCultures(includeArchived = false) {
  const list = await run('cultures', 'readonly', (s) => s.getAll());
  const res = (list || []).sort((a, b) => (a.createdAt || 0) - (b.createdAt || 0));
  return includeArchived ? res : res.filter((c) => (c.status || 'ACTIVE') === 'ACTIVE');
}
export function getCulture(id) {
  return run('cultures', 'readonly', (s) => s.get(id));
}
export function putCulture(c) {
  return run('cultures', 'readwrite', (s) => s.put(c));
}
export async function deleteCulture(id) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const t = db.transaction(['cultures', 'tasks', 'notes', 'sowings', 'harvests', 'photos', 'plant_tags'], 'readwrite');
    t.objectStore('cultures').delete(id);
    for (const store of ['tasks', 'notes', 'sowings', 'harvests', 'photos', 'plant_tags']) {
      const os = t.objectStore(store);
      const index = os.index(os.indexNames.contains('cultureId') ? 'cultureId' : 'plantId');
      index.openCursor().onsuccess = (e) => {
        const cur = e.target.result;
        if (cur) { cur.delete(); cur.continue(); }
      };
    }
    t.oncomplete = () => resolve();
    t.onerror = () => reject(t.error);
  });
}

// ── Задачи ────────────────────────────────────────────────

export async function getTasksByCulture(cultureId) {
  const all = await run('tasks', 'readonly', (s) => s.index('cultureId').getAll(cultureId));
  return (all || []).sort((a, b) => String(a.dueDate || '').localeCompare(String(b.dueDate || '')));
}
export async function getAllTasks() {
  const all = await run('tasks', 'readonly', (s) => s.getAll());
  return (all || []).sort((a, b) => String(a.dueDate || '').localeCompare(String(b.dueDate || '')));
}
export function getTask(id) {
  return run('tasks', 'readonly', (s) => s.get(id));
}
export function putTask(task) {
  return run('tasks', 'readwrite', (s) => s.put(task));
}
export function deleteTask(id) {
  return run('tasks', 'readwrite', (s) => s.delete(id));
}
export async function deleteAiTasks(cultureId) {
  const tasks = await getTasksByCulture(cultureId);
  await Promise.all(tasks.filter((t) => t.source === 'ai').map((t) => deleteTask(t.id)));
}

// ── Заметки ───────────────────────────────────────────────

export async function getNotesByCulture(cultureId) {
  const all = await run('notes', 'readonly', (s) => s.index('cultureId').getAll(cultureId));
  return (all || []).sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0));
}
export async function getGeneralNotes() {
  const all = await run('notes', 'readonly', (s) => s.getAll());
  return (all || [])
    .filter((n) => n.cultureId == null)
    .sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0));
}
export function putNote(note) {
  return run('notes', 'readwrite', (s) => s.put(note));
}
export function getNote(id) {
  return run('notes', 'readonly', (s) => s.get(id));
}
export function deleteNote(id) {
  return run('notes', 'readwrite', (s) => s.delete(id));
}

// ── Локации ───────────────────────────────────────────────

export async function getLocations() {
  const list = await run('locations', 'readonly', (s) => s.getAll());
  return (list || []).sort((a, b) => a.name.localeCompare(b.name, 'ru'));
}
export function getLocation(id) {
  return run('locations', 'readonly', (s) => s.get(id));
}
export function putLocation(loc) {
  return run('locations', 'readwrite', (s) => s.put(loc));
}

// ── Посевы (сезонные события) ─────────────────────────────

export async function getSowingsByPlant(plantId) {
  const all = await run('sowings', 'readonly', (s) => s.index('plantId').getAll(plantId));
  return (all || []).sort((a, b) => String(b.date || '').localeCompare(String(a.date || '')));
}
export function putSowing(sw) {
  return run('sowings', 'readwrite', (s) => s.put(sw));
}
export function deleteSowing(id) {
  return run('sowings', 'readwrite', (s) => s.delete(id));
}

// ── Урожай ────────────────────────────────────────────────

export async function getHarvestsByPlant(plantId) {
  const all = await run('harvests', 'readonly', (s) => s.index('plantId').getAll(plantId));
  return (all || []).sort((a, b) => String(b.date || '').localeCompare(String(a.date || '')));
}
export function putHarvest(h) {
  return run('harvests', 'readwrite', (s) => s.put(h));
}
export function deleteHarvest(id) {
  return run('harvests', 'readwrite', (s) => s.delete(id));
}

// ── Фото (dataURL, только локально) ───────────────────────

export async function getPhotosByPlant(plantId) {
  const all = await run('photos', 'readonly', (s) => s.index('plantId').getAll(plantId));
  return (all || []).sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0));
}
export function putPhoto(p) {
  return run('photos', 'readwrite', (s) => s.put(p));
}

// ── Идентификации (photo-first) ───────────────────────────

export function putIdentification(i) {
  return run('identifications', 'readwrite', (s) => s.put(i));
}
export function getIdentifications() {
  return run('identifications', 'readonly', (s) => s.getAll());
}

// ── Пользовательские виды каталога ────────────────────────

export function putCustomSpecies(sp) {
  return run('species_custom', 'readwrite', (s) => s.put(sp));
}
export function getCustomSpecies() {
  return run('species_custom', 'readonly', (s) => s.getAll());
}

// ── Теги растений ─────────────────────────────────────────

export function putPlantTag(plantId, tagCode) {
  return run('plant_tags', 'readwrite', (s) => s.put({ id: plantId + '|' + tagCode, plantId, tagId: tagCode }));
}
export async function getPlantTags(plantId) {
  const all = await run('plant_tags', 'readonly', (s) => s.index('plantId').getAll(plantId));
  return (all || []).map((r) => r.tagId);
}

// ── Кэш AI-ответов ────────────────────────────────────────

export function cacheGet(key) {
  return run('ai_cache', 'readonly', (s) => s.get(key));
}
export function cachePut(key, text, model) {
  return run('ai_cache', 'readwrite', (s) => s.put({ key, text, model, createdAt: Date.now() }));
}
export function cacheClear() {
  return run('ai_cache', 'readwrite', (s) => s.clear());
}
