function escapeHtml(value) {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}

function transform(node) {
  if (!node || typeof node !== 'object') return;

  if (node.type === 'code' && node.lang === 'mermaid') {
    node.type = 'html';
    node.value = `<pre class="mermaid" aria-label="流程图">${escapeHtml(node.value)}</pre>`;
    delete node.lang;
    delete node.meta;
    return;
  }

  if (Array.isArray(node.children)) {
    for (const child of node.children) transform(child);
  }
}

export default function remarkMermaid() {
  return transform;
}
