const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { createServiceManager } = require("../modules/service.cjs");

function createPackageLayout(siteEntry) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "a-pi-package-root-"));
  const projectRoot = path.join(root, "workbench", "outputs", "a_share_quant");
  const siteRoot = path.join(root, "workbench", "outputs", "a_pi_site");
  fs.mkdirSync(path.join(projectRoot, "quant"), { recursive: true });
  fs.mkdirSync(siteRoot, { recursive: true });
  fs.writeFileSync(path.join(projectRoot, "start_a_pi.ps1"), "");
  fs.writeFileSync(path.join(projectRoot, "quant", "api_server.py"), "");
  const indexPath = path.join(siteRoot, siteEntry);
  fs.mkdirSync(path.dirname(indexPath), { recursive: true });
  fs.writeFileSync(indexPath, "");
  return { root, projectRoot };
}

for (const siteEntry of ["index.html", path.join("build", "index.html")]) {
  test(`findProjectRoot accepts packaged frontend at ${siteEntry}`, (t) => {
    const { root, projectRoot } = createPackageLayout(siteEntry);
    t.after(() => fs.rmSync(root, { recursive: true, force: true }));

    const service = createServiceManager(
      { getAppPath: () => path.join(root, "app.asar") },
      path.join(root, "desktop"),
    );
    const previousRoot = process.env.A_PI_PROJECT_ROOT;
    process.env.A_PI_PROJECT_ROOT = projectRoot;
    t.after(() => {
      if (previousRoot === undefined) delete process.env.A_PI_PROJECT_ROOT;
      else process.env.A_PI_PROJECT_ROOT = previousRoot;
    });

    assert.equal(service.findProjectRoot(), projectRoot);
  });
}
