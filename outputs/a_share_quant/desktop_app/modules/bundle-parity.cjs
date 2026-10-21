const fs = require('node:fs');
const path = require('node:path');

// Verify every runtime module, not just its presence: stale stable bundles can
// otherwise pass packaging checks while probing a retired protocol.
/** @param {{extractFile: (archive: string, name: string) => Buffer}} asar
 * @param {string} archivePath @param {string} sourceRoot */
function verifyRuntimeParity(asar, archivePath, sourceRoot) {
  const files = ['main.cjs'];
  /** @param {string} relative */
  function visit(relative) {
    for (const entry of fs.readdirSync(path.join(sourceRoot, relative), { withFileTypes: true })) {
      const name = path.posix.join(relative, entry.name);
      if (entry.isDirectory()) visit(name);
      else if (entry.isFile() && name.endsWith('.cjs')) files.push(name);
    }
  }
  visit('modules');
  return files.filter((name) => {
    try {
      return !asar.extractFile(archivePath, name).equals(fs.readFileSync(path.join(sourceRoot, name)));
    } catch {
      return true;
    }
  });
}

module.exports = { verifyRuntimeParity };
