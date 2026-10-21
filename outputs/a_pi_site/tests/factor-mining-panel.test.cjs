const assert = require('node:assert/strict');
const test = require('node:test');
const { readFileSync } = require('node:fs');
const path = require('node:path');

test('template census escapes failures and never invents missing metrics', async () => {
  const {miningCensusMarkup} = await import('../dist/factor-mining-panel.mjs');
  const markup=miningCensusMarkup({n_tested:1,family_ranking:[],templates:[{name:'<img src=x onerror=alert(1)>',category:'financial',status:'failed',n_tested:1,error:'<script>bad</script>'}]});
  assert.doesNotMatch(markup,/<img|<script>/);
  assert.match(markup,/&lt;script&gt;/);
  assert.match(markup,/不重复计数/);
  assert.match(markup,/<td>—<\/td>/);
});

test('busy research is not reported as failure and honors Retry-After', async () => {
  const { factorMiningFailureFeedback } = await import('../dist/factor-mining-panel.mjs');
  assert.match(factorMiningFailureFeedback(429, 'operation_already_active', '180'), /数十分钟.*180/);
  assert.match(factorMiningFailureFeedback(429, 'operation_global_capacity', null), /其他研究任务/);
  assert.match(factorMiningFailureFeedback(429, 'backtest_already_active', 'bad'), /后台任务/);
  assert.equal(factorMiningFailureFeedback(500, 'operation_already_active', '180'), null);
  assert.equal(factorMiningFailureFeedback(429, 'heavy_hourly_budget', '180'), null);
});

test('factor mining status maps to clear labels and semantic tones', async () => {
  const panel = await import('../dist/factor-mining-panel.mjs');
  assert.equal(panel.factorMiningStatusLabel('accepted'), '验收通过 · 待提升');
  assert.equal(panel.factorMiningTagClass('accepted'), 'is-pass');
  assert.equal(panel.factorMiningTagClass('acceptance_claimed'), 'is-warn');
  assert.equal(panel.factorMiningTagClass('rejected'), 'is-fail');
});

test('factor template label matches load button and sits immediately before it', () => {
  const html = readFileSync(path.join(__dirname, '..', 'dist', 'index.html'), 'utf8');
  const css = readFileSync(path.join(__dirname, '..', 'dist', 'styles.css'), 'utf8');
  assert.match(html, /<select id="factorTemplateSelect"[\s\S]*?<label class="secondary-button factor-template-label" for="factorTemplateSelect">基础模板<\/label><button class="secondary-button" id="factorTemplateLoadButton"/);
  assert.match(css, /\.factor-template-row label\.factor-template-label\s*\{[^}]*min-height:\s*44px;[^}]*font-weight:\s*var\(--weight-semibold\)/s);
});

test('sealed evaluation report escapes untrusted expressions and includes PIT diagnostics', async () => {
  const panel = await import('../dist/factor-mining-panel.mjs');
  const escapeHtml = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
  const markup = panel.factorEvaluationReportMarkup({
    expression: '<script>bad()</script>',
    bh_adjusted_p_value: 0.03,
    evaluation: {
      coverage: 0.9,
      n_tested: 3,
      horizons: { '5': { mean_ic: 0.12, monthly_hit_rate: 0.6, environment_slices: { industry: { status: 'unavailable', reason: '<missing PIT>' } } } },
    },
  }, escapeHtml);

  assert.match(markup, /&lt;script&gt;bad\(\)&lt;\/script&gt;/);
  assert.doesNotMatch(markup, /<script>/);
  assert.match(markup, /&lt;missing PIT&gt;/);
  assert.match(markup, /BH q/);
});

test('factor evaluation report safely renders a missing report as empty', async () => {
  const panel = await import('../dist/factor-mining-panel.mjs');
  assert.equal(panel.factorEvaluationReportMarkup({}), '');
});

test('training report separates acceptance and never displays sealed BH values', async () => {
  const panel = await import('../dist/factor-mining-panel.mjs');
  const candidate = { bh_adjusted_p_value: 0.031234, evaluation: { horizons: { '5': { mean_ic: 0.12 } } } };
  const training = panel.factorEvaluationReportMarkup(candidate, undefined, 'training');
  assert.match(training, /查看训练报告/);
  assert.match(training, /不代表密封验收通过/);
  assert.doesNotMatch(training, /3\.12e-2/);
  assert.match(panel.factorEvaluationReportMarkup(candidate), /查看密封验收报告/);
});

test('matrix escapes keys, preserves missing values and exposes exact sample counts', async () => {
  const { factorCorrelationMatrixMarkup } = await import('../dist/factor-mining-panel.mjs');
  const report = {
    keys: ['__candidate__', '<img src=x onerror=alert(1)>'],
    matrix: [
      [{ pearson: 1, spearman: 1, observations: 3, paired_samples: 12 }, { pearson: null, spearman: null, observations: 0, paired_samples: 0 }],
      [{ pearson: NaN, spearman: 1.5 }, { pearson: 0, spearman: -0.5, observations: 2, paired_samples: 6 }],
    ],
    sealed_dates_used: false,
    scope: 'enabled_registry_plus_current_candidate_training_only',
  };
  const markup = factorCorrelationMatrixMarkup(report);
  assert.doesNotMatch(markup, /<img|NaN|1\.500/);
  assert.match(markup, /&lt;img/);
  assert.match(markup, /当前候选/);
  assert.match(markup, /3 天 · 12 对/);
  assert.match(markup, /<span>—<\/span>/);
  assert.match(markup, /<span>0\.000<\/span>/);
  assert.match(markup, /<span>-0\.500<\/span>/);
  assert.equal((markup.match(/scope="col"/g) || []).length, 6);
  assert.match(factorCorrelationMatrixMarkup({ ...report, sealed_dates_used: true }), /未通过校验/);
  assert.match(factorCorrelationMatrixMarkup({ ...report, matrix: [[]] }), /未通过校验/);
  assert.equal(factorCorrelationMatrixMarkup(undefined), '');
});

test('report content confines wide matrices and uses theme-aware summary colors', () => {
  const css = readFileSync(path.join(__dirname, '..', 'dist', 'styles.css'), 'utf8');
  assert.match(css, /\.factor-report-content\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\);[^}]*min-width:\s*0/s);
  assert.match(css, /\.factor-report-table-wrap\s*\{[^}]*min-width:\s*0;[^}]*overflow-x:\s*auto/s);
  assert.match(css, /\.factor-evaluation-report > summary\s*\{[^}]*color:\s*var\(--purple\)/s);
  assert.match(css, /\.factor-report-table\.factor-correlation-matrix thead th:first-child\s*\{[^}]*text-align:\s*center/s);
});

test('composition weight report escapes labels and keeps negative or missing weights explicit', async () => {
  const { compositionResultMarkup } = await import('../dist/factor-mining-panel.mjs');
  const markup = compositionResultMarkup({ weighting: 'icir', weights: { '<script>bad()</script>': -0.5, missing: NaN } });
  assert.doesNotMatch(markup, /<script>|NaN/);
  assert.match(markup, /-0\.5000/);
  assert.match(markup, /<td>—<\/td>/);
  assert.match(markup, /不代表可交易或验收通过/);
});
