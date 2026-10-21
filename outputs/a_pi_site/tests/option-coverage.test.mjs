import assert from "node:assert/strict";
import test from "node:test";
import { splitObservedWindows } from "../dist/data-extension-panel.mjs";

test("option observations never draw a continuous line across missing years", () => {
  const windows = splitObservedWindows([
    { date: "2026-09-30", value: 0.8 },
    { date: "2015-02-09", value: 0.6 },
    { date: "2015-02-13", value: 0.7 },
  ]);
  assert.deepEqual(
    windows.map((window) => window.map((point) => point.date)),
    [["2015-02-09", "2015-02-13"], ["2026-09-30"]],
  );
});
