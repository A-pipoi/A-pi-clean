import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";
import { installPasswordReveal } from "../dist/password-reveal.mjs";

test("login password reveal toggles visibility accessibly without changing the value", () => {
  const html = readFileSync(
    new URL("../dist/index.html", import.meta.url),
    "utf8",
  );
  const dom = new JSDOM(html);
  const { document } = dom.window;
  const input = document.getElementById("authPassword");
  const button = document.querySelector(
    '[data-password-toggle="authPassword"]',
  );
  const labels = { 显示密码: "Show password", 隐藏密码: "Hide password" };

  assert.equal(input.type, "password");
  assert.equal(
    button.querySelector(".password-eye-show").hasAttribute("hidden"),
    true,
  );
  assert.equal(
    button.querySelector(".password-eye-hide").hasAttribute("hidden"),
    false,
  );

  installPasswordReveal(document, (value) => labels[value]);

  assert.equal(input.type, "password");
  assert.equal(
    button.querySelector(".password-eye-show").hasAttribute("hidden"),
    true,
  );
  assert.equal(
    button.querySelector(".password-eye-hide").hasAttribute("hidden"),
    false,
  );
  input.value = "private-test-value";
  button.click();
  assert.equal(input.type, "text");
  assert.equal(input.value, "private-test-value");
  assert.equal(button.getAttribute("aria-pressed"), "true");
  assert.equal(button.getAttribute("aria-label"), "Hide password");
  assert.equal(
    button.querySelector(".password-eye-show").hasAttribute("hidden"),
    false,
  );
  assert.equal(
    button.querySelector(".password-eye-hide").hasAttribute("hidden"),
    true,
  );
  button.click();
  assert.equal(input.type, "password");
  assert.equal(button.getAttribute("aria-pressed"), "false");
  assert.equal(button.getAttribute("aria-label"), "Show password");
  dom.window.close();
});
