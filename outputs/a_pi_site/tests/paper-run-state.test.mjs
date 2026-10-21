import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";
import {
  paperRunExplanation,
  paperCycleMessage,
  paperSchedulerStatus,
  initPaperPage,
  applyPaperState,
} from "../dist/paper-page.mjs";
import { configureApiClient } from "../dist/api-client.mjs";

test("thread availability is distinct from execution; absent fields remain unknown", () => {
  assert.match(paperRunExplanation({ running: true }), /调度在线 · 等待执行/);
  assert.match(
    paperRunExplanation({ running: true, execution_in_progress: true }),
    /纸面循环执行中/,
  );
  assert.match(paperRunExplanation({ enabled: false }), /自动调度已关闭/);
  assert.match(paperRunExplanation(null), /状态待加载/);
  assert.match(paperRunExplanation(null), /每日运行审计：暂无记录/);
  assert.doesNotMatch(
    paperRunExplanation(null),
    /通知状态请查看通知中心|外部推送/,
  );
  assert.doesNotMatch(paperRunExplanation(null), /已发送/);
});

test("managed scheduler stays online when the independent account thread is false", () => {
  const managed = {
    scheduler_running: true,
    running: false,
    execution_in_progress: false,
  };
  assert.match(paperRunExplanation(managed), /调度在线 · 等待执行/);
  assert.equal(paperSchedulerStatus(managed), "调度在线 · 等待执行");
  assert.equal(
    paperSchedulerStatus({ ...managed, execution_in_progress: true }),
    "执行中",
  );
  assert.equal(paperSchedulerStatus({ ...managed, enabled: false }), "已停用");
  assert.equal(
    paperSchedulerStatus({ scheduler_running: false, running: true }),
    "待启动",
  );
  assert.equal(paperSchedulerStatus({ running: true }), "调度在线 · 等待执行");
});

test("snake_case maintenance stages map to user labels and preserve unknown stages honestly", () => {
  const explanation = paperRunExplanation({
    last_result: {
      outcome: "governance_blocked",
      maintenance_stages: {
        record_quotes: { status: "completed" },
        record_equity: { status: "completed" },
        corporate_actions: { status: "pending" },
        process_pending_orders: { status: "waiting_next_session" },
        strategy_validation: { status: "blocked", reason: "验证未通过" },
        new_stage: { status: "pending" },
      },
    },
  });
  for (const expected of [
    "交易门禁未通过",
    "行情更新：已完成",
    "权益估值：已完成",
    "公司行动处理：待执行",
    "待执行委托处理：等待下一交易日",
    "策略验证：校验阻止交易（验证未通过）",
    "new_stage：待执行",
  ])
    assert.ok(explanation.includes(expected), expected);
});

test("maintenance-only result explains no trades, actual trading-day schedule and audit retries", () => {
  const explanation = paperRunExplanation({
    authority: "backend",
    scheduled_window: "16:00:00–16:10:59",
    maintenance: {
      latest: {
        run_date: "2026-10-09",
        status: "waiting_data",
        attempts: 2,
        reason_code: "recovering_or_retrying",
        started_at: "2026-10-09T16:01:00+08:00",
        finished_at: "2026-10-09T16:01:10+08:00",
        next_attempt_at: "2026-10-09T16:02:10+08:00",
        result: {
          outcome: "maintenance_only",
          no_trade_reason: "未到调仓日",
          generated_orders: 0,
          rebalance_schedule: {
            interval_trading_days: 5,
            trading_days_since_previous: 0,
            due: false,
          },
          next_rebalance: {
            remaining_trading_days: 5,
            anchor_date: "2026-10-09",
            reason: "按实际交易日计数",
          },
          maintenance_stages: {
            quotes: { status: "completed" },
            pending_orders: { status: "blocked", reason: "校验未通过" },
          },
        },
      },
    },
  });
  for (const expected of [
    "已完成账户维护，未交易",
    "无交易原因：未到调仓日",
    "每 5 个交易日",
    "已间隔 0 个交易日",
    "剩余 5 个交易日",
    "行情更新：已完成",
    "待执行委托：校验阻止交易",
    "等待当日数据",
    "尝试 2 次",
    "后端计划重试：2026-10-09T16:02:10+08:00",
  ])
    assert.ok(explanation.includes(expected), expected);
});

test("missed window, errors, zero orders and newer schedule fields remain explicit", () => {
  const explanation = paperRunExplanation({
    last_error: "供应商失败",
    maintenance: { missed_window: true },
    last_result: {
      generated_orders: 0,
      rebalance_schedule: {
        elapsed_trading_days: 3,
        next_rebalance_date: "2026-10-13",
        next_rebalance_in_trading_days: 2,
      },
    },
  });
  for (const expected of [
    "最近执行错误：供应商失败",
    "接口未提供无交易原因",
    "已间隔 3 个交易日",
    "下次 2026-10-13",
    "距离下次 2 个交易日",
    "页面不会自动补跑",
  ])
    assert.ok(explanation.includes(expected), expected);
});

test("HTTP 200 skipped outcomes show exact reason and never claim completion", () => {
  for (const outcome of [
    "outside_window",
    "maintenance_not_due",
    "already_running",
  ]) {
    const result = {
      outcome,
      skip_reason: `真实原因 ${outcome}`,
      no_trade_reason: "另一原因",
    };
    assert.equal(
      paperCycleMessage(result),
      `策略日循环已跳过 · 真实原因 ${outcome}`,
    );
    assert.doesNotMatch(
      paperCycleMessage({ ...result, skipped: true }),
      /完成/,
    );
  }
  assert.match(paperCycleMessage({ skipped: true }), /接口未提供原因/);
  assert.match(
    paperCycleMessage({
      outcome: "no_trade",
      no_trade_reason: "没有可执行委托",
    }),
    /没有可执行委托/,
  );
});

test("existing DOM renders untrusted reasons as text; manual cycle sends empty payload once", async () => {
  const dom = new JSDOM(
    readFileSync(new URL("../dist/index.html", import.meta.url), "utf8"),
    { url: "http://localhost:8000" },
  );
  const previousDocument = globalThis.document;
  globalThis.document = dom.window.document;
  const requests = [],
    toasts = [];
  const hostile = "<img src=x onerror=alert(1)><script>alert(1)</script>";
  let release;
  const waiting = new Promise((resolve) => {
    release = resolve;
  });
  const client = configureApiClient({
    origin: "http://localhost:8000",
    onExpired() {},
    fetch: async (url, options) => {
      if (url === "/api/auth/session")
        return Response.json({
          authenticated: true,
          username: "test",
          role: "user",
          expires_at: "2030-01-01",
        });
      if (url === "/api/paper/research")
        return Response.json({ ok: true, enabled: false });
      requests.push({ url, options });
      if (url === "/api/paper/orders?page=1&page_size=20") {
        assert.equal(options?.method || "GET", "GET");
        return Response.json({
          ok: true,
          orders: [],
          page: 1,
          pages: 1,
          total: 0,
        });
      }
      assert.equal(url, "/api/paper/cycle", "不允许额外的写操作或未知请求");
      assert.equal(options?.method, "POST");
      await waiting;
      return Response.json({
        ok: true,
        skipped: true,
        outcome: "outside_window",
        skip_reason: "16:11 已超出执行窗口",
        scheduler: { running: true, execution_in_progress: false },
      });
    },
  });
  try {
    await client.boot();
    initPaperPage({
      setPaperState() {},
      getWorkspace: () => ({}),
      getRiskLimits: () => ({}),
      setText: (id, value) => {
        document.getElementById(id).textContent = value;
      },
      formatNumber: (value) => String(value),
      formatPercent: () => "0%",
      dataSourceLabel: () => "真实行情",
      localeText: (value) => value,
      escapeHtml: (value) => String(value),
      renderAllocationDonut() {},
      applySignalSnapshot() {},
      applyRiskSnapshot() {},
      getLocale: () => "zh",
      applyLocale() {},
      showToast: (value) => toasts.push(value),
      backtestPreset: () => {
        throw Error("研究策略不得参与纸面配置");
      },
    });
    const account = {
      equity: 100,
      cash: 100,
      market_value: 0,
      paper_trading_allowed: true,
    };
    applyPaperState(account, {
      running: true,
      execution_in_progress: true,
      last_result: { no_trade_reason: hostile },
    });
    const explanation = document.getElementById("paperRunExplanation");
    assert.equal(explanation.closest("details").open, false);
    assert.ok(explanation.textContent.includes(hostile));
    assert.equal(explanation.querySelector("img,script"), null);
    const button = document.getElementById("paperCycleButton");
    assert.equal(button.disabled, true);
    assert.equal(button.getAttribute("aria-busy"), "true");
    assert.match(document.getElementById("paperStatus").textContent, /执行中/);
    button.click();
    assert.equal(requests.length, 0);
    applyPaperState(account, {
      scheduler_running: true,
      running: false,
      execution_in_progress: false,
    });
    assert.equal(button.disabled, false);
    assert.match(
      document.getElementById("paperStatus").textContent,
      /等待执行/,
    );
    button.click();
    button.click();
    assert.equal(button.disabled, true);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, "/api/paper/cycle");
    assert.equal(requests[0].options.method, "POST");
    assert.deepEqual(JSON.parse(requests[0].options.body), {});
    release();
    for (let i = 0; i < 20 && button.disabled; i++)
      await new Promise((resolve) => setImmediate(resolve));
    assert.equal(button.disabled, false);
    assert.equal(button.textContent, "手动运行一次");
    assert.equal(document.getElementById("paperEquity").textContent, "¥ 100");
    assert.equal(toasts.at(-1), "策略日循环已跳过 · 16:11 已超出执行窗口");
    const writes = requests.filter(({ options }) => options?.method === "POST");
    assert.equal(writes.length, 1, "状态更新不得自动补跑");
    assert.deepEqual(
      requests.map(({ url, options }) => [url, options?.method || "GET"]),
      [
        ["/api/paper/cycle", "POST"],
        ["/api/paper/orders?page=1&page_size=20", "GET"],
      ],
      "运行后仅允许刷新委托列表，不得产生第二次循环请求",
    );
  } finally {
    globalThis.document = previousDocument;
    dom.window.close();
  }
});
