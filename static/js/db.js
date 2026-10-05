// Локальная база (IndexedDB): культуры, задачи, заметки, кэш AI-ответов.
// Весь пользовательский контент остаётся на устройстве — как в Android-версии (Room).

const DB_NAME = 'ai-botanik';
const DB_VERSION = 1;

let dbPromise = null;

function openDB() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains('cultures')) {
        db.createObjectStore('cultures', { keyPath: 'id' });
      }
      if (!db.objectStoreNames.contains('tasks')) {
        const s = db.createObjectStore('tasks', { keyPath: 'id' });
        s.createIndex('cultureId', 'cultureId');
        s.createIndex('month', 'month');
      }
      if (!db.objectStoreNames.contains('notes')) {
        const s = db.createObjectStore('notes', { keyPath: 'id' });
        s.createIndex('cultureId', 'cultureId');
      }
      if (!db.objectStoreNames.contains('ai_cache')) {
        db.createObjectStore('ai_cache', { keyPath: 'key' });
      }
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
  const req = fn(t.objectStore(store));
  return promisify(req);
}

// ── Культуры ──────────────────────────────────────────────

export async function getCultures() {
  const list = await run('cultures', 'readonly', (s) => s.getAll());
  return (list || []).sort((a, b) => (a.createdAt || 0) - (b.createdAt || 0));
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
    const t = db.transaction(['cultures', 'tasks', 'notes'], 'readwrite');
    t.objectStore('cultures').delete(id);
    const ti = t.objectStore('tasks').index('cultureId');
    ti.openCursor().onsuccess = (e) => {
      const cur = e.target.result;
      if (cur) { cur.delete(); cur.continue(); }
    };
    const ni = t.objectStore('notes').index('cultureId');
    ni.openCursor().onsuccess = (e) => {
      const cur = e.target.result;
      if (cur) { cur.delete(); cur.continue(); }
    };
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

// Общие заметки по саду (cultureId = null). null не индексируется в IndexedDB,
// поэтому фильтруем из полного списка.
export async function getGeneralNotes() {
  const all = await run('notes', 'readonly', (s) => s.getAll());
  return (all || [])
    .filter((n) => n.cultureId == null)
    .sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0));
}
export function putNote(note) {
  return run('notes', 'readwrite', (s) => s.put(note));
}
export function deleteNote(id) {
  return run('notes', 'readwrite', (s) => s.delete(id));
}

// ── Кэш AI-ответов (локальный, не тратит лимит и работает офлайн) ──

export function cacheGet(key) {
  return run('ai_cache', 'readonly', (s) => s.get(key));
}
export function cachePut(key, text, model) {
  return run('ai_cache', 'readwrite', (s) => s.put({ key, text, model, createdAt: Date.now() }));
}
export function cacheClear() {
  return run('ai_cache', 'readwrite', (s) => s.clear());
}
