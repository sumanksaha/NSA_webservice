"use strict";

// Unit tests for app/static/js/search_snippet.js (escapeHtml + renderSnippet).
// Run with: node tests/js/test_search_snippet.js  (or npm run test:js)

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const file = path.join(
    __dirname,
    "..",
    "..",
    "app",
    "static",
    "js",
    "search_snippet.js"
);
const code = fs.readFileSync(file, "utf8");

// Run the script inside a sandbox that mimics a browser `window` global.
const sandbox = { window: {} };
vm.createContext(sandbox);
vm.runInContext(code, sandbox);

const escapeHtml = sandbox.window.escapeHtml;
const renderSnippet = sandbox.window.renderSnippet;

let failures = 0;

function test(name, fn) {
    try {
        fn();
        console.log("ok - " + name);
    } catch (err) {
        failures += 1;
        console.error("FAIL - " + name);
        console.error("    " + err.message);
    }
}

test("escapeHtml escapes HTML metacharacters", function () {
    assert.strictEqual(
        escapeHtml("<script>alert(1)</script>"),
        "&lt;script&gt;alert(1)&lt;/script&gt;"
    );
    assert.strictEqual(escapeHtml('a & b "q" \'s\''), "a &amp; b &quot;q&quot; &#39;s&#39;");
});

test("renderSnippet keeps the server's <mark> tags", function () {
    assert.strictEqual(
        renderSnippet("found <mark>Acme</mark> Foods"),
        "found <mark>Acme</mark> Foods"
    );
});

test("renderSnippet escapes literal <mark>-shaped text from DB content", function () {
    // OCR text containing a literal closing mark + script must not become a
    // live closing tag followed by executable HTML.
    const out = renderSnippet("x</mark><script>alert(1)</script><mark>y");
    assert.ok(!out.includes("<script>"), "script tag must stay escaped: " + out);
    assert.ok(out.includes("&lt;script&gt;"), "script text must be HTML-escaped");
});

test("renderSnippet escapes plain HTML but keeps marks intact", function () {
    const out = renderSnippet("a <b>bold</b> <mark>term</mark>");
    assert.ok(out.includes("&lt;b&gt;"), "plain tags must be escaped");
    assert.ok(out.includes("<mark>term</mark>"), "server marks must survive");
});

test("renderSnippet escapes entities before re-marking", function () {
    const out = renderSnippet("Fish &amp; <mark>Chips</mark>");
    assert.ok(out.includes("Fish &amp;amp;"), "existing entities stay escaped");
    assert.ok(out.includes("<mark>Chips</mark>"));
});

test("renderSnippet handles empty and missing input", function () {
    assert.strictEqual(renderSnippet(""), "");
    assert.strictEqual(renderSnippet(null), "");
    assert.strictEqual(renderSnippet(undefined), "");
});

if (failures > 0) {
    console.error("\n" + failures + " test(s) failed");
    process.exit(1);
}
console.log("\nAll tests passed");
