import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { createAppShell } from "../dist/app-shell.mjs";

test("closing a risk-alert detail marks only that alert as handled", () => {
  const dom = new JSDOM(
    '<dialog id="utilityDialog"><h2 id="utilityDialogTitle"></h2><div id="utilityDialogBody"></div></dialog>',
  );
  const originalDocument = global.document;
  const originalWindow = global.window;
  global.document = dom.window.document;
  global.window = dom.window;
  const dismissed = [];
  const appShell = createAppShell({
    latestRiskAlertDetails: [
      { id: "alert-1", title: "数据过期", description: "质量提醒", rows: [] },
    ],
    notificationState: { health: null, paper: null, workspace: null },
    escapeHtml: (value) => String(value ?? ""),
    localeText: (value) => String(value ?? ""),
    setText: (id, value) => {
      const element = global.document.getElementById(id);
      if (element) element.textContent = String(value ?? "");
    },
    applyLocale() {},
    dismissRiskAlert: (id) => dismissed.push(id),
  });

  try {
    appShell.openRiskAlertDetails(0);
    appShell.handleUtilityDialogClosed();
    assert.deepEqual(dismissed, ["alert-1"]);

    appShell.openUtilityDialog("其他详情", "<p>普通弹窗</p>");
    appShell.handleUtilityDialogClosed();
    assert.deepEqual(dismissed, ["alert-1"], "closing unrelated dialogs must not dismiss alerts");
  } finally {
    global.document = originalDocument;
    if (originalWindow === undefined) delete global.window;
    else global.window = originalWindow;
    dom.window.close();
  }
});
