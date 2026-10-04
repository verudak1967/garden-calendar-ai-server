// Клиент AI-сервера «AI Ботаник» (FastAPI на Render).
// Все запросы включают device_id и часовой пояс — как это делает Android-клиент.

const API_BASE = 'https://garden-calendar-ai-server.onrender.com';
const TIMEOUT_MS = 90_000; // Render на бесплатном тарифе может просыпаться до 60 сек

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `Ошибка сервера (${status})`);
    this.status = status;
  }
}

export function deviceId() {
  let id = localStorage.getItem('device_id');
  if (!id) {
    id = 'web-' + (crypto.randomUUID ? crypto.randomUUID() : Date.now() + '-' + Math.random().toString(36).slice(2));
    localStorage.setItem('device_id', id);
  }
  return id;
}

export function tzOffsetMinutes() {
  return -new Date().getTimezoneOffset();
}

async function request(path, { method = 'GET', body } = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  let resp;
  try {
    resp = await fetch(API_BASE + path, {
      method,
      headers: body ? { 'Content-Type': 'application/json; charset=utf-8' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    });
  } catch (e) {
    if (e.name === 'AbortError') {
      throw new ApiError(0, 'Сервер не ответил за 90 секунд. Попробуйте ещё раз — при «пробуждении» это бывает.');
    }
    throw new ApiError(0, 'Нет связи с сервером. Проверьте интернет.');
  } finally {
    clearTimeout(timer);
  }

  let data = {};
  try { data = await resp.json(); } catch { /* пустое тело */ }

  if (!resp.ok) {
    throw new ApiError(resp.status, data.detail || `Ошибка сервера (${resp.status})`);
  }
  return data;
}

// Текстовый AI-запрос: request_type = free | care | pests | diseases
export async function ask({ query, request_type = 'free', culture_name = '', variety = '', region_zone = 0, context = '' }) {
  return request('/api/ask', {
    method: 'POST',
    body: {
      query,
      context,
      device_id: deviceId(),
      request_type,
      timezone_offset_minutes: tzOffsetMinutes(),
      culture_name,
      variety,
      region_zone: Number(region_zone) || 0,
    },
  });
}

// Анализ фото растения. image_base64 — чистый base64 JPEG без префекса data:
export async function askPhoto({ image_base64, context = '' }) {
  return request('/api/ask-photo', {
    method: 'POST',
    body: {
      image_base64,
      context,
      device_id: deviceId(),
      timezone_offset_minutes: tzOffsetMinutes(),
    },
  });
}

// Генерация годового плана задач. phase: P0..P9
export async function generatePlan({ culture_name, variety = '', region_zone = 5, phase = 'P2' }) {
  return request('/api/generate-plan', {
    method: 'POST',
    body: {
      culture_name,
      variety,
      region_zone: Number(region_zone) || 5,
      phase,
      device_id: deviceId(),
      timezone_offset_minutes: tzOffsetMinutes(),
    },
  });
}

// Текущий расход дневного лимита
export async function getUsage() {
  return request(`/api/usage?device_id=${encodeURIComponent(deviceId())}&timezone_offset_minutes=${tzOffsetMinutes()}`);
}
