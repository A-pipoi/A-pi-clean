import createDOMPurify from "dompurify";
/** @type {WeakMap<object, import('dompurify').DOMPurify>} */
const sanitizers = new WeakMap();
/** @param {Element} target @param {string} markup */
function cleanFragment(target, markup) {
  const view = target.ownerDocument.defaultView;
  if (!view)
    throw new Error("HTML rendering requires an owned browser document");
  let sanitizer = sanitizers.get(view);
  if (!sanitizer) {
    sanitizer = createDOMPurify(view);
    sanitizers.set(view, sanitizer);
  }
  // HTML table/option fragments require their parsing context. Sanitization is
  // complete BEFORE anything is attached; never reparse the sanitized output.
  const tag = target.tagName.toLowerCase();
  const contexts = /** @type {Record<string, [string,string,string]>} */ ({
    tbody: ["<table><tbody>", "</tbody></table>", "tbody"],
    thead: ["<table><thead>", "</thead></table>", "thead"],
    tfoot: ["<table><tfoot>", "</tfoot></table>", "tfoot"],
    tr: ["<table><tbody><tr>", "</tr></tbody></table>", "tr"],
    select: ["<select>", "</select>", "select"],
  });
  const context = contexts[tag];
  const fragment = sanitizer.sanitize(
    context ? context[0] + markup + context[1] : markup,
    {
      RETURN_DOM_FRAGMENT: true,
      USE_PROFILES: { html: true, svg: true },
      FORBID_TAGS: [
        "script",
        "style",
        "iframe",
        "object",
        "embed",
        "foreignObject",
        "base",
        "meta",
        "link",
      ],
      FORBID_ATTR: ["srcdoc"],
    },
  );
  if (!context) return fragment;
  const root = fragment.querySelector(context[2]);
  const output = target.ownerDocument.createDocumentFragment();
  if (root) output.append(...root.childNodes);
  return output;
}
/** Central, fail-closed HTML sink. Plain data should still use textContent.
 * @param {Element} target @param {string} markup */
export function setSafeHtml(target, markup) {
  if (!markup) {
    target.replaceChildren();
    return;
  }
  target.replaceChildren(cleanFragment(target, markup));
}
/** @param {Element} target @param {string} markup */
export function appendSafeHtml(target, markup) {
  target.append(cleanFragment(target, markup));
}
