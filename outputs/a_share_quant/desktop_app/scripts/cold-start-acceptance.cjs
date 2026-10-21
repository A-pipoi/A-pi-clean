const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createServiceManager } = require("../modules/service.cjs");

const projectRoot = path.resolve(__dirname, "../..");
const port = Number(process.argv[2] || 18080);
const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "a-pi-cold-start-"));
const app = { getPath: (name) => {
  assert.equal(name, "temp");
  return tempRoot;
} };
const manager = createServiceManager(app, path.resolve(__dirname, ".."), { port });
const startup = manager.startLocalService(projectRoot);

(async () => {
  try {
    let health = null;
    for (let attempt = 0; attempt < 75; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      health = await manager.readHealth(projectRoot);
      if (manager.backendIsReady(health)) break;
      if (startup.getStartupError() || startup.child.exitCode !== null) {
        throw new Error(startup.getStartupError()?.message || startup.getLogTail());
      }
    }
    assert.equal(manager.backendIsReady(health), true, startup.getLogTail());
    assert.equal(startup.child.exitCode, null);
    assert.match(startup.getLogTail(), /desktop launching local Python service/u);
    process.stdout.write(JSON.stringify({
      ok: true,
      port,
      python_pid: startup.child.pid,
      https_ready: true,
      authentication_required: health?.authentication_required === true,
      log_nonempty: startup.getLogTail().length > 0,
    }, null, 2));
  } finally {
    try {
      execFileSync("taskkill.exe", ["/PID", String(startup.child.pid), "/T", "/F"], {
        stdio: "ignore",
      });
    } catch {
      startup.child.kill();
    }
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
})().catch((error) => {
  console.error(error.stack || error.message || String(error));
  process.exitCode = 1;
});

