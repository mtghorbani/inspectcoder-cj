"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { clearCliOverrides } = require("./cli-environment");

test("clean editor harness removes restored legacy SDK overrides and preserves real HOME and config", () => {
  const env = {
    HOME: "/real/home",
    INSPECT_CJ_CONFIG: "/isolated/config.json",
    PATH: "/usr/bin",
    CJC: "/stale/cjc",
    CJDB: "/stale/cjdb",
    CJPM: "/stale/cjpm",
    CANGJIE_HOME: "/stale/sdk",
    INSPECT_CJ_SYSROOT: "/stale/sysroot",
    CJDB_PYTHON_FRAMEWORK: "/stale/python",
    DYLD_LIBRARY_PATH: "/stale/lib",
    DYLD_FRAMEWORK_PATH: "/stale/framework",
    LD_LIBRARY_PATH: "/stale/linux/lib",
  };
  clearCliOverrides(env);
  assert.deepEqual(env, {
    HOME: "/real/home",
    INSPECT_CJ_CONFIG: "/isolated/config.json",
    PATH: "/usr/bin",
  });
});
