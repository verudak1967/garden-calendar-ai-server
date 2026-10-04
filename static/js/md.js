// Мини-рендер Markdown для ответов AI (## заголовки, **жирный**, списки).
// Ответы сервера используют ограниченный набор разметки, поэтому парсер нарочито прост.

import { esc } from './ui.js';

function inline(s) {
  return s
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

export function renderMarkdown(text) {
  const lines = String(text || '').split(/\r?\n/);
  const out = [];
  let listType = null; // 'ul' | 'ol'

  const closeList = () => {
    if (listType) { out.push(`</${listType}>`); listType = null; }
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (!line.trim()) { closeList(); continue; }

    const h = line.match(/^(#{2,4})\s+(.*)$/);
    if (h) {
      closeList();
      const lvl = Math.min(h[1].length, 3);
      out.push(`<h${lvl + 1}>${inline(esc(h[2]))}</h${lvl + 1}>`); // ## → h3 и т.д., но первый уровень смотрится крупнее
      continue;
    }

    const ul = line.match(/^[-•]\s+(.*)$/);
    if (ul) {
      if (listType !== 'ul') { closeList(); out.push('<ul>'); listType = 'ul'; }
      out.push(`<li>${inline(esc(ul[1]))}</li>`);
      continue;
    }

    const ol = line.match(/^\d+[.)]\s+(.*)$/);
    if (ol) {
      if (listType !== 'ol') { closeList(); out.push('<ol>'); listType = 'ol'; }
      out.push(`<li>${inline(esc(ol[1]))}</li>`);
      continue;
    }

    closeList();
    out.push(`<p>${inline(esc(line))}</p>`);
  }
  closeList();
  return out.join('\n');
}
