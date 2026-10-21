/** Authored 24px stroke icon vocabulary, independent of platform fonts. */
const paths = {
  "⌁": "M3 12h4l3-6 4 12 3-6h4",
  "↗": "M6 18 18 6M6 6h12v12",
  "△": "M12 3 2 21h20L12 3ZM12 9v5M12 17v1",
  "▣": "M4 4h16v16H4ZM8 8h8v8H8Z",
  "⌕": "M21 21l-6-6M16 9a7 7 0 1 1-14 0 7 7 0 0 1 14 0",
  "→": "M4 12h16M14 6l6 6-6 6",
  "✓": "m4 12 5 5L20 6",
  "?": "M9 8a3 3 0 1 1 4 3v3M12 18h.01",
  "▶": "m7 4 13 8-13 8V4Z",
  "↓": "M12 3v18M5 14l7 7 7-7",
  "×": "m6 6 12 12M18 6 6 18",
  "↻": "M20 7v5h-5M20 12a8 8 0 1 0-2 6",
  "◉": "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12ZM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0",
  i: "M12 11v7M12 6h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
  "!": "M12 6v8M12 18h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
  "•••": "M4 12h.01M12 12h.01M20 12h.01",
  "...": "M4 12h.01M12 12h.01M20 12h.01",
  "…": "M4 12h.01M12 12h.01M20 12h.01",
  "●": "M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
};
export function installIcons() {
  /** @param {Node} root */
  function decorate(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    /** @type {Text[]} */
    const nodes = [];
    if (root.nodeType === Node.TEXT_NODE)
      nodes.push(/** @type {Text} */ (root));
    while (walker.nextNode())
      nodes.push(/** @type {Text} */ (walker.currentNode));
    for (const node of nodes) {
      if (
        !node.parentElement?.closest(
          "button,.nav-icon,.tracker-symbol,.tracker-icon,.market-icon,.search-icon,.stock-search-icon,.top-search,.notification-arrow,.notification-marker,.notice-icon,.auth-note,.toast-icon,.hero-icon",
        ) ||
        node.parentElement.closest("svg,code,pre,textarea")
      )
        continue;
      const text = node.textContent || "";
      const standalone = /^(?:[?!i●]|•••|\.\.\.|…)$/.test(text.trim());
      if (!/[⌁↗△▣⌕→✓▶↓×↻◉]/.test(text) && !standalone) continue;
      const fragment = document.createDocumentFragment();
      for (const part of standalone
        ? [text.trim()]
        : text.split(/([⌁↗△▣⌕→✓▶↓×↻◉])/)) {
        if (!(part in paths)) {
          fragment.append(document.createTextNode(part));
          continue;
        }
        const svg = document.createElementNS(
          "http://www.w3.org/2000/svg",
          "svg",
        );
        svg.setAttribute("viewBox", "0 0 24 24");
        svg.setAttribute("fill", "none");
        svg.setAttribute("stroke", "currentColor");
        svg.setAttribute("stroke-width", "1.8");
        svg.setAttribute("stroke-linecap", "round");
        svg.setAttribute("stroke-linejoin", "round");
        svg.setAttribute("aria-hidden", "true");
        svg.setAttribute("focusable", "false");
        svg.classList.add("ui-icon");
        const path = document.createElementNS(svg.namespaceURI, "path");
        path.setAttribute("d", paths[/** @type {keyof typeof paths} */ (part)]);
        svg.append(path);
        fragment.append(svg);
      }
      node.replaceWith(fragment);
    }
  }
  decorate(document.body);
  new MutationObserver((records) =>
    records.forEach((record) =>
      record.addedNodes.forEach((node) => decorate(node)),
    ),
  ).observe(document.body, { childList: true, subtree: true });
}
