// ── Content script: DOM extraction + step execution ──

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === 'extractElements') {
    sendResponse(extractElements());
  } else if (msg.action === 'execute') {
    executeStep(msg.step).then(() => sendResponse({ ok: true })).catch(e => sendResponse({ error: e.message }));
    return true; // async
  }
});

function extractElements() {
  const results = [];
  let id = 1;
  const seen = new Set();

  function walk(node, depth) {
    if (!node || depth > 20) return;
    if (node.nodeType === 1) {
      const tag = (node.tagName || '').toLowerCase();
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      const visible = rect.width > 0 && rect.height > 0 &&
        style.display !== 'none' && style.visibility !== 'hidden' &&
        parseFloat(style.opacity) > 0 && rect.bottom > 0;

      if (visible && (
        tag === 'button' || tag === 'a' || tag === 'input' ||
        tag === 'select' || tag === 'textarea' ||
        node.getAttribute('role') === 'button' ||
        node.getAttribute('role') === 'link' ||
        node.getAttribute('role') === 'textbox' ||
        node.getAttribute('role') === 'combobox' ||
        node.getAttribute('role') === 'option' ||
        node.getAttribute('contenteditable') === 'true'
      )) {
        const aria = node.getAttribute('aria-label') || '';
        const title = node.getAttribute('title') || '';
        const placeholder = node.getAttribute('placeholder') || '';
        let text = (node.textContent || '').trim().slice(0, 60);
        if (tag === 'input' || tag === 'textarea') {
          text = (node.getAttribute('type') || 'text') + ' input' + (placeholder ? ' "' + placeholder + '"' : '');
        }
        if (tag === 'a') {
          const href = node.getAttribute('href') || '';
          if (href) text = text || href;
        }
        const label = aria || title || placeholder || text;
        if (label && !seen.has(label + tag)) {
          seen.add(label + tag);
          results.push({ id: id++, tag, text: label.slice(0, 100) });
        }
      }
    }
    for (const child of node.childNodes) { walk(child, depth + 1); }
  }
  walk(document.body, 0);
  return results;
}

async function executeStep(step) {
  const id = step.id;
  const action = (step.action || 'click').toLowerCase();
  const value = step.value || '';

  // Find element by our ID (we stored them in order during extraction)
  const elements = extractElements();
  const el = elements.find(e => e.id === id);
  if (!el) throw new Error(`Element ${id} not found`);

  // Find the actual DOM element matching the text/tag
  const selector = `${el.tag}`;
  const matches = document.querySelectorAll(selector);
  let target = null;
  for (const m of matches) {
    if ((m.textContent || '').trim().slice(0, 60).includes(el.text.slice(0, 30)) ||
        (m.getAttribute('aria-label') || '').includes(el.text.slice(0, 30)) ||
        (m.getAttribute('placeholder') || '').includes(el.text.slice(0, 30))) {
      target = m;
      break;
    }
  }
  if (!target) {
    // Fallback: use the first matching element by ID order
    const all = document.querySelectorAll(selector);
    const idx = id - 1;
    if (idx >= 0 && idx < all.length) target = all[idx];
  }
  if (!target) throw new Error(`Cannot find element for step ${id}`);

  if (action === 'click') {
    target.click();
  } else if (action === 'type') {
    target.focus();
    target.value = value;
    target.dispatchEvent(new Event('input', { bubbles: true }));
  } else if (action === 'select') {
    target.value = value;
    target.dispatchEvent(new Event('change', { bubbles: true }));
  }

  await new Promise(r => setTimeout(r, 1500));
}
