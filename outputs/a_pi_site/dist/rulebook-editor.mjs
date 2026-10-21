import { apiFetch } from "./api-client.mjs";
/** @typedef {{version:string,recipe:Record<string,unknown>}} SavedRecipe */
/** @param {string} zh @param {string} en */
const text = (zh, en) =>
  document.documentElement.lang.startsWith("zh") ? zh : en;

/** Resolve only the explicitly selected, saved version. Never infer edited rules.
 * @param {string} strategy */
export function selectedRulebookVersion(strategy) {
  if (strategy !== "rulebook") return "";
  const select = /** @type {HTMLSelectElement|null} */ (
    document.getElementById("rulebookVersion")
  );
  const input = /** @type {HTMLTextAreaElement|null} */ (
    document.getElementById("rulebookInput")
  );
  if (
    !select?.value ||
    !input?.dataset.saved ||
    JSON.stringify(JSON.parse(input.value)) !== input.dataset.saved
  )
    throw new Error(
      text(
        "请先保存并选择复杂规则版本",
        "Save and select the rule version first",
      ),
    );
  return select.value;
}

export function createRulebookEditor() {
  const host = document.getElementById("rulebookEditor");
  if (!host) return { refresh: async () => {} };
  const title = host.querySelector("summary") || document.createElement("h2");
  title.textContent = text("复杂日线规则编辑器", "Daily rule editor");
  const note = document.createElement("p");
  note.textContent = text(
    "最多 8 个价格/量能信号、30 只持仓、252 日历史。支持资格筛选、广度择时和收盘退出规则。保存后在实验配置选择复杂规则策略；所有版本共享研究预算，不改变纸面配置。",
    "Up to 8 price/volume signals, 30 holdings and 252 history sessions. Eligibility, breadth timing and close-based exits. Save first, then select the rulebook strategy in experiment settings. Versions share one research budget; paper configuration is unchanged.",
  );
  const label = document.createElement("label");
  label.htmlFor = "rulebookVersion";
  label.textContent = text("已保存版本", "Saved versions");
  const select = document.createElement("select");
  select.id = "rulebookVersion";
  const inputLabel = document.createElement("label");
  inputLabel.htmlFor = "rulebookInput";
  inputLabel.textContent = text("规则 JSON", "Rule JSON");
  const input = document.createElement("textarea");
  input.id = "rulebookInput";
  input.rows = 15;
  input.spellcheck = false;
  const actions = document.createElement("div");
  actions.className = "rulebook-actions";
  const save = document.createElement("button");
  save.type = "button";
  save.className = "secondary-button";
  save.textContent = text("保存并选用版本", "Save and select version");
  const reload = document.createElement("button");
  reload.type = "button";
  reload.className = "secondary-button";
  reload.textContent = text("重新读取", "Reload");
  actions.append(save, reload);
  const feedback = document.createElement("p");
  feedback.setAttribute("role", "status");
  if (!title.parentElement) host.append(title);
  host.append(note, label, select, inputLabel, input, actions, feedback);

  const syncRulebookVisibility = () => {
    const strategy = /** @type {HTMLSelectElement|null} */ (
      document.getElementById("strategySelect")
    );
    const show = (strategy?.value || "") === "rulebook";
    host.hidden = !show;
    if (!show) host.open = false;
  };
  document
    .getElementById("strategySelect")
    ?.addEventListener("change", syncRulebookVisibility);
  syncRulebookVisibility();

  new MutationObserver(() => {
    title.textContent = text("复杂日线规则编辑器", "Daily rule editor");
    label.textContent = text("已保存版本", "Saved versions");
    inputLabel.textContent = text("规则 JSON", "Rule JSON");
    save.textContent = text("保存并选用版本", "Save and select version");
    reload.textContent = text("重新读取", "Reload");
  }).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["lang"],
  });
  /** @type {SavedRecipe[]} */ let recipes = [];
  let baseline = "";
  let previousVersion = "";
  function dirty() {
    return input.value !== baseline;
  }
  function useSelected() {
    const row = recipes.find((row) => row.version === select.value);
    if (!row) return;
    input.value = JSON.stringify(row.recipe, null, 2);
    input.dataset.saved = JSON.stringify(row.recipe);
    baseline = input.value;
    previousVersion = select.value;
    feedback.textContent = `${text("已选择版本", "Selected version")} ${row.version.slice(0, 12)}`;
  }
  /** @param {string} [selected] */
  async function load(selected = "") {
    reload.disabled = save.disabled = true;
    input.readOnly = select.disabled = true;
    try {
      const response = await apiFetch("/api/strategy-recipes", {
        cache: "no-store",
      });
      const data = await response.json();
      if (!response.ok || !data.ok || !Array.isArray(data.recipes))
        throw new Error(data.error || `HTTP ${response.status}`);
      recipes = data.recipes;
      select.replaceChildren();
      const empty = document.createElement("option");
      empty.value = "";
      empty.textContent = text("先保存模板", "Save a template first");
      select.append(empty);
      for (const row of recipes) {
        const option = document.createElement("option");
        option.value = row.version;
        option.textContent = `${String(row.recipe.name)} · ${row.version.slice(0, 12)}`;
        select.append(option);
      }
      select.value = selected || recipes[0]?.version || "";
      if (select.value) useSelected();
      else {
        input.value = JSON.stringify(data.template, null, 2);
        baseline = input.value;
        previousVersion = "";
        delete input.dataset.saved;
        feedback.textContent = text("模板尚未保存", "Template not saved");
      }
    } catch (error) {
      feedback.textContent = `${text("读取失败", "Load failed")}: ${error instanceof Error ? error.message : String(error)}`;
    } finally {
      reload.disabled = save.disabled = false;
      input.readOnly = select.disabled = false;
    }
  }
  save.addEventListener("click", async () => {
    save.disabled = reload.disabled = true;
    input.readOnly = select.disabled = true;
    try {
      const recipe = JSON.parse(input.value);
      const response = await apiFetch("/api/strategy-recipes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ recipe }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok)
        throw new Error(data.error || `HTTP ${response.status}`);
      await load(data.version);
    } catch (error) {
      feedback.textContent = `${text("保存失败，输入已保留", "Save failed; input retained")}: ${error instanceof Error ? error.message : String(error)}`;
    } finally {
      save.disabled = reload.disabled = false;
      input.readOnly = select.disabled = false;
    }
  });
  reload.addEventListener("click", () => {
    if (dirty()) {
      feedback.textContent = text(
        "存在未保存修改，请先保存",
        "Unsaved changes; save before reloading",
      );
      return;
    }
    load(select.value);
  });
  select.addEventListener("change", () => {
    if (dirty()) {
      select.value = previousVersion;
      feedback.textContent = text(
        "请先保存修改，再切换版本",
        "Save changes before switching versions",
      );
      return;
    }
    if (!select.value) {
      select.value = previousVersion;
      return;
    }
    useSelected();
  });
  return { refresh: () => (dirty() ? Promise.resolve() : load()) };
}
