const test = require('node:test');
const assert = require('node:assert/strict');

test('risk view renders numeric leverage limits and preserves unavailable-data warnings', async () => {
  const { APiRiskView } = await import('../dist/risk-view.js');
  const { JSDOM } = await import('jsdom');
  const dom = new JSDOM('<body></body>');
  const originalDocument = global.document;
  const elements = new Map();
  global.document = { getElementById(id) {
    if (!elements.has(id)) {
      const element = dom.window.document.createElement('div');
      elements.set(id, element);
    }
    return elements.get(id);
  } };
  try {
    let alerts;
    const limits = { max_single_weight: .3, max_industry_weight: .5, max_gross_leverage: 1, max_drawdown: .15 };
    const view = APiRiskView.create({
      setText(id, value) { global.document.getElementById(id).textContent = value; },
      localeText: value => String(value ?? ''), escapeHtml: value => String(value ?? ''),
      getRiskLimits: () => limits, setRiskLimits() {}, getWorkspaceSnapshot: () => null,
      getHealth: () => null, setAlertDetails(value) { alerts = value; },
      getCurrentUsername: () => 'pipoi',
    });
    view.apply({ equity: 100, market_value: 120, positions: [{ symbol: '000001.SZ', market_value: 120 }], data_quality: { warnings: ['日线不可用'] } }, null, limits);
    assert.equal(elements.get('homeLeverageLimit').textContent, '≤ 1.00×');
    assert.ok(alerts.some(item => item.title === '总杠杆超过 1.00×'));
    assert.ok(alerts.some(item => item.title === '日线不可用'));
  } finally { global.document = originalDocument; dom.window.close(); }
});

test('closing a viewed risk reminder suppresses only the unchanged alert for that user', async () => {
  const { APiRiskView } = await import('../dist/risk-view.js');
  const { JSDOM } = await import('jsdom');
  const dom = new JSDOM('<body></body>', { url: 'http://localhost' });
  const originalDocument = global.document;
  const originalStorage = global.localStorage;
  const elements = new Map();
  global.document = { getElementById(id) {
    if (!elements.has(id)) elements.set(id, dom.window.document.createElement('div'));
    return elements.get(id);
  } };
  global.localStorage = dom.window.localStorage;
  try {
    let alerts = [];
    let username = 'pipoi';
    const limits = { max_single_weight: .3, max_industry_weight: .5, max_gross_leverage: 1, max_drawdown: .15 };
    const view = APiRiskView.create({
      setText(id, value) { global.document.getElementById(id).textContent = value; },
      localeText: value => String(value ?? ''), escapeHtml: value => String(value ?? ''),
      getRiskLimits: () => limits, setRiskLimits() {}, getWorkspaceSnapshot: () => null,
      getHealth: () => null, getCurrentUsername: () => username,
      setAlertDetails(value) { alerts = value; },
    });
    const paper = { equity: 100, market_value: 120, positions: [{ symbol: '000001.SZ', market_value: 120 }], data_quality: { warnings: ['日线不可用'] } };
    view.apply(paper, null, limits);
    const dismissed = alerts.find(item => item.title === '日线不可用');
    assert.ok(dismissed?.id);
    view.dismissAlert(dismissed.id);
    assert.ok(!alerts.some(item => item.id === dismissed.id));
    view.apply(paper, null, limits);
    assert.ok(!alerts.some(item => item.id === dismissed.id), 'unchanged reminder stays dismissed after refresh');
    view.apply({ ...paper, data_quality: { warnings: ['行情数据已过期'] } }, null, limits);
    assert.ok(alerts.some(item => item.title === '行情数据已过期'), 'a changed reminder remains visible');
    username = 'another-user';
    view.apply(paper, null, limits);
    assert.ok(alerts.some(item => item.title === '日线不可用'), 'dismissal is isolated by username');
  } finally {
    global.document = originalDocument;
    if (originalStorage === undefined) delete global.localStorage;
    else global.localStorage = originalStorage;
    dom.window.close();
  }
});
