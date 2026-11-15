/* Parses JavaScript source with node's own parser.
 *
 * Usage: node inline_script_parse_harness.js <file> [<file> ...]
 *
 * Reads every path given on the command line and tries to parse it as a script. Prints ONE line of
 * JSON: an array of {file, ok, error?}. It never exits non-zero for a bad input — the caller reads
 * the verdict — so a syntax error in the code under test cannot be mistaken for a harness crash.
 *
 * ``vm.Script`` compiles the source the way a ``<script>`` block is compiled: an early syntax error
 * throws at construction, which is what a browser would report (and silently discard) on the page.
 */
"use strict";

const fs = require("fs");
const vm = require("vm");

const results = process.argv.slice(2).map(function (path) {
  const source = fs.readFileSync(path, "utf8");
  try {
    new vm.Script(source, { filename: path });
    return { file: path, ok: true };
  } catch (error) {
    return { file: path, ok: false, error: error.name + ": " + error.message };
  }
});

process.stdout.write(JSON.stringify(results));