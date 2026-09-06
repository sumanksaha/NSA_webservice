// ESLint flat config (ESLint 9) for the Flask app's vanilla-JS files.
// Extended to parse TypeScript files under app/static/ts/.
// See: https://eslint.org/docs/latest/use/configure/configuration-files
"use strict";

const js = require("@eslint/js");
const globals = require("globals");
const eslintConfigPrettier = require("eslint-config-prettier");
const tseslint = require("typescript-eslint");

// Extract the plugin + parser from recommended, then manually scope
// the rules to TS files only (the default recommended config leaks
// @typescript-eslint rules into JS files).
const { plugin, parser } = tseslint.configs.recommended.reduce(
  (acc, cfg) => {
    if (cfg.plugins) acc.plugin = cfg.plugins;
    if (cfg.languageOptions && cfg.languageOptions.parser) acc.parser = cfg.languageOptions.parser;
    return acc;
  },
  { plugin: null, parser: null },
);

module.exports = [
  {
    // Vendor bundles and dependency dirs are never linted.
    ignores: ["app/static/vendor/**", "node_modules/**"],
  },
  js.configs.recommended,
  eslintConfigPrettier,

  // --- JavaScript files (existing) ---
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

  // --- TypeScript files (incremental migration) ---
  // Register the @typescript-eslint plugin + parser, scoped to TS files.
  {
    files: ["app/static/ts/**/*.ts"],
    plugins: plugin,
    languageOptions: {
      ecmaVersion: 2020,
      sourceType: "script",
      parser: parser || tseslint.parser,
      parserOptions: {
        ecmaVersion: 2020,
        sourceType: "script",
      },
      globals: {
        ...globals.browser,
      },
    },
    rules: {
      // --- Recommended rules, scoped to TS files only ---
      "@typescript-eslint/ban-ts-comment": "error",
      // Quill's type definitions use `any` extensively; suppress rather than fight.
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-extra-non-null-assertion": "error",
      "@typescript-eslint/no-misused-new": "error",
      "@typescript-eslint/no-namespace": "off",
      "@typescript-eslint/no-non-null-asserted-optional-chain": "error",
      "@typescript-eslint/no-unnecessary-type-constraint": "error",
      "@typescript-eslint/no-unsafe-declaration-merging": "error",
      "@typescript-eslint/no-unsafe-function-type": "error",
      "@typescript-eslint/no-wrapper-object-types": "error",
      "@typescript-eslint/prefer-as-const": "error",
      "@typescript-eslint/prefer-namespace-keyword": "error",
      "@typescript-eslint/triple-slash-reference": "error",
      "@typescript-eslint/no-this-alias": "error",
      "@typescript-eslint/no-duplicate-enum-values": "error",
      "@typescript-eslint/no-empty-object-type": "error",

      // --- Custom rules mirroring the JS config ---
      // Use TS-specific unused-vars (handles imports/destructuring).
      "no-unused-vars": "off",
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { args: "none", varsIgnorePattern: "^_" },
      ],
      // The TS compiler handles these; ESLint's versions conflict.
      "no-undef": "off",
      "no-redeclare": "off",
      "@typescript-eslint/no-redeclare": "error",
      // Keep no-array-constructor but allow TS arrays.
      "no-array-constructor": "off",
      "@typescript-eslint/no-array-constructor": "error",
      // no-unused-expressions — use TS version.
      "no-unused-expressions": "off",
      "@typescript-eslint/no-unused-expressions": "error",
      // Our codebase uses ES5-style var in IIFEs; don't force modernization.
      "no-var": "off",
      "prefer-const": "off",
      // Allow namespace merging for the global Window augmentation.
      "@typescript-eslint/no-require-imports": "off",
    },
  },
];
