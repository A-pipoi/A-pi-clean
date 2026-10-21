import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const html = readFileSync(
  new URL("../dist/index.html", import.meta.url),
  "utf8",
);
const source = readFileSync(
  new URL("../dist/startup.js", import.meta.url),
  "utf8",
);

function fixture({
  reduced = false,
  type = "navigate",
  seen = false,
  blocked = false,
} = {}) {
  const dom = new JSDOM(html, {
    url: "https://localhost/",
    runScripts: "outside-only",
  });
  const w = dom.window;
  const pending = new Map();
  let next = 0;
  w.setTimeout = (fn, ms) => {
    const id = ++next;
    pending.set(id, { fn, ms });
    return id;
  };
  w.clearTimeout = (id) => pending.delete(id);
  w.matchMedia = () => ({
    matches: reduced,
    addEventListener() {},
    removeEventListener() {},
  });
  w.PerformanceNavigationTiming = class {
    constructor() {
      this.type = type;
    }
  };
  w.performance.getEntriesByType = () => [new w.PerformanceNavigationTiming()];
  if (seen) w.sessionStorage.setItem("a_pi_opening_seen", "1");
  if (blocked)
    Object.defineProperty(w, "sessionStorage", {
      get() {
        throw new Error("blocked");
      },
    });
  w.eval(source);
  w.document.dispatchEvent(new w.Event("DOMContentLoaded"));
  return {
    dom,
    w,
    doc: w.document,
    pending,
    run(ms) {
      const [id, task] =
        [...pending.entries()].find(([, task]) => task.ms === ms) || [];
      assert.ok(task, `timer ${ms} exists`);
      pending.delete(id);
      task.fn();
    },
  };
}

test("opening is bounded to 2.4 seconds without app load, restores interaction, preserves branding", () => {
  const f = fixture();
  try {
    assert.ok(f.doc.documentElement.classList.contains("desktop-startup"));
    assert.equal(f.doc.getElementById("authView").inert, true);
    assert.match(
      f.doc.querySelector(".desktop-splash-mascot").src,
      /a-pi-mark\.svg$/,
    );
    assert.equal(
      f.doc.querySelectorAll(".desktop-splash-sparkle,.desktop-splash-loading")
        .length,
      0,
    );
    assert.equal(f.doc.getElementById("desktopSplashSkip").textContent, "跳过");
    assert.equal(f.w.sessionStorage.getItem("a_pi_opening_seen"), "1");
    f.run(1900);
    assert.ok(
      f.doc.getElementById("desktopSplash").classList.contains("is-leaving"),
    );
    f.run(500);
    assert.equal(f.doc.getElementById("desktopSplash"), null);
    assert.ok(!f.doc.documentElement.classList.contains("desktop-startup"));
    assert.notEqual(f.doc.getElementById("authView").inert, true);
    assert.equal(f.pending.size, 0);
  } finally {
    f.dom.window.close();
  }
});

test("skip is immediate, returns keyboard focus to login, and remains safe on repeated clicks", () => {
  const f = fixture();
  try {
    const skip = f.doc.getElementById("desktopSplashSkip");
    skip.focus();
    skip.click();
    skip.click();
    assert.equal(f.doc.getElementById("desktopSplash"), null);
    assert.equal(f.doc.activeElement.id, "authUsername");
    assert.equal(f.pending.size, 0);
  } finally {
    f.dom.window.close();
  }
});

test("Escape skips without waiting for the application or a market response", () => {
  const f = fixture();
  try {
    f.doc.dispatchEvent(new f.w.KeyboardEvent("keydown", { key: "Escape" }));
    assert.ok(!f.doc.documentElement.classList.contains("desktop-startup"));
    assert.notEqual(f.doc.getElementById("appShell").inert, true);
  } finally {
    f.dom.window.close();
  }
});

test("reload, history navigation, seen session and reduced motion never replay the opening", () => {
  for (const options of [
    { type: "reload" },
    { type: "back_forward" },
    { seen: true },
    { reduced: true },
    { type: "reload", blocked: true },
  ]) {
    const f = fixture(options);
    try {
      assert.ok(!f.doc.documentElement.classList.contains("desktop-startup"));
      assert.equal(f.pending.size, 0);
      assert.notEqual(f.doc.getElementById("authView").inert, true);
    } finally {
      f.dom.window.close();
    }
  }
});

test("script failure and visible authentication failure remove the overlay immediately", async () => {
  for (const scenario of ["error", "unhandledrejection", "auth"]) {
    const f = fixture();
    try {
      if (scenario === "auth") {
        const error = f.doc.getElementById("authError");
        error.textContent = "无法连接认证服务";
        error.hidden = false;
        await Promise.resolve();
      } else f.w.dispatchEvent(new f.w.Event(scenario));
      assert.equal(f.doc.getElementById("desktopSplash"), null);
      assert.equal(f.pending.size, 0);
      assert.notEqual(f.doc.getElementById("authView").inert, true);
    } finally {
      f.dom.window.close();
    }
  }
});

test("hard deadline removes the overlay even if the normal exit callback is lost", () => {
  const f = fixture({ blocked: true });
  try {
    f.run(2500);
    assert.equal(f.doc.getElementById("desktopSplash"), null);
    assert.equal(f.pending.size, 0);
  } finally {
    f.dom.window.close();
  }
});
