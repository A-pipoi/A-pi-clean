/** @param {unknown} value @returns {Error} */
export function toError(value) {
  return value instanceof Error ? value : new Error(String(value));
}

/**
 * @template {keyof APiDomElementMap} K
 * @param {K} id
 * @returns {APiDomElementMap[K]}
 */
export function requiredElement(id) {
  const element = document.getElementById(id);
  if (!element)
    throw new Error(`Required application element is missing: ${id}`);
  return element;
}
