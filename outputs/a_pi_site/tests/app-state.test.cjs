const assert = require('node:assert/strict');
const test = require('node:test');

test('scoped module state observes later updates and publishes changes to other consumers', async () => {
  const { connectState } = await import('../dist/app-state.mjs');
  const state = { account: { equity: 100 }, locale: 'zh-CN', privateToken: 'not-exposed' };
  const view = connectState(state, ['account', 'locale'], { render: () => 'rendered' });
  const presenter = connectState(state, ['account'], {});
  presenter.account = { equity: 200 };
  assert.equal(view.account.equity, 200);
  state.locale = 'en-US';
  assert.equal(view.locale, 'en-US');
  assert.equal('privateToken' in view, false);
  assert.equal(view.render(), 'rendered');
});
