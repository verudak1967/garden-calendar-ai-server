// Главный модуль: hash-роутинг, экраны, связка UI ↔ локальная БД ↔ AI-сервер.
// Модель данных синхронизирована с Android-версией (Room):
//   Culture { name, variety, sowingDate, transplantDate, quantity, plantingType }
//   Task    { cultureId|null, title, description, dueDate 'YYYY-MM-DD', status }
//   Note    { cultureId, title, content }

import * as api from './api.js';
import * as db from './db.js';
import { icon } from './icons.js';
import { renderMarkdown } from './md.js';
import {
  esc, toast, setLoading, hideLoading, openModal, closeModal, confirmDialog, uuid,
  formatDateRu, dateFieldHTML, syncDateField, initDateFieldPicker, toISODate,
  parseISODate, addDays, usdaZoneToDayShift, shiftMMDD, determinePlanYear,
} from './ui.js';

const view = document.getElementById('view');
const pageTitle = document.getElementById('page-title');
const usageBadge = document.getElementById('usage-badge');

// ── Константы ──────────────────────────────────────────────

const ZONES = {
  1: '1 — Якутия, Оймякон (до −46 °C)',
  2: '2 — Новосибирск, Красноярск (−46…−40 °C)',
  3: '3 — Архангельск, Мурманск, Камчатка (−40…−34 °C)',
  4: '4 — Хабаровск, Иркутск, Кемерово (−34…−29 °C)',
  5: '5 — Москва, Урал, Поволжье (−29…−23 °C)',
  6: '6 — Воронеж, Калининград, Курск (−23…−18 °C)',
  7: '7 — Ростов-на-Дону, Ставрополь (−18…−12 °C)',
  8: '8 — Астрахань, Волгоград, Кавказ (−12…−7 °C)',
  9: '9 — Сочи, Ялта, Крым (−7…−1 °C)',
};

const PLANTING_TYPES = {
  OPEN_GROUND: 'Открытый грунт',
  GREENHOUSE: 'Закрытый грунт',
  SEEDLING: 'Рассада',
};

const PHASES = {
  P0: 'Покой', P1: 'Пробуждение', P2: 'Рост', P3: 'Бутонизация',
  P4: 'Цветение', P5: 'Завязывание', P6: 'Рост плодов', P7: 'Созревание',
  P8: 'Завершение', P9: 'Переход в покой',
};

// Запросы — один в один с RealAiService.kt (префиксы матчатся на сервере)
const AI_TABS = {
  care:     { query: (n) => `Расскажи про уход за культурой: ${n}`,           label: 'Уход' },
  pests:    { query: (n) => `Какие вредители опасны для культуры: ${n}? Как бороться?`, label: 'Вредители' },
  diseases: { query: (n) => `Какие болезни бывают у культуры: ${n}? Как лечить?`, label: 'Болезни' },
};

function getSettings() {
  return { region_zone: Number(localStorage.getItem('region_zone') || 5) };
}

function aiCacheKey(tab, culture, settings) {
  return JSON.stringify([AI_TABS[tab].query(culture.name), culture.variety || '', settings.region_zone]);
}

async function refreshUsageBadge() {
  try {
    const u = await api.getUsage();
    usageBadge.hidden = false;
    usageBadge.textContent = `AI: ${u.used}/${u.limit} сегодня`;
  } catch { /* тихо: бейдж не критичен */ }
}

// Нормализация старых записей (month/day → dueDate, done → status)
function normTask(t) {
  if (!t.dueDate && t.month) {
    t.dueDate = `${new Date().getFullYear()}-${String(t.month).padStart(2, '0')}-${String(t.day).padStart(2, '0')}`;
  }
  if (!t.status) t.status = t.done ? 'completed' : 'planned';
  return t;
}

// ── Роутинг ────────────────────────────────────────────────

const routes = [
  { re: /^#garden$/,                  fn: screenGarden,         title: 'Мой сад',     nav: 'garden' },
  { re: /^#culture\/([^/]+)\/(\w+)/,  fn: screenCulture,        title: 'Культура',    nav: 'garden' },
  { re: /^#templates$/,               fn: screenTemplates,      title: 'Шаблоны планов', nav: 'garden' },
  { re: /^#templates\/([\w-]+)$/,     fn: screenTemplateDetail, title: 'Шаблон',      nav: 'garden' },
  { re: /^#calendar$/,                fn: screenCalendar,       title: 'Календарь',   nav: 'calendar' },
  { re: /^#ai$/,                      fn: screenAiHandbook,     title: 'Справка AI',  nav: 'ai' },
  { re: /^#photo$/,                   fn: screenPhoto,          title: 'Анализ фото', nav: 'photo' },
  { re: /^#settings$/,                fn: screenSettings,       title: 'Ещё',         nav: 'settings' },
];

async function route() {
  const hash = location.hash || '#garden';
  for (const r of routes) {
    const m = hash.match(r.re);
    if (m) {
      document.querySelectorAll('.bottomnav a').forEach((a) =>
        a.classList.toggle('active', a.dataset.nav === r.nav));
      view.innerHTML = '';
      try {
        await r.fn(m);
      } catch (e) {
        view.innerHTML = `<div class="empty">${esc(e.message || 'Ошибка')}</div>`;
      }
      return;
    }
  }
  location.hash = '#garden';
}

window.addEventListener('hashchange', route);

// ── Экран «Мой сад» ────────────────────────────────────────

async function screenGarden() {
  const cultures = await db.getCultures();
  pageTitle.textContent = 'Мой сад';

  // Первый запуск: выбор климатической зоны (паритет FirstTimeRegionDialog)
  if (!localStorage.getItem('onboarded')) showFirstZoneDialog();

  const sub = (c) => [
    c.variety,
    PLANTING_TYPES[c.plantingType] || null,
    c.quantity ? `${c.quantity} шт.` : null,
  ].filter(Boolean).join(' · ');

  view.innerHTML = (cultures.length
    ? `<div class="card"><h2>Мои растения</h2>
        ${cultures.map((c) => `
          <div class="culture-item" data-action="open-culture" data-id="${esc(c.id)}">
            <div class="culture-emoji">${icon('sprout')}</div>
            <div style="flex:1">
              <div class="culture-name">${esc(c.name)}</div>
              <div class="culture-sub">${esc(sub(c) || 'детали не указаны')}</div>
            </div>
            <span class="muted">›</span>
          </div>`).join('')}
      </div>`
    : `<div class="empty"><span class="big">${icon('sprout', 56)}</span>Здесь будут ваши растения.<br>Добавьте своё или возьмите готовый шаблон.</div>`)
    + `<button class="btn secondary" data-action="open-templates">${icon('list', 18)} Готовые шаблоны планов</button>
       <button class="fab" data-action="add-culture" aria-label="Добавить">${icon('plus', 26)}</button>`;

  refreshUsageBadge();
}

function showFirstZoneDialog() {
  const back = openModal(`
    <h2>Ваш климатический регион?</h2>
    <p class="muted small">Зона USDA влияет на советы AI, генерацию планов и даты шаблонов. Позже можно изменить в «Ещё».</p>
    <label class="field"><select id="ob-zone">
      ${Object.entries(ZONES).map(([k, v]) => `<option value="${k}" ${k === '5' ? 'selected' : ''}>${v}</option>`).join('')}
    </select></label>
    <div class="modal-actions">
      <button class="btn" id="ob-save">Продолжить</button>
    </div>`);
  back.querySelector('#ob-save').addEventListener('click', () => {
    localStorage.setItem('region_zone', back.querySelector('#ob-zone').value);
    localStorage.setItem('onboarded', '1');
    closeModal();
    toast('Зона сохранена');
  });
}

function showAddCultureModal() {
  const back = openModal(`
    <h2>Новое растение</h2>
    <label class="field"><span>Название *</span>
      <input type="text" id="c-name" placeholder="Томат, яблоня, фикус…" autocomplete="off"></label>
    <label class="field"><span>Сорт</span>
      <input type="text" id="c-variety" placeholder="Санька, Антоновка…" autocomplete="off"></label>
    <label class="field"><span>Тип посадки</span>
      <select id="c-type">
        <option value="OPEN_GROUND">Открытый грунт</option>
        <option value="GREENHOUSE">Закрытый грунт (теплица)</option>
        <option value="SEEDLING">Рассада</option>
      </select></label>
    <div class="field"><span>Посев</span>${dateFieldHTML('c-sowing', '', 'дд.мм.гггг')}</div>
    <div class="field"><span>Высадка</span>${dateFieldHTML('c-transplant', '', 'дд.мм.гггг')}</div>
    <label class="field"><span>Количество</span>
      <input type="number" id="c-qty" min="1" inputmode="numeric" placeholder="например, 6"></label>
    <div class="modal-actions">
      <button class="btn secondary" data-close>Отмена</button>
      <button class="btn" id="c-save">Добавить</button>
    </div>`);
  back.querySelector('#c-save').addEventListener('click', async () => {
    const name = back.querySelector('#c-name').value.trim();
    if (!name) { toast('Введите название растения'); return; }
    await db.putCulture({
      id: uuid(),
      name,
      variety: back.querySelector('#c-variety').value.trim() || null,
      plantingType: back.querySelector('#c-type').value,
      sowingDate: back.querySelector('#c-sowing').value || null,
      transplantDate: back.querySelector('#c-transplant').value || null,
      quantity: Number(back.querySelector('#c-qty').value) || null,
      createdAt: Date.now(),
    });
    closeModal();
    route();
  });
  back.querySelector('#c-name').focus();
}

// ── Экран «Культура» (табы) ────────────────────────────────

async function screenCulture(m) {
  const id = m[1];
  let tab = m[2];
  if (!['tasks', 'care', 'pests', 'diseases', 'notes'].includes(tab)) tab = 'tasks';

  const culture = await db.getCulture(id);
  if (!culture) { location.hash = '#garden'; return; }
  pageTitle.textContent = culture.name + (culture.variety ? ` (${culture.variety})` : '');

  const tabs = [
    ['tasks', 'Задачи'], ['care', 'Уход'], ['pests', 'Вредители'],
    ['diseases', 'Болезни'], ['notes', 'Заметки'],
  ];
  view.innerHTML = `
    <div class="tabs">${tabs.map(([key, label]) =>
      `<div class="tab ${key === tab ? 'active' : ''}" data-action="culture-tab" data-tab="${key}">${label}</div>`).join('')}
    </div>
    <div id="tab-body"></div>
    <button class="btn danger small mt" data-action="delete-culture" data-id="${esc(id)}">Удалить растение</button>`;

  const body = view.querySelector('#tab-body');
  if (tab === 'tasks') await renderTasksTab(body, culture);
  else if (tab === 'notes') await renderNotesTab(body, culture);
  else await renderAiTab(body, culture, tab);
}

// — Таб «Задачи» —

function taskRow(t, showCulture = false, cultureName = '') {
  const done = t.status === 'completed';
  return `
    <div class="task ${done ? 'done' : ''}">
      <input type="checkbox" ${done ? 'checked' : ''} data-action="toggle-task" data-id="${esc(t.id)}">
      <div style="flex:1">
        <div class="task-title">${esc(t.title)}</div>
        ${t.description ? `<div class="task-desc">${esc(t.description)}</div>` : ''}
        ${showCulture && cultureName ? `<div class="task-desc">${icon('sprout', 14)} ${esc(cultureName)}</div>` : ''}
      </div>
      <span class="task-date">${formatDateRu(t.dueDate)}</span>
    </div>`;
}

async function renderTasksTab(body, culture) {
  const tasks = (await db.getTasksByCulture(culture.id)).map(normTask);
  body.innerHTML = `
    <div class="card">
      <h2>План работ на год</h2>
      <label class="field"><span>Текущая фаза вегетации</span>
        <select id="phase-select">${Object.entries(PHASES).map(([k, v]) =>
          `<option value="${k}" ${k === 'P2' ? 'selected' : ''}>${k}. ${v}</option>`).join('')}</select></label>
      <button class="btn" data-action="gen-plan" data-id="${esc(culture.id)}">Сформировать план (1 AI-запрос)</button>
      <p class="muted small mt">План строится от текущей фазы до конца сезона. Повторная генерация заменит прежние AI-задачи.</p>
    </div>
    <div class="card">
      ${tasks.length ? tasks.map((t) => taskRow(t)).join('')
        : '<div class="empty">Задач пока нет.<br>Сформируйте план или добавьте вручную.</div>'}
    </div>
    <button class="btn secondary" data-action="add-task" data-id="${esc(culture.id)}">+ Своя задача</button>`;
}

async function generatePlanFor(cultureId) {
  const culture = await db.getCulture(cultureId);
  const settings = getSettings();
  const phase = (view.querySelector('#phase-select') || {}).value || 'P2';
  setLoading('Составляем план на год… Если сервер спал — до минуты.');
  try {
    const resp = await api.generatePlan({
      culture_name: culture.name,
      variety: culture.variety || '',
      region_zone: settings.region_zone,
      phase,
    });
    await db.deleteAiTasks(cultureId);
    // month/day → полная дата; если большинство дат уже прошло — следующий год
    const mmdds = resp.tasks.map((t) => `${String(t.month).padStart(2, '0')}-${String(t.day).padStart(2, '0')}`);
    const year = determinePlanYear(mmdds, 0);
    for (const t of resp.tasks) {
      await db.putTask({
        id: uuid(),
        cultureId,
        title: t.title,
        description: t.description,
        dueDate: `${year}-${String(t.month).padStart(2, '0')}-${String(t.day).padStart(2, '0')}`,
        status: 'planned',
        completedDate: null,
        source: 'ai',
        createdAt: Date.now(),
      });
    }
    toast(`Готово: ${resp.tasks.length} задач${resp.from_cache ? ' (из кэша сервера)' : ''}`);
    await refreshUsageBadge();
    route();
  } catch (e) {
    toast(e.message, 4500);
  } finally {
    hideLoading();
  }
}

function showAddTaskModal(cultureId, presetDate) {
  const back = openModal(`
    <h2>${cultureId ? 'Своя задача' : 'Общая задача по саду'}</h2>
    <label class="field"><span>Название *</span>
      <input type="text" id="t-title" placeholder="Полить, подкормить…" autocomplete="off"></label>
    <label class="field"><span>Описание</span>
      <input type="text" id="t-desc" autocomplete="off"></label>
    <div class="field"><span>Дата</span>${dateFieldHTML('t-date', presetDate || toISODate(new Date()))}</div>
    <div class="modal-actions">
      <button class="btn secondary" data-close>Отмена</button>
      <button class="btn" id="t-save">Добавить</button>
    </div>`);
  back.querySelector('#t-save').addEventListener('click', async () => {
    const title = back.querySelector('#t-title').value.trim();
    if (!title) { toast('Введите название'); return; }
    await db.putTask({
      id: uuid(), cultureId: cultureId || null,
      title,
      description: back.querySelector('#t-desc').value.trim() || null,
      dueDate: back.querySelector('#t-date').value || toISODate(new Date()),
      status: 'planned', completedDate: null,
      source: 'manual', createdAt: Date.now(),
    });
    closeModal();
    route();
  });
  back.querySelector('#t-title').focus();
}

// — Табы AI —

async function renderAiTab(body, culture, tab) {
  const settings = getSettings();
  const key = aiCacheKey(tab, culture, settings);
  const cached = await db.cacheGet(key);

  body.innerHTML = `
    <div class="card" id="ai-result">
      ${cached
        ? `<div class="muted small">из локального кэша · модель: ${esc(cached.model || '')}</div>
           <div class="md">${renderMarkdown(cached.text)}</div>
           <button class="btn secondary small mt" data-action="ai-reload" data-tab="${tab}">Обновить</button>`
        : `<div class="empty"><span class="big">${icon('book', 56)}</span>Ответ AI ещё не загружен.<br>Запрос тратит 1 из дневных лимитов (кэш — бесплатно).</div>
           <button class="btn" data-action="ai-load" data-tab="${tab}">Спросить AI (${esc(AI_TABS[tab].label.toLowerCase())})</button>`}
    </div>`;
}

async function loadAiTab(cultureId, tab) {
  const culture = await db.getCulture(cultureId);
  const settings = getSettings();
  setLoading('AI думает… Если сервер спал — до минуты.');
  try {
    const resp = await api.ask({
      query: AI_TABS[tab].query(culture.name),
      request_type: tab,
      context: culture.name,
      culture_name: culture.name,
      variety: culture.variety || '',
      region_zone: settings.region_zone,
    });
    await db.cachePut(aiCacheKey(tab, culture, settings), resp.text, resp.model);
    toast(`Лимит: использовано ${resp.used} из ${resp.limit}`);
    await refreshUsageBadge();
    route();
  } catch (e) {
    toast(e.message, 4500);
  } finally {
    hideLoading();
  }
}

// — Таб «Заметки» —

async function renderNotesTab(body, culture) {
  const notes = await db.getNotesByCulture(culture.id);
  body.innerHTML = `
    <div class="card">
      <h2>Заметки</h2>
      <label class="field"><span>Заголовок</span>
        <input type="text" id="note-title" placeholder="Подкормка, наблюдение…"></label>
      <textarea id="note-text" placeholder="Например: подкормила 12 мая мочевиной…"></textarea>
      <button class="btn secondary small mt" data-action="add-note" data-id="${esc(culture.id)}">Добавить заметку</button>
    </div>
    ${notes.length ? notes.map((n) => `
      <div class="card">
        <div class="task-title">${esc(n.title || 'Заметка')}</div>
        <div class="muted small">${new Date(n.createdAt).toLocaleString('ru-RU')}</div>
        <p style="margin:6px 0 0">${esc(n.content || n.text || '')}</p>
        <button class="btn danger small mt" data-action="del-note" data-id="${esc(n.id)}">Удалить</button>
      </div>`).join('')
    : '<div class="empty">Заметок пока нет</div>'}`;
}

// ── Шаблоны планов (паритет TemplatesScreen + templates.json) ──

let templatesCache = null;
async function loadTemplates() {
  if (templatesCache) return templatesCache;
  const resp = await fetch('./templates.json');
  templatesCache = await resp.json();
  return templatesCache;
}

async function screenTemplates() {
  pageTitle.textContent = 'Шаблоны планов';
  const templates = await loadTemplates();
  const shift = usdaZoneToDayShift(getSettings().region_zone);
  view.innerHTML = `
    <div class="card">
      <p class="muted small">Готовые планы работ по культурам. Даты сдвинуты на ${shift} дн. относительно средней полосы под вашу зону.</p>
    </div>
    ${templates.map((t) => `
      <div class="card" style="cursor:pointer" data-action="open-template" data-id="${esc(t.id)}">
        <div class="culture-name">${esc(t.name)}</div>
        <div class="culture-sub">${esc(t.type)} · ${t.tasks.length} задач</div>
        <p class="small" style="margin:6px 0 0">${esc(t.description || '')}</p>
      </div>`).join('')}`;
}

async function screenTemplateDetail(m) {
  const templates = await loadTemplates();
  const t = templates.find((x) => x.id === m[1]);
  if (!t) { location.hash = '#templates'; return; }
  pageTitle.textContent = t.name;

  const zone = getSettings().region_zone;
  const shift = usdaZoneToDayShift(zone);
  const year = determinePlanYear(t.tasks.map((x) => x.date), shift);

  view.innerHTML = `
    <div class="card">
      <h2>${esc(t.name)}</h2>
      <div class="culture-sub">${esc(t.type)}</div>
      <p class="small">${esc(t.description || '')}</p>
    </div>
    <div class="card">
      <h2>План (${year} г., зона ${zone})</h2>
      ${t.tasks.map((x) => `
        <div class="task">
          <div style="flex:1">
            <div class="task-title">${esc(x.title)}</div>
            ${x.description ? `<div class="task-desc">${esc(x.description)}</div>` : ''}
          </div>
          <span class="task-date">${formatDateRu(shiftMMDD(x.date, shift, year))}</span>
        </div>`).join('')}
    </div>
    <button class="btn" data-action="apply-template" data-id="${esc(t.id)}">Добавить в мой сад</button>`;
}

async function applyTemplate(templateId) {
  const templates = await loadTemplates();
  const t = templates.find((x) => x.id === templateId);
  if (!t) return;
  const shift = usdaZoneToDayShift(getSettings().region_zone);
  const year = determinePlanYear(t.tasks.map((x) => x.date), shift);

  const cultureId = uuid();
  await db.putCulture({
    id: cultureId,
    name: t.name,
    variety: null,
    plantingType: 'OPEN_GROUND',
    sowingDate: null,
    transplantDate: toISODate(new Date()),
    quantity: null,
    createdAt: Date.now(),
  });
  for (const x of t.tasks) {
    await db.putTask({
      id: uuid(),
      cultureId,
      title: x.title,
      description: x.description || null,
      dueDate: shiftMMDD(x.date, shift, year),
      status: 'planned',
      completedDate: null,
      source: 'template',
      createdAt: Date.now(),
    });
  }
  toast(`«${t.name}» добавлен: ${t.tasks.length} задач`);
  location.hash = `#culture/${cultureId}/tasks`;
}

// ── Экран «Календарь» ──────────────────────────────────────

async function screenCalendar() {
  pageTitle.textContent = 'Календарь';
  const selected = localStorage.getItem('cal_date') || toISODate(new Date());

  view.innerHTML = `
    <div class="card">
      <div class="field"><span>Выберите дату</span>${dateFieldHTML('cal-date', selected)}</div>
    </div>
    <div id="cal-body"></div>
    <button class="btn secondary" data-action="add-common-task">+ Общая задача по саду</button>`;

  const render = async () => {
    const val = view.querySelector('#cal-date').value || toISODate(new Date());
    localStorage.setItem('cal_date', val);
    const day = parseISODate(val);
    const cultures = await db.getCultures();
    const names = Object.fromEntries(cultures.map((c) => [c.id, c.name]));
    const all = (await db.getAllTasks()).map(normTask);

    const inRange = (from, to) => all.filter((t) => {
      if (!t.dueDate) return false;
      const d = t.dueDate;
      return d >= toISODate(from) && d <= toISODate(to);
    });

    const dayTasks = inRange(day, day);
    const end = addDays(day, 13);
    const upcoming = inRange(addDays(day, 1), end);

    view.querySelector('#cal-body').innerHTML = `
      <div class="card">
        <h2>Задачи на ${formatDateRu(val)}</h2>
        ${dayTasks.length ? dayTasks.map((t) => taskRow(t, true, names[t.cultureId] || 'Общая')).join('')
          : '<div class="empty">На эту дату задач нет</div>'}
      </div>
      <div class="card">
        <h2>Ближайшие 2 недели</h2>
        ${upcoming.length ? upcoming.map((t) => taskRow(t, true, names[t.cultureId] || 'Общая')).join('')
          : '<div class="empty">Пусто</div>'}
      </div>`;
  };

  view.querySelector('#cal-date').addEventListener('change', render);
  await render();
}

// ── Экран «Справка AI» ─────────────────────────────────────

async function screenAiHandbook() {
  pageTitle.textContent = 'Справка AI';
  const history = JSON.parse(localStorage.getItem('ai_history') || '[]');
  view.innerHTML = `
    <div class="card">
      <h2>Вопрос агроному</h2>
      <textarea id="ai-query" placeholder="Например: чем подкормить рассаду томатов в мае?"></textarea>
      <button class="btn mt" data-action="ai-ask">Спросить (1 запрос лимита)</button>
      <p class="muted small mt">Отвечаем только на вопросы о саде, огороде и растениях.</p>
    </div>
    ${history.length ? `
      <div class="card">
        <h2>История</h2>
        ${history.map((h) => `
          <div class="culture-item" data-action="ai-history" data-q="${esc(h.q)}">
            <div class="culture-emoji">${icon('chat')}</div>
            <div style="flex:1">
              <div class="culture-name" style="font-weight:500">${esc(h.q)}</div>
              <div class="culture-sub">${new Date(h.ts).toLocaleString('ru-RU')} · нажмите, чтобы повторить</div>
            </div>
          </div>`).join('')}
        <button class="btn secondary small mt" data-action="ai-clear-history">Очистить историю</button>
      </div>` : ''}`;
  refreshUsageBadge();
}

async function askFree(predefined) {
  const ta = view.querySelector('#ai-query');
  const q = (predefined ?? (ta ? ta.value : '')).trim();
  if (!q) { toast('Введите вопрос'); return; }
  setLoading('AI думает… Если сервер спал — до минуты.');
  try {
    const resp = await api.ask({ query: q, request_type: 'free' });
    const history = JSON.parse(localStorage.getItem('ai_history') || '[]');
    history.unshift({ q, ts: Date.now() });
    localStorage.setItem('ai_history', JSON.stringify(history.slice(0, 10)));

    openModal(`
      <h2>Ответ AI</h2>
      <div class="md">${renderMarkdown(resp.text)}</div>
      <div class="muted small mt">модель: ${esc(resp.model || '')} · лимит: ${resp.used}/${resp.limit}</div>
      <div class="modal-actions mt">
        <button class="btn" data-close>Закрыть</button>
      </div>`);
    await refreshUsageBadge();
    if (predefined === undefined) route();
  } catch (e) {
    toast(e.message, 4500);
  } finally {
    hideLoading();
  }
}

// ── Экран «Анализ фото» ────────────────────────────────────

async function screenPhoto() {
  pageTitle.textContent = 'Анализ фото';
  usageBadge.hidden = true;
  view.innerHTML = `
    <div class="card">
      <h2>Что с растением?</h2>
      <p class="muted">Сфотографируйте растение — AI определит вид, болезни, вредителей и подскажет, что делать.</p>
      <input type="file" id="photo-file" accept="image/*" style="display:none">
      <button class="btn" data-action="pick-photo">${icon('camera', 20)} Выбрать / сделать фото</button>
      <img id="photo-preview" class="photo-preview mt">
      <label class="field mt"><span>Пояснение (необязательно)</span>
        <input type="text" id="photo-context" placeholder="Например: что за пятна на листьях смородины?"></label>
      <button class="btn secondary" data-action="analyze-photo" disabled id="analyze-btn">Проанализировать (1 запрос лимита)</button>
      <div id="photo-result" class="mt"></div>
    </div>`;
}

function setupPhotoInput(file) {
  const img = view.querySelector('#photo-preview');
  const btn = view.querySelector('#analyze-btn');
  const reader = new FileReader();
  reader.onload = (e) => { img.src = e.target.result; img.classList.add('visible'); btn.disabled = false; };
  reader.readAsDataURL(file);
}

async function downscaleToBase64(file, maxSide = 1024, quality = 0.8) {
  const dataUrl = await new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(r.result);
    r.onerror = rej;
    r.readAsDataURL(file);
  });
  const img = await new Promise((res, rej) => {
    const i = new Image();
    i.onload = () => res(i);
    i.onerror = rej;
    i.src = dataUrl;
  });
  const scale = Math.min(1, maxSide / Math.max(img.width, img.height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(img.width * scale);
  canvas.height = Math.round(img.height * scale);
  canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL('image/jpeg', quality).split(',')[1];
}

async function analyzePhoto() {
  const fileInput = view.querySelector('#photo-file');
  if (!fileInput.files || !fileInput.files[0]) { toast('Сначала выберите фото'); return; }
  setLoading('Сжимаем фото и спрашиваем AI…');
  let base64;
  try {
    base64 = await downscaleToBase64(fileInput.files[0]);
  } catch {
    hideLoading();
    toast('Не удалось обработать изображение');
    return;
  }
  try {
    const resp = await api.askPhoto({
      image_base64: base64,
      context: view.querySelector('#photo-context').value.trim(),
    });
    view.querySelector('#photo-result').innerHTML = `
      <div class="card md">${renderMarkdown(resp.text)}
      <div class="muted small mt">модель: ${esc(resp.model || '')} · лимит: ${resp.used}/${resp.limit}</div></div>`;
    await refreshUsageBadge();
  } catch (e) {
    toast(e.message, 4500);
  } finally {
    hideLoading();
  }
}

// ── Экран «Ещё / Настройки» ────────────────────────────────

async function screenSettings() {
  pageTitle.textContent = 'Ещё';
  const s = getSettings();
  view.innerHTML = `
    <div class="card">
      <h2>Климатическая зона (USDA)</h2>
      <p class="muted small">Влияет на советы AI, генерацию планов и сдвиг дат в шаблонах.</p>
      <label class="field"><select id="zone-select">
        ${Object.entries(ZONES).map(([k, v]) =>
          `<option value="${k}" ${Number(k) === s.region_zone ? 'selected' : ''}>${v}</option>`).join('')}
      </select></label>
    </div>
    <div class="card">
      <h2>Данные</h2>
      <p class="muted small">Все растения, задачи и заметки хранятся только на этом устройстве.</p>
      <button class="btn secondary" data-action="clear-cache">Очистить кэш AI-ответов</button>
    </div>
    <div class="card">
      <h2>О приложении</h2>
      <p class="muted small">Веб-версия «AI Ботаник» для тестирования. Android-версия — в RuStore.</p>
      <p class="small"><a href="https://privacy.integroai.ru" target="_blank" rel="noopener">Политика конфиденциальности</a></p>
      <p class="muted small">Бесплатный лимит: 10 AI-запросов в день на устройство. Повторные запросы из кэша не расходуются.</p>
    </div>`;
}

// ── Делегирование событий ──────────────────────────────────

view.addEventListener('click', async (e) => {
  const el = e.target.closest('[data-action]');
  if (!el) return;
  const action = el.dataset.action;

  try {
    switch (action) {
      case 'add-culture': showAddCultureModal(); break;
      case 'open-culture': location.hash = `#culture/${el.dataset.id}/tasks`; break;
      case 'culture-tab': {
        const id = location.hash.match(/^#culture\/([^/]+)/)[1];
        location.hash = `#culture/${id}/${el.dataset.tab}`;
        break;
      }
      case 'delete-culture': {
        const id = el.dataset.id;
        if (await confirmDialog('Удалить растение?', 'Будут удалены также его задачи и заметки. Действие необратимо.')) {
          await db.deleteCulture(id);
          toast('Растение удалено');
          location.hash = '#garden';
        }
        break;
      }
      case 'gen-plan': await generatePlanFor(el.dataset.id); break;
      case 'add-task': showAddTaskModal(el.dataset.id); break;
      case 'add-common-task': showAddTaskModal(null, localStorage.getItem('cal_date') || toISODate(new Date())); break;
      case 'toggle-task': {
        const t = normTask(await db.getTask(el.dataset.id));
        if (t) {
          t.status = el.checked ? 'completed' : 'planned';
          t.completedDate = el.checked ? toISODate(new Date()) : null;
          await db.putTask(t);
          route();
        }
        break;
      }
      case 'ai-load': case 'ai-reload': {
        const id = location.hash.match(/^#culture\/([^/]+)/)[1];
        await loadAiTab(id, el.dataset.tab);
        break;
      }
      case 'add-note': {
        const title = view.querySelector('#note-title').value.trim();
        const content = view.querySelector('#note-text').value.trim();
        if (!content) { toast('Введите текст заметки'); return; }
        await db.putNote({ id: uuid(), cultureId: el.dataset.id, title: title || null, content, createdAt: Date.now() });
        route();
        break;
      }
      case 'del-note': await db.deleteNote(el.dataset.id); route(); break;
      case 'ai-ask': await askFree(); break;
      case 'ai-history': await askFree(el.dataset.q); break;
      case 'ai-clear-history': localStorage.removeItem('ai_history'); route(); break;
      case 'pick-photo': view.querySelector('#photo-file').click(); break;
      case 'analyze-photo': await analyzePhoto(); break;
      case 'open-templates': location.hash = '#templates'; break;
      case 'open-template': location.hash = `#templates/${el.dataset.id}`; break;
      case 'apply-template': await applyTemplate(el.dataset.id); break;
    }
  } catch (err) {
    toast(err.message || 'Ошибка', 4000);
  }
});

view.addEventListener('change', (e) => {
  if (e.target.id === 'photo-file') setupPhotoInput(e.target.files[0]);
  if (e.target.id === 'zone-select') {
    localStorage.setItem('region_zone', e.target.value);
    localStorage.setItem('onboarded', '1');
    toast('Зона сохранена: ' + ZONES[e.target.value]);
  }
});

// Единое обновление надписей всех полей дат (календарь + модальные формы)
document.addEventListener('change', (e) => {
  if (e.target.matches('.datefield input[type="date"]')) syncDateField(e.target);
});

// ── Инициализация ──────────────────────────────────────────

// Иконки нижней навигации
const NAV_ICONS = { garden: 'sprout', calendar: 'calendar', ai: 'book', photo: 'camera', settings: 'sliders' };
document.querySelectorAll('.bottomnav a').forEach((a) => {
  const span = a.querySelector('.nav-ico');
  if (span && NAV_ICONS[a.dataset.nav]) span.innerHTML = icon(NAV_ICONS[a.dataset.nav]);
});

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('./sw.js').catch(() => { /* SW не критичен */ });
}
initDateFieldPicker();
route();
