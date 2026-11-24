# tests/run.sh

Component test runner for the flat `tests/` tree. It selects; it never moves a file.

Every test file is named `tests/test_<component>[_<detail>].py`. The component is the
first underscore-separated token of the stem, so `test_mission_seam.py` belongs to
`mission` and `test_missionstats_refueling.py` belongs to `missionstats`. A component owns
`test_<component>.py` plus `test_<component>_*.py` and nothing else — that is why
`mission` does not pick up `missionstats`, `python` does not pick up `python_version`,
`service` does not pick up `servicebus` and `config` does not pick up `configwrite`.
A new file is picked up by its name alone; no list in this script has to change.

The runner resolves the repo root from its own location, so it can be called from any
directory, and it prints every pytest command before running it.

## Subcommands

| command | what it does |
| --- | --- |
| `tests/run.sh <component>` | runs the component's files (glob), extra arguments are passed to pytest: `tests/run.sh mission -x -k seam` |
| `tests/run.sh list` | component → file-count table, derived by globbing at run time |
| `tests/run.sh all` | the whole suite; the `-n 12` default comes from `tests/pytest.ini` |
| `tests/run.sh changed` | `pytest tests/ --testmon --rootdir=<repo root>` — only what a source edit can break |
| `tests/run.sh cov [component]` | coverage over `services plugins core extensions`, per-area table, floor check |
| `tests/run.sh cov --update` | rewrites `tests/coverage-baseline.json` from a fresh measurement |

`tests/run.sh <component>` also accepts a deeper prefix that is not a component, e.g.
`tests/run.sh scheduler_launch` runs `test_scheduler_launch_failure.py` and
`test_scheduler_launched_preset.py`. An argument that matches no file prints the known
components and exits 2 — the runner never passes an empty selection to pytest.

Exit codes: `0` ok, `1` test failure or a coverage-floor breach, `2` usage error.

## `changed` — the inner loop

`changed` compares the source files' mtimes and hashes against `.testmondata` at the repo
root and executes only the tests whose dependency set they touch. That database is written
by testmon itself, which only records under `--testmon`:

- the **first** `changed` run has no database, so it executes the whole suite once and
  seeds it (the runner says so before starting);
- every later run is seconds on a normal one-area edit;
- the map is keyed to the interpreter and to `tests/conftest.py`, so a conftest change or
  a different interpreter invalidates it and the next `changed` run is a full one again.

## `cov` — the floor

`cov` runs the suite with `pytest-cov` over the four product trees and prints statements,
covered lines and percent per area plus `overall`, next to the baseline value and the
delta. It exits non-zero when any area (or `overall`) is more than **0.5 percentage
points** below its baseline. Coverage data goes to a temp directory, so the repo is not
littered with `.coverage*` files.

- The floor only applies to the whole-suite run. `tests/run.sh cov mission` prints the
  table for that subset and skips the comparison: a subset covers less by construction, so
  comparing it to the whole-suite baseline would fail every time.
- `cov` passes `-p no:randomly`. Statement totals and percentages are reproducible run to
  run; the covered-line count itself jitters by a handful of lines between runs (a few
  tests reach a different branch depending on how the files land on the workers), which is
  far inside the 0.5-point tolerance of every area.
- `cov --update` measures the whole suite and rewrites `tests/coverage-baseline.json`. Do
  that when the drop is intended, and review the diff of the JSON like any other change.
  `--update` refuses a component argument (exit 2): a subset would replace the whole-suite
  baseline with its own, far lower numbers.
- Statement counts in the baseline change whenever product code changes; the gate compares
  percentages only.

## What a run costs

`tests/run.sh mission` is 12 files / 261 tests in ~8 seconds. `all` and `cov` are the whole
suite: ~5 minutes for `all`, ~6 minutes for `cov` on 12 workers. `changed` is seconds once
`.testmondata` exists; the first run after the database is missing or invalidated is a full
suite run that also records the map (~9 minutes).
