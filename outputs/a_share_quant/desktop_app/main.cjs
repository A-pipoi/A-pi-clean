const { app, BrowserWindow, dialog, shell, Menu } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const { createServiceManager } = require("./modules/service.cjs");
const { createDesktopWindows } = require("./modules/windows.cjs");
const { matchesLocalCertificate } = require("./modules/local-certificate.cjs");
const service = createServiceManager(app, __dirname);
const windows = createDesktopWindows({
  app,
  BrowserWindow,
  shell,
  Menu,
  desktopRoot: __dirname,
});
app.setAppUserModelId("com.api.quantworkbench");

let trustedLocalFingerprint = "";
app.on(
  "certificate-error",
  (event, _webContents, url, _error, certificate, callback) => {
    if (
      matchesLocalCertificate(url, certificate.data, trustedLocalFingerprint)
    ) {
      event.preventDefault();
      callback(true);
      return;
    }
    callback(false);
  },
);

app.whenReady().then(async () => {
  let startupStage = "初始化客户端";
  try {
    await windows.createWindow();
    startupStage = "查找工作台项目目录";
    const projectRoot = service.findProjectRoot();
    startupStage = "等待本地 Python 服务";
    await service.ensureLocalService(projectRoot);
    trustedLocalFingerprint = service.localCertificateFingerprint(projectRoot);
    const currentWindow = windows.current();
    if (currentWindow && !currentWindow.isDestroyed()) {
      startupStage = "打开工作台页面";
      await windows.openWorkbench();
    }
  } catch (caught) {
    const error = caught instanceof Error ? caught : new Error(String(caught));
    const detail = `${startupStage}\n${error.stack || error.message || String(error)}`;
    try {
      fs.writeFileSync(
        path.join(app.getPath("temp"), "a-pi-desktop-startup-error.log"),
        detail,
        "utf8",
      );
    } catch {
      // Keep the original startup error visible if diagnostics cannot be written.
    }
    dialog.showErrorBox(
      "A-pi 量化工作台启动失败",
      `${startupStage}\n${error.message || String(error)}`,
    );
    app.quit();
  }
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    void windows.createWindow().catch((error) => {
      dialog.showErrorBox(
        "A-pi 量化工作台启动失败",
        error.message || String(error),
      );
    });
  }
});

app.on("window-all-closed", () => app.quit());
