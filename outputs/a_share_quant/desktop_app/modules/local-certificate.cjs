const { X509Certificate } = require("node:crypto");

/** @param {string} url @param {string} certificatePem @param {string} fingerprint */
function matchesLocalCertificate(url, certificatePem, fingerprint) {
  try {
    const parsed = new URL(url);
    if (
      !["https://127.0.0.1:8000", "https://localhost:8000"].includes(
        parsed.origin,
      ) ||
      parsed.username ||
      parsed.password ||
      !fingerprint
    )
      return false;
    const certificate = new X509Certificate(certificatePem);
    const now = Date.now();
    if (
      now < Date.parse(certificate.validFrom) ||
      now > Date.parse(certificate.validTo)
    )
      return false;
    const nameMatches =
      parsed.hostname === "127.0.0.1"
        ? certificate.checkIP(parsed.hostname)
        : certificate.checkHost(parsed.hostname);
    return Boolean(nameMatches && certificate.fingerprint256 === fingerprint);
  } catch {
    return false;
  }
}

module.exports = { matchesLocalCertificate };
