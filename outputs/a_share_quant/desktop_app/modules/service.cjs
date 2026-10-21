const { spawn } = require("node:child_process");
const { X509Certificate } = require("node:crypto");
const fs = require("node:fs");
const https = require("node:https");
const net = require("node:net");
const path = require("node:path");
const APP_URL = "https://127.0.0.1:8000/";
const APP_URL_LOCALHOST = "https://localhost:8000/";
const REQUIRED_API_VERSION = "governance-v5.5";
const REQUIRED_API_FEATURES = [
  "workspace-auto-retry-v1",
  "manual-paper-pit-seal-v1",
  "paper-pit-status-recovery-v1",
  "modular-runtime-v1",
  "typed-runtime-contracts-v1",
];
const START_TIMEOUT_MS = 75_000;

/** @param {string} projectRoot */
function serviceEnvironment(projectRoot) {
  const environment = { ...process.env };
  const envPath = path.join(projectRoot, ".env");
  if (!fs.existsSync(envPath)) return environment;
  for (const line of fs.readFileSync(envPath, "utf8").split(/\r?\n/u)) {
    const match = line.match(/^\s*(TUSHARE_TOKEN|A_PI_[A-Za-z0-9_]+)\s*=\s*(.*?)\s*$/u);
    if (!match) continue;
    environment[match[1]] = match[2].replace(/^(?:"(.*)"|'(.*)')$/u, "$1$2");
  }
  return environment;
}

/** @param {string} logPath */
function logTail(logPath) {
  try {
    return fs.readFileSync(logPath, "utf8").slice(-6000).trim();
  } catch {
    return "";
  }
}

/** @param {number} port */
function portIsOpen(port) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host: "127.0.0.1", port });
    /** @param {boolean} open */
    const finish = (open) => {
      socket.destroy();
      resolve(open);
    };
    socket.setTimeout(500);
    socket.once("connect", () => finish(true));
    socket.once("timeout", () => finish(false));
    socket.once("error", () => finish(false));
  });
}

/**
 * @param {import('electron').App} app
 * @param {string} desktopRoot
 * @param {{port?:number}} [options]
 */
function createServiceManager(app, desktopRoot, options = {}) {
  const port = options.port || 8000;
  if (!Number.isInteger(port) || port < 1024 || port > 65535)
    throw new Error("本地服务端口无效。");
  const serviceUrl = `https://127.0.0.1:${port}/`;
  let lastProbeError = "";
  /** @param {string} projectRoot */
  function certificatePath(projectRoot) {
    return path.join(projectRoot, "local_tls", "localhost.crt.pem");
  }

  /** @param {string} projectRoot */
  function localCertificateFingerprint(projectRoot) {
    return new X509Certificate(fs.readFileSync(certificatePath(projectRoot)))
      .fingerprint256;
  }

  /** @param {string} projectRoot @param {string} pathname */
  function requestJson(projectRoot, pathname) {
    return new Promise((resolve, reject) => {
      const trustedFingerprint = localCertificateFingerprint(projectRoot);
      const request = https.get(
        new URL(pathname, serviceUrl),
        {
          // Electron's bundled OpenSSL rejects a pinned self-signed leaf when
          // it is supplied as a CA. Verify the exact certificate ourselves so
          // behavior stays consistent across Electron and system Node builds.
          rejectUnauthorized: false,
          agent: false,
          timeout: 2500,
        },
        (response) => {
          const tlsSocket = /** @type {import("node:tls").TLSSocket} */ (
            response.socket
          );
          const presentedFingerprint =
            tlsSocket.getPeerCertificate().fingerprint256 || "";
          if (presentedFingerprint !== trustedFingerprint) {
            response.resume();
            request.destroy(
              new Error(
                `本地 HTTPS 证书指纹不匹配（实际 ${presentedFingerprint || "空"}，期望 ${trustedFingerprint}）`,
              ),
            );
            return;
          }
          let body = "";
          response.setEncoding("utf8");
          response.on("data", (chunk) => {
            body += chunk;
            if (body.length > 1024 * 1024)
              request.destroy(new Error("响应过大"));
          });
          response.on("end", () => {
            try {
              resolve({
                status: response.statusCode || 0,
                body: JSON.parse(body),
              });
            } catch (error) {
              reject(error);
            }
          });
        },
      );
      request.on("timeout", () =>
        request.destroy(new Error("本地 HTTPS 服务响应超时")),
      );
      request.on("error", reject);
    });
  }

  function findProjectRoot() {
    const starts = [
      process.env.A_PI_PROJECT_ROOT,
      app.getAppPath(),
      path.dirname(process.execPath),
      desktopRoot,
    ].filter((value) => typeof value === "string");
    const visited = new Set();

    for (const start of starts) {
      let current = path.resolve(start);
      for (let depth = 0; depth < 9; depth += 1) {
        if (visited.has(current)) break;
        visited.add(current);
        const hasServer =
          fs.existsSync(path.join(current, "start_a_pi.ps1")) &&
          fs.existsSync(path.join(current, "quant", "api_server.py"));
        const siteRoot = path.resolve(current, "..", "a_pi_site");
        const hasSite = [
          path.join(siteRoot, "build", "index.html"),
          path.join(siteRoot, "index.html"),
        ].some((indexPath) => fs.existsSync(indexPath));
        if (hasServer && hasSite) return current;
        const parent = path.dirname(current);
        if (parent === current) break;
        current = parent;
      }
    }

    throw new Error(
      "找不到 A-pi Python 服务和前端文件。请确认桌面客户端仍位于工作台项目目录中。",
    );
  }

  /** @param {string} projectRoot */
  async function readHealth(projectRoot) {
    try {
      const response = await requestJson(projectRoot, "api/health");
      if (response.status !== 200) return null;
      const health = response.body;
      if (health.ok === true && Object.keys(health).length === 1) {
        const session = await requestJson(projectRoot, "api/auth/session");
        if (session.status !== 401 || session.body.error !== "unauthorized")
          return null;
        return { ok: true, authentication_required: true };
      }
      return health;
    } catch (caught) {
      const error = caught instanceof Error ? caught : new Error(String(caught));
      lastProbeError = error.message;
      return null;
    }
  }

  /** @param {{ok?:boolean,api_features?:string[],api_version?:string,authentication_required?:boolean} | null} health */
  function backendIsReady(health) {
    return Boolean(
      health &&
      health.ok &&
      (health.authentication_required === true ||
        REQUIRED_API_FEATURES.every((feature) =>
          health.api_features?.includes(feature),
        )),
    );
  }

  /** @param {string} projectRoot */
  function startLocalService(projectRoot) {
    const pythonPath = path.join(projectRoot, ".venv", "Scripts", "python.exe");
    const tlsCertificate = certificatePath(projectRoot);
    const tlsKey = path.join(projectRoot, "local_tls", "localhost.key.pem");
    for (const [label, requiredPath] of [
      ["Python 环境", pythonPath],
      ["HTTPS 证书", tlsCertificate],
      ["HTTPS 私钥", tlsKey],
    ]) {
      if (!fs.existsSync(requiredPath)) {
        throw new Error(`${label}不存在：${requiredPath}`);
      }
    }
    const logPath = path.join(
      app.getPath("temp"),
      "a-pi-desktop-service-start.log",
    );
    fs.writeFileSync(
      logPath,
      `${new Date().toISOString()} desktop launching local Python service\n`,
      "utf8",
    );
    const logFd = fs.openSync(logPath, "a");
    let child;
    try {
      child = spawn(
        pythonPath,
        [
          "-B",
          "-m",
          "quant.api_server",
          "--host",
          "127.0.0.1",
          "--port",
          String(port),
          "--tls-cert",
          tlsCertificate,
          "--tls-key",
          tlsKey,
        ],
        {
          cwd: projectRoot,
          detached: true,
          env: serviceEnvironment(projectRoot),
          stdio: ["ignore", logFd, logFd],
          windowsHide: true,
        },
      );
    } finally {
      fs.closeSync(logFd);
    }
    /** @type {Error | null} */
    let startupError = null;
    child.on("error", (error) => {
      startupError = error;
    });
    child.unref();
    return {
      child,
      logPath,
      getStartupError: () => startupError,
      getLogTail: () => logTail(logPath),
    };
  }

  /** @param {string} projectRoot */
  async function ensureLocalService(projectRoot) {
    let health = await readHealth(projectRoot);
    if (backendIsReady(health)) return;

    // A supervisor may already be bringing the service back while the first
    // HTTPS probe is in flight. Never race it by binding a second server.
    if (await portIsOpen(port)) {
      for (let attempt = 0; attempt < 10; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        health = await readHealth(projectRoot);
        if (backendIsReady(health)) return;
      }
      throw new Error(
        `端口 ${port} 已被占用，但现有服务未通过 HTTPS/版本校验${lastProbeError ? `（${lastProbeError}）` : ""}。请运行管理员重启脚本。`,
      );
    }

    const startup = startLocalService(projectRoot);
    const deadline = Date.now() + START_TIMEOUT_MS;
    let exitedAt = 0;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      health = await readHealth(projectRoot);
      if (backendIsReady(health)) return;
      if (startup.getStartupError()) {
        throw new Error(
          `本地服务启动失败：${startup.getStartupError()?.message || "无法创建 Python 进程"}。\n日志：${startup.logPath}`,
        );
      }
      if (startup.child.exitCode !== null) {
        if (!exitedAt) exitedAt = Date.now();
        // A SYSTEM supervisor can win the port race just after our first
        // probe. Give that existing service time to finish its own startup.
        if (Date.now() - exitedAt < 8_000) continue;
        const cause = startup.getStartupError()?.message ||
          `Python 进程提前退出（代码 ${startup.child.exitCode}）`;
        const output = startup.getLogTail();
        throw new Error(
          `本地服务启动失败：${cause}。\n${output || "启动日志为空。"}\n日志：${startup.logPath}`,
        );
      }
    }

    const detail =
      health && health.api_version
        ? `检测到 API 版本 ${health.api_version}，需要 ${REQUIRED_API_VERSION}。`
        : `本地 Python 服务没有在规定时间内就绪${lastProbeError ? `（${lastProbeError}）` : ""}。`;
    throw new Error(
      `${detail}\n${startup.getLogTail() || "启动日志为空。"}\n请查看 ${startup.logPath}。`,
    );
  }

  return {
    findProjectRoot,
    ensureLocalService,
    startLocalService,
    readHealth,
    backendIsReady,
    localCertificateFingerprint,
    getLastProbeError: () => lastProbeError,
  };
}
module.exports = {
  createServiceManager,
  serviceEnvironment,
  APP_URL,
  APP_URL_LOCALHOST,
};
