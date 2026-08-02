// ESLint flat config (ESLint 9) for the Flask app's vanilla-JS files.
// See: https://eslint.org/docs/latest/use/configure/configuration-files
"use strict";

const js = require("@eslint/js");
const globals = require("globals");
const eslintConfigPrettier = require("eslint-config-prettier");

module.exports = [
  {
    // Vendor bundles and dependency dirs are never linted.
    ignores: ["app/static/vendor/**", "node_modules/**"],
  },
  js.configs.recommended,
  eslintConfigPrettier,
  {
    files: ["app/static/js/**/*.js"],
    languageOptions: {
      // Existing files mix ES5 style (var, function) with a few modern
      // features like optional chaining (?.); ES2020 parses all of it.
      ecmaVersion: 2020,
      sourceType: "script",
      globals: {
        ...globals.browser,
        // Quill is loaded globally from app/static/vendor/quill/quill.js
        Quill: "readonly",
      },
    },
    rules: {
      // The codebase intentionally uses ES5 `var`; don't force modernization.
      "no-var": "off",
      "prefer-const": "off",
      // Flag unused variables (catches real bugs) but allow `_`-prefixed ones.
      "no-unused-vars": ["warn", { args: "none", varsIgnorePattern: "^_" }],
      // Undefined identifiers are genuine bugs in browser scripts.
      "no-undef": "error",
    },
  },
];
