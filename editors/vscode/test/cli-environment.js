"use strict";
// Code may restore login-shell variables after launch. Clear only the test host's
// inherited SDK overrides so its CLI children exercise the isolated user config.
function clearCliOverrides(env) {
  for (const key of [
    "CJC",
    "CJDB",
    "CJPM",
    "CANGJIE_HOME",
    "INSPECT_CJ_SYSROOT",
    "CJDB_PYTHON_FRAMEWORK",
    "DYLD_LIBRARY_PATH",
    "DYLD_FRAMEWORK_PATH",
    "LD_LIBRARY_PATH",
  ])
    delete env[key];
}
module.exports = { clearCliOverrides };
