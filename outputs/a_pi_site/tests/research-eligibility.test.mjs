import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { installBacktestResearchControls, buildBacktestPreset } from '../dist/backtest-research-controls.mjs';

test('research policy defaults off, uses labeled controls and rejects invalid sessions', () => {
  const {document} = new JSDOM('<html lang="en"><body><form><label>Strategy<select id="strategySelect"><option>baseline</option></select></label></form></body></html>').window;
  installBacktestResearchControls(document);
  installBacktestResearchControls(document);
  const policy = document.getElementById('backtestDataQualityPolicy');
  const age = document.getElementById('backtestMinListingSessions');
  const audit = document.getElementById('backtestExcludeAudit');
  assert.equal(document.querySelectorAll('#backtestDataQualityPolicy').length, 1);
  assert.equal(age.disabled, true);
  assert.equal(buildBacktestPreset({doc:document}).data_quality_policy, undefined);
  policy.value = 'enabled';
  policy.dispatchEvent(new document.defaultView.Event('change'));
  assert.equal(age.disabled, false);
  assert.equal(audit.disabled, false);
  audit.value = 'enabled';
  const payload = buildBacktestPreset({doc:document});
  assert.equal(payload.min_listing_sessions, 120);
  assert.equal(payload.exclude_nonstandard_audit, true);
  assert.equal(buildBacktestPreset({doc:document, researchOnly:false}).data_quality_policy, undefined);
  assert.ok(age.labels.length);
  assert.ok(age.getAttribute('aria-describedby'));
  age.value = '1.5';
  assert.throws(() => buildBacktestPreset({doc:document}), /整数/);
});
