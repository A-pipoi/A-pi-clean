const fs = require("node:fs");
const path = require("node:path");
const { APP_URL, APP_URL_LOCALHOST } = require("./service.cjs");
/** @param {string} url */
function freshWorkbenchUrl(url) {
  const freshUrl = new URL(url);
  freshUrl.searchParams.set("_desktop_launch", `${Date.now()}-${process.pid}`);
  return freshUrl.toString();
}

/** @param {{app:import('electron').App,BrowserWindow:typeof import('electron').BrowserWindow,shell:typeof import('electron').shell,Menu:typeof import('electron').Menu,desktopRoot:string}} dependencies */
function createDesktopWindows({
  app,
  BrowserWindow,
  shell,
  Menu,
  desktopRoot,
}) {
  /** @type {import('electron').BrowserWindow | null} */
  let mainWindow = null;
  /** @type {import('electron').BrowserWindow | null} */
  let startupWindow = null;
  let revealingWorkbench = false;
  /** @param {import('electron').WebContents} contents @param {string} targetUrl */
  function openExternalWhenRequested(contents, targetUrl) {
    contents.setWindowOpenHandler(({ url }) => {
      if (url.startsWith("https://") || url.startsWith("http://"))
        shell.openExternal(url);
      return { action: "deny" };
    });
    contents.on("will-navigate", (event, url) => {
      try {
        if (new URL(url).origin === new URL(targetUrl).origin) return;
      } catch {
        // Only explicitly trusted startup and local app URLs may stay in the window.
      }
      event.preventDefault();
      if (url.startsWith("https://") || url.startsWith("http://"))
        shell.openExternal(url);
    });
  }

  async function createWindow() {
    const options = {
      width: 1440,
      height: 960,
      minWidth: 1024,
      minHeight: 680,
      show: false,
      backgroundColor: "#f3f5f9",
      title: "A-pi 量化工作台",
      autoHideMenuBar: true,
      webPreferences: {
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        webSecurity: true,
      },
    };
    mainWindow = new BrowserWindow(options);
    // Keep the spinner in its own renderer while the workbench parses and paints.
    startupWindow = new BrowserWindow({ ...options, show: true });
    revealingWorkbench = false;

    Menu.setApplicationMenu(null);
    mainWindow.on("closed", () => {
      mainWindow = null;
      startupWindow?.close();
    });
    startupWindow.on("closed", () => {
      startupWindow = null;
      if (!revealingWorkbench) mainWindow?.close();
    });
    await startupWindow.loadFile(path.join(desktopRoot, "assets", "startup.html"));
  }

  async function openWorkbench() {
    const failures = [];
    for (const url of [APP_URL, APP_URL_LOCALHOST]) {
      if (!mainWindow || mainWindow.isDestroyed()) return;
      /** @type {ReturnType<typeof setTimeout> | undefined} */
      let timeout;
      const window = mainWindow;
      let painted = false;
      /** @type {()=>void} */
      let onFirstPaint = () => {};
      const firstPaint = new Promise((resolve) => {
        onFirstPaint = () => { painted = true; resolve(undefined); };
        window.once("ready-to-show", onFirstPaint);
      });
      try {
        await Promise.race([
          (async () => {
            await window.loadURL(freshWorkbenchUrl(url));
            if (!painted) await firstPaint;
          })(),
          new Promise((_, reject) => {
            timeout = setTimeout(
              () => reject(new Error("页面加载超时（20 秒）")),
              20_000,
            );
          }),
        ]);
        if (window.isDestroyed()) return;
        openExternalWhenRequested(window.webContents, url);
        if (startupWindow && !startupWindow.isDestroyed())
          window.setBounds(startupWindow.getBounds());
        revealingWorkbench = true;
        window.show();
        startupWindow?.close();
        return;
      } catch (caught) {
        if (window.isDestroyed()) return;
        const error =
          caught instanceof Error ? caught : new Error(String(caught));
        failures.push(`${url}: ${error.message || String(error)}`);
        window.webContents.stop();
      } finally {
        clearTimeout(timeout);
        window.removeListener("ready-to-show", onFirstPaint);
      }
    }

    const detail = `Electron 内嵌页面加载失败：\n${failures.join("\n")}`;
    try {
      const logPath = path.join(
        app.getPath("temp"),
        "a-pi-desktop-startup-error.log",
      );
      fs.writeFileSync(logPath, detail, "utf8");
      await shell.openExternal(APP_URL);
      app.quit();
    } catch (caught) {
      const error =
        caught instanceof Error ? caught : new Error(String(caught));
      throw new Error(
        `${detail}\n无法打开系统浏览器：${error.message || String(error)}`,
      );
    }
  }

  return { createWindow, openWorkbench, current: () => mainWindow };
}
module.exports = { createDesktopWindows };
