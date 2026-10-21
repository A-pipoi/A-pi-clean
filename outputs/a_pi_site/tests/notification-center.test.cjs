const assert = require('node:assert/strict');
const test = require('node:test');

test('notification ids are bounded and safe for data attributes', async () => {
  const module = await import('../dist/notification-center.mjs');
  const id = module.notificationId('paper-order', 'AP/2026 09 30 #1');
  assert.equal(id, 'paper-order:AP-2026-09-30-1');
  assert.ok(module.notificationId('x', 'a'.repeat(200)).length <= 98);
});

test('notifications reflect service, data-quality, paper and validation state', async () => {
  const module = await import('../dist/notification-center.mjs');
  const items = module.buildNotifications({
    health: { online: false },
    workspace: { warnings: ['行情覆盖不足'], latest_trade_date: '2026-09-29' },
    paper: { orders: [{ order_id: 'order-1', symbol: '600000.SH', side: 'BUY', status: 'FILLED', filled_quantity: 100, created_at: '2026-09-29T08:00:00Z' }] },
    backtest: { experiment_id: 'bt-1', strategy_validation: { passed: true, folds: 4 } },
  }, status => `状态 ${status}`, new Date('2026-09-30T08:00:00.000Z'));

  assert.equal(items.length, 4);
  assert.equal(items[0].id, 'service-offline');
  assert.equal(items[1].body, '行情覆盖不足');
  assert.match(items[2].body, /已成交 100 股，状态：状态 FILLED/);
  assert.equal(items[3].timestamp, '2026-09-30T08:00:00.000Z');
});

test('pending orders are informational, never reported as filled', async () => {
  const module = await import('../dist/notification-center.mjs');
  const items = module.buildNotifications({paper:{orders:[{order_id:'pending',symbol:'600000.SH',side:'BUY',status:'NEW',quantity:100,filled_quantity:0}]}}, status=>status);
  const order=items.find(item=>item.id==='paper-order:pending');
  assert.equal(order.type,'info');
  assert.doesNotMatch(order.title,/已成交/);
  assert.match(order.body,/已成交 0 股/);
});

test('notification timestamps safely fall back when input is missing or invalid', async () => {
  const module = await import('../dist/notification-center.mjs');
  assert.equal(module.notificationTime(null), '刚刚');
  assert.equal(module.notificationTime('not-a-date'), 'not-a-date');
});
