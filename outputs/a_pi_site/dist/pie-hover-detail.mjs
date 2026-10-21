/** Non-overlapping pie tooltip: a reserved sibling surface, never a canvas overlay.
 * @param {HTMLElement} anchor
 * @param {Pick<import('echarts/core').ECharts,'on'|'off'>} chart
 * @param {{name:string,value:number}[]} segments
 */
export function installPieHoverDetail(anchor, chart, segments) {
  const id = anchor.id ? anchor.id + "-hover-detail" : "";
  if (id) document.getElementById(id)?.remove();
  const rows = segments.filter(
    (row) => Number.isFinite(row.value) && row.value > 0,
  );
  const total = rows.reduce((sum, row) => sum + row.value, 0);
  const box = document.createElement("div");
  box.className = "pie-hover-detail";
  box.dataset.floating = "true";
  if (id) box.id = id;
  box.tabIndex = 0;
  box.setAttribute("aria-label", "当前暴露详情；方向键切换，Esc 清除");
  const name = document.createElement("strong");
  const amount = document.createElement("span");
  const share = document.createElement("span");
  box.append(name, amount, share);
  anchor.after(box);
  const panel = anchor.closest(".allocation-panel");
  let current = -1;
  let pinned = false;
  const words = () => {
    const lang = document.documentElement.lang.slice(0, 2);
    return (
      {
        zh: ["悬停或点击圆环查看详情", "方向键切换，Esc 清除", "金额", "占比"],
        en: [
          "Hover or click a segment",
          "Arrow keys to browse; Esc to clear",
          "Amount",
          "Share",
        ],
        ja: [
          "円グラフにカーソルを合わせるかクリック",
          "矢印キーで選択、Escで解除",
          "金額",
          "割合",
        ],
        fr: [
          "Survolez ou cliquez un segment",
          "Flèches pour parcourir ; Échap pour effacer",
          "Montant",
          "Part",
        ],
      }[lang] || [
        "Hover or click a segment",
        "Arrow keys to browse; Esc to clear",
        "Amount",
        "Share",
      ]
    );
  };
  const reset = () => {
    const labels = words();
    name.textContent = labels[0];
    amount.textContent = labels[1];
    share.textContent = "";
    box.dataset.active = "false";
    panel?.removeAttribute("data-pie-active");
  };
  /** @param {number} index */
  const show = (index) => {
    const row = rows[index];
    if (!row) return;
    current = index;
    const locale = document.documentElement.lang || "zh-CN";
    const labels = words();
    name.textContent = row.name;
    amount.textContent =
      labels[2] +
      " " +
      new Intl.NumberFormat(locale, {
        style: "currency",
        currency: "CNY",
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(row.value);
    share.textContent =
      labels[3] +
      " " +
      new Intl.NumberFormat(locale, {
        style: "percent",
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
      }).format(row.value / total);
    box.dataset.active = "true";
    panel?.setAttribute("data-pie-active", "true");
  };
  /** @param {{dataIndex?:number}} event */
  const select = (event) => {
    if (Number.isInteger(event.dataIndex))
      show(/** @type {number} */ (event.dataIndex));
  };
  for (const event of ["mouseover", "click", "globalout"]) chart.off(event);
  chart.on("mouseover", select);
  chart.on("click", (event) => {
    pinned = true;
    select(event);
  });
  chart.on("globalout", () => {
    if (!pinned) reset();
  });
  box.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      pinned = false;
      current = -1;
      reset();
    } else if (
      rows.length &&
      ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)
    ) {
      event.preventDefault();
      pinned = true;
      show(
        (current +
          (["ArrowLeft", "ArrowUp"].includes(event.key) ? -1 : 1) +
          rows.length) %
          rows.length,
      );
    }
  });
  reset();
  return box;
}
