// Утилиты UI: экранирование, тосты, оверлей загрузки, модальные окна.

export function esc(s) {
  return String(s ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

let toastTimer = null;
export function toast(msg, ms = 2600) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, ms);
}

export function setLoading(text) {
  const ov = document.getElementById('overlay');
  document.getElementById('overlay-text').textContent = text || 'Загрузка…';
  ov.hidden = false;
}
export function hideLoading() {
  document.getElementById('overlay').hidden = true;
}

export function openModal(html) {
  const root = document.getElementById('modal-root');
  root.innerHTML = `<div class="modal-back" data-close-back><div class="modal">${html}</div></div>`;
  const back = root.firstElementChild;
  back.addEventListener('click', (e) => {
    if (e.target === back || e.target.closest('[data-close]')) closeModal();
  });
  return back;
}
export function closeModal() {
  document.getElementById('modal-root').innerHTML = '';
}

export function confirmDialog(title, message) {
  return new Promise((resolve) => {
    const back = openModal(`
      <h2>${esc(title)}</h2>
      <p class="muted">${esc(message)}</p>
      <div class="modal-actions">
        <button class="btn secondary" data-close>Отмена</button>
        <button class="btn danger" data-confirm>Подтвердить</button>
      </div>`);
    back.querySelector('[data-confirm]').addEventListener('click', () => { closeModal(); resolve(true); });
    back.addEventListener('click', (e) => { if (e.target === back || e.target.closest('[data-close]')) resolve(false); });
  });
}

export function uuid() {
  return (crypto.randomUUID && crypto.randomUUID()) ||
    'id-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
}

// Дата задачи в русском формате: '2026-05-14' → '14.05.2026'
export function formatDateRu(iso) {
  if (!iso) return '';
  const [y, m, d] = iso.split('-').map(Number);
  return `${pad2(d)}.${pad2(m)}.${y}`;
}

import { icon } from './icons.js';

// Поле даты с единым отображением дд.мм.гггг: своя надпись + прозрачный
// нативный input поверх (тап открывает системный календарь устройства).
export function dateFieldHTML(id, value, placeholder = 'Выберите дату') {
  const shown = value ? formatDateRu(value) : esc(placeholder);
  return `<label class="datefield">
    <span class="datefield-value${value ? '' : ' empty'}">${shown}</span>
    <span class="datefield-ico">${icon('calendar', 18)}</span>
    <input type="date" id="${id}" value="${value || ''}">
  </label>`;
}

// Тап в любое место поля открывает календарь принудительно:
// на десктопном Chrome прозрачный input сам пикер не открывает.
export function initDateFieldPicker() {
  document.addEventListener('click', (e) => {
    const field = e.target.closest('.datefield');
    if (!field) return;
    const inp = field.querySelector('input[type="date"]');
    if (!inp) return;
    try {
      inp.focus();
      if (typeof inp.showPicker === 'function') inp.showPicker();
    } catch { /* браузер открыл пикер сам по фокусу — это тоже норм */ }
  });
}

// Обновить надпись после выбора даты (навешивается глобально на change)
export function syncDateField(inputEl) {
  const wrap = inputEl.closest('.datefield');
  if (!wrap) return;
  const span = wrap.querySelector('.datefield-value');
  if (inputEl.value) {
    span.textContent = formatDateRu(inputEl.value);
    span.classList.remove('empty');
  } else {
    span.textContent = 'Выберите дату';
    span.classList.add('empty');
  }
}

// ── Дата-утилиты, паритет с Android (GardenViewModel/TemplatesScreen) ──

export const pad2 = (n) => String(n).padStart(2, '0');
export const toISODate = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
export function parseISODate(s) {
  const [y, m, d] = String(s).split('-').map(Number);
  return new Date(y, m - 1, d);
}
export function addDays(d, n) {
  const x = new Date(d);
  x.setDate(x.getDate() + n);
  return x;
}

// Сдвиг дат шаблона относительно 5-й зоны USDA (как usdaZoneToDayShift в Android)
export function usdaZoneToDayShift(zone) {
  return { 1: 40, 2: 30, 3: 20, 4: 10, 5: 0, 6: -10, 7: -20, 8: -25, 9: -30 }[zone] || 0;
}

// 'MM-DD' + сдвиг → 'YYYY-MM-DD' заданного года
export function shiftMMDD(mmdd, shift, year) {
  const [m, d] = mmdd.split('-').map(Number);
  return toISODate(addDays(new Date(year, m - 1, d), shift));
}

// Год для плана: если большая часть дат в текущем году уже прошла — следующий год
export function determinePlanYear(datesMmDd, shift = 0) {
  const today = new Date();
  const y = today.getFullYear();
  today.setHours(0, 0, 0, 0);
  const shifted = datesMmDd.map((s) => {
    const [m, d] = s.split('-').map(Number);
    return addDays(new Date(y, m - 1, d), shift);
  });
  const future = shifted.filter((x) => x >= today).length;
  return future >= Math.ceil(shifted.length / 2) ? y : y + 1;
}
