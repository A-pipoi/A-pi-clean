export const APiLocaleRuntime = (() => {
  const supportedLocales = new Set(["zh-CN", "en-US", "fr-FR", "ja-JP"]);

  /** @param {Record<string, Record<string, string>>} dictionary @param {string} storageKey */
  function createLocaleRuntime(dictionary, storageKey) {
    const originalText = new WeakMap();
    const originalAttributes = new WeakMap();
    let locale = "zh-CN";
    try {
      const stored = localStorage.getItem(storageKey);
      if (stored && supportedLocales.has(stored)) locale = stored;
    } catch {
      /* local storage is optional */
    }

    const reverseSources = new Map();
    const sourcePhrases = new Set();
    for (const phrases of Object.values(dictionary || {})) {
      for (const source of Object.keys(phrases || {})) {
        if (/[\u3400-\u9fff]/.test(source)) sourcePhrases.add(source);
      }
    }
    for (const [language, phrases] of Object.entries(dictionary || {})) {
      if (language === "zh-CN") continue;
      for (const [source, translated] of Object.entries(phrases || {})) {
        if (
          !/[\u3400-\u9fff]/.test(source) ||
          !translated ||
          translated === source ||
          sourcePhrases.has(translated) ||
          /^[\u3400-\u9fff]{1,4}$/.test(translated)
        )
          continue;
        if (reverseSources.has(translated)) {
          if (reverseSources.get(translated) !== source)
            reverseSources.set(translated, null);
        } else {
          reverseSources.set(translated, source);
        }
      }
    }

    /** @param {unknown} value */
    function canonicalize(value) {
      const text = String(value || "");
      return reverseSources.get(text) || text;
    }

    /** @type {import('../types/services').SharedServices['translate']} */
    function translate(value) {
      const base = canonicalize(value);
      const phrases = dictionary?.[locale] || {};
      if (Object.prototype.hasOwnProperty.call(phrases, base))
        return phrases[base];
      const entries = Object.entries(phrases)
        .filter(
          ([source, translated]) =>
            source && source !== translated && base.includes(source),
        )
        .sort((left, right) => right[0].length - left[0].length);
      let result = base;
      let untranslated = base;
      const replacements = [];
      for (const [source, translated] of entries) {
        if (!untranslated.includes(source)) continue;
        /** @type {string} */
        const marker = `\u0000${replacements.length}\u0000`;
        result = result.split(source).join(marker);
        untranslated = untranslated.split(source).join("");
        replacements.push([marker, translated]);
      }
      if (/[\u3400-\u9fff]/.test(untranslated)) return base;
      return replacements.reduce(
        (text, [marker, translated]) => text.split(marker).join(translated),
        result,
      );
    }

    /** @param {Node} node */
    function localizeTextNode(node) {
      const parent = node.parentElement;
      if (
        !parent ||
        ["SCRIPT", "STYLE", "TEXTAREA", "CODE", "PRE"].includes(
          parent.tagName,
        ) ||
        parent.closest("[data-locale-skip]")
      )
        return;
      const current = String(node.nodeValue || "");
      if (!current.trim()) return;
      let source = originalText.get(node);
      if (source === undefined) {
        source = canonicalize(current.trim());
        originalText.set(node, source);
      }
      const translated = translate(source);
      const leading = current.match(/^\s*/)?.[0] || "";
      const trailing = current.match(/\s*$/)?.[0] || "";
      const next = `${leading}${translated}${trailing}`;
      if (next !== current) node.nodeValue = next;
    }

    /** @param {Element} element */
    function localizeAttributes(element) {
      if (
        !(element instanceof Element) ||
        element.closest("[data-locale-skip]")
      )
        return;
      for (const attribute of ["aria-label", "placeholder", "title"]) {
        if (!element.hasAttribute(attribute)) continue;
        let originals = originalAttributes.get(element);
        if (!originals) {
          originals = new Map();
          originalAttributes.set(element, originals);
        }
        const current = element.getAttribute(attribute) || "";
        if (!originals.has(attribute))
          originals.set(attribute, canonicalize(current));
        const translated = translate(originals.get(attribute));
        if (translated !== current) element.setAttribute(attribute, translated);
      }
    }

    /** @param {Node} root */
    function localizeSubtree(root) {
      if (!root) return;
      if (root.nodeType === Node.TEXT_NODE) {
        localizeTextNode(root);
        return;
      }
      if (root instanceof Element) localizeAttributes(root);
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) localizeTextNode(node);
      if (root instanceof Element)
        root
          .querySelectorAll("[aria-label],[placeholder],[title]")
          .forEach(localizeAttributes);
    }

    function apply(nextLocale = locale) {
      locale = supportedLocales.has(nextLocale) ? nextLocale : "zh-CN";
      try {
        localStorage.setItem(storageKey, locale);
      } catch {
        /* local storage is optional */
      }
      document.documentElement.lang = locale;
      localizeSubtree(document.body);
      const title = translate("A-pi · A股量化工作台");
      if (document.title !== title) document.title = title;
      const select = /** @type {HTMLSelectElement | null} */ (
        document.getElementById("languageSelect")
      );
      if (select && select.value !== locale) select.value = locale;
      const search = /** @type {HTMLInputElement | null} */ (
        document.getElementById("globalSearchInput")
      );
      if (search) {
        const placeholder = translate("搜索股票、策略或页面");
        if (search.placeholder !== placeholder)
          search.placeholder = placeholder;
      }
      return locale;
    }

    const observer = new MutationObserver((records) => {
      for (const record of records) {
        if (record.type === "attributes" && record.target instanceof Element)
          localizeAttributes(record.target);
        for (const node of record.addedNodes || []) localizeSubtree(node);
        if (record.type === "characterData") localizeTextNode(record.target);
      }
    });
    observer.observe(document.body, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
      attributeFilter: ["aria-label", "placeholder", "title"],
    });

    return { apply, canonicalize, locale: () => locale, translate };
  }

  return { create: createLocaleRuntime };
})();
