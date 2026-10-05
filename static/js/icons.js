// Линейный набор иконок приложения (24px, stroke 2, currentColor).
// Единый стиль вместо эмодзи — единый «голос» интерфейса.

const PATHS = {
  sprout: '<path d="M12 21v-8"/><path d="M12 13C12 8.5 9 6 4.5 6c0 4.5 3 7 7.5 7z"/><path d="M12 11c0-3.6 2.6-5.5 7.5-5.5 0 3.9-2.6 5.5-7.5 5.5z"/>',
  calendar: '<rect x="4" y="5.5" width="16" height="15" rx="3"/><path d="M8 3v4.5M16 3v4.5M4 10.5h16"/><path d="M8 14.5h2M14 14.5h2M8 17.5h2"/>',
  book: '<path d="M5 4.5A2.5 2.5 0 0 1 7.5 2H19v17.5H7.5A2.5 2.5 0 0 0 5 22z"/><path d="M5 19.5A2.5 2.5 0 0 1 7.5 17H19"/><path d="M9.5 7h6"/>',
  camera: '<path d="M4.5 8.5h3l1.8-2.8h5.4l1.8 2.8h3a1.2 1.2 0 0 1 1.2 1.2v9a1.2 1.2 0 0 1-1.2 1.2h-15a1.2 1.2 0 0 1-1.2-1.2v-9a1.2 1.2 0 0 1 1.2-1.2z"/><circle cx="12" cy="14" r="3.6"/>',
  sliders: '<path d="M5 7.5h8.5M18.5 7.5H19M5 16.5h.5M10.5 16.5H19"/><circle cx="15.5" cy="7.5" r="2.2"/><circle cx="7.5" cy="16.5" r="2.2"/>',
  list: '<path d="M10 6.5h9.5M10 12h9.5M10 17.5h9.5"/><path d="M4 6.2l1.2 1.2 2.3-2.4M4 11.7l1.2 1.2 2.3-2.4M4 17.2l1.2 1.2 2.3-2.4"/>',
  chat: '<path d="M20.5 11.5c0 4.1-3.8 7.3-8.5 7.3-1 0-2-.15-2.9-.42L4.5 20l1.2-3.4C4.1 15.2 3.5 13.4 3.5 11.5c0-4.1 3.8-7.3 8.5-7.3s8.5 3.2 8.5 7.3z"/>',
  plus: '<path d="M12 5.5v13M5.5 12h13"/>',
};

export function icon(name, size = 24) {
  const p = PATHS[name];
  if (!p) return '';
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" ` +
    `stroke="currentColor" stroke-width="1.8" stroke-linecap="round" ` +
    `stroke-linejoin="round" aria-hidden="true">${p}</svg>`;
}
