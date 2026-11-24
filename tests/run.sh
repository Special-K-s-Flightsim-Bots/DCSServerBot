#!/bin/sh
# Component test runner for the flat tests/ tree.
#
# Every test file is tests/test_<component>[_<detail>].py. The component is the first
# underscore-separated token of the file stem: test_mission_seam.py is `mission`,
# test_missionstats_refueling.py is `missionstats`. A component therefore owns the files
# test_<component>.py and test_<component>_*.py and nothing else, so no component can
# swallow another (mission/missionstats, python/python_version, service/servicebus,
# config/configwrite).
#
# The subcommands are documented in tests/README-runner.md.
#
# Usage:
#   tests/run.sh <component> [pytest args...]
#   tests/run.sh list
#   tests/run.sh all [pytest args...]
#   tests/run.sh changed
#   tests/run.sh cov [component|--update]
#
# Exit codes: 0 ok, 1 test failure or coverage-floor drop, 2 usage error (unknown
# component / unknown option).

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
BASELINE="$SCRIPT_DIR/coverage-baseline.json"

# The venv carries pytest, pytest-xdist, pytest-cov and pytest-testmon.
if [ -x "$REPO_ROOT/venv/bin/python" ]; then
    PY="$REPO_ROOT/venv/bin/python"
else
    PY="python3"
fi

# --- component discovery -----------------------------------------------------

# Every component name in the tree, sorted, derived by globbing at run time.
component_names() {
    for f in "$SCRIPT_DIR"/test_*.py; do
        b=${f##*/}
        b=${b#test_}
        b=${b%.py}
        printf '%s\n' "${b%%_*}"
    done | LC_ALL=C sort -u
}

# Files owned by one component: test_<c>.py and test_<c>_*.py, sorted.
component_files() {
    for f in "$SCRIPT_DIR/test_$1.py" "$SCRIPT_DIR"/test_"$1"_*.py; do
        if [ -f "$f" ]; then
            printf '%s\n' "$f"
        fi
    done | LC_ALL=C sort
}

# Files for a component, or for a deeper prefix such as `scheduler_launch`. Empty when
# nothing matches.
resolve_target() {
    found=$(component_files "$1")
    if [ -z "$found" ]; then
        for f in "$SCRIPT_DIR"/test_"$1"*.py; do
            if [ -f "$f" ]; then
                printf '%s\n' "$f"
            fi
        done | LC_ALL=C sort
    else
        printf '%s\n' "$found"
    fi
}

die_unknown() {
    printf 'run.sh: unknown component %s\n\n' "'$1'" >&2
    printf 'known components:\n' >&2
    component_names | tr '\n' ' ' >&2
    printf '\n\n' >&2
    printf 'run %s for the component -> file-count table.\n' "tests/run.sh list" >&2
    exit 2
}

# --- pytest invocation -------------------------------------------------------

# Runs pytest from the repo root and echoes the command first. File arguments are passed
# as a newline-separated string so a 300-file component does not need a giant argv on the
# echo line.
run_pytest() {
    files=$1
    shift
    count=$(printf '%s\n' "$files" | wc -l | tr -d ' ')
    if [ "$count" -le 6 ]; then
        shown=$(printf '%s' "$files" | tr '\n' ' ')
    else
        shown=$(printf '%s\n' "$files" | sed -n '1p')" ... ($count files)"
    fi
    printf '+ (cd %s && %s -m pytest %s %s)\n' "$REPO_ROOT" "$PY" "$shown" "$*"
    # shellcheck disable=SC2086
    (cd "$REPO_ROOT" && exec "$PY" -m pytest $files "$@")
}

# --- subcommands -------------------------------------------------------------

cmd_list() {
    printf '%-22s %6s\n' "COMPONENT" "FILES"
    printf '%-22s %6s\n' "----------------------" "-----"
    for c in $(component_names); do
        n=$(component_files "$c" | wc -l | tr -d ' ')
        printf '%s %s\n' "$n" "$c"
    done | LC_ALL=C sort -rn -k1,1 | while read -r n c; do
        printf '%-22s %6s\n' "$c" "$n"
    done
    printf '%-22s %6s\n' "----------------------" "-----"
    printf '%-22s %6s\n' "total" "$(ls "$SCRIPT_DIR"/test_*.py | wc -l | tr -d ' ')"
}

cmd_component() {
    component=$1
    shift
    files=$(resolve_target "$component")
    if [ -z "$files" ]; then
        die_unknown "$component"
    fi
    n=$(printf '%s\n' "$files" | wc -l | tr -d ' ')
    printf 'component %s: %s file(s)\n' "$component" "$n"
    run_pytest "$files" "$@"
}

cmd_all() {
    printf 'the whole suite; the -n 12 parallel default comes from tests/pytest.ini\n'
    printf '+ (cd %s && %s -m pytest tests/ %s)\n' "$REPO_ROOT" "$PY" "$*"
    (cd "$REPO_ROOT" && exec "$PY" -m pytest tests/ "$@")
}

cmd_changed() {
    if [ ! -f "$REPO_ROOT/.testmondata" ]; then
        printf 'no .testmondata at the repo root: this run executes the whole suite and\n'
        printf 'records the test -> source map. Later runs only execute the affected tests.\n'
    fi
    printf '+ (cd %s && %s -m pytest tests/ --testmon --rootdir=%s)\n' \
        "$REPO_ROOT" "$PY" "$REPO_ROOT"
    (cd "$REPO_ROOT" && exec "$PY" -m pytest tests/ --testmon --rootdir="$REPO_ROOT")
}

cmd_cov() {
    update=0
    target="tests/"
    for arg in "$@"; do
        case $arg in
            --update) update=1 ;;
            -*) printf 'run.sh cov: unknown option %s\n' "'$arg'" >&2; exit 2 ;;
            *)
                files=$(resolve_target "$arg")
                if [ -z "$files" ]; then
                    die_unknown "$arg"
                fi
                target=$(printf '%s' "$files" | tr '\n' ' ')
                ;;
        esac
    done

    if [ "$update" = 1 ] && [ "$target" != "tests/" ]; then
        printf 'run.sh cov: --update rewrites the whole-suite baseline; it cannot be\n' >&2
        printf 'combined with a component selection (a subset would replace the baseline\n' >&2
        printf 'with its own, much lower numbers).\n' >&2
        exit 2
    fi

    tmpd=$(mktemp -d "${TMPDIR:-/tmp}/dcssb-cov.XXXXXX")
    trap 'rm -rf "$tmpd"' EXIT HUP INT TERM
    report="$tmpd/coverage.json"

    printf '+ (cd %s && COVERAGE_FILE=%s/.coverage %s -m pytest %s -p no:randomly \\\n' \
        "$REPO_ROOT" "$tmpd" "$PY" "$target"
    printf '     --cov=services --cov=plugins --cov=core --cov=extensions \\\n'
    printf '     --cov-report= --cov-report=json:%s)\n' "$report"

    rc=0
    set +e
    (cd "$REPO_ROOT" && COVERAGE_FILE="$tmpd/.coverage" exec "$PY" -m pytest $target -p no:randomly \
        --cov=services --cov=plugins --cov=core --cov=extensions \
        --cov-report= --cov-report=json:"$report")
    rc=$?
    set -e

    "$PY" - "$report" "$REPO_ROOT" "$BASELINE" "$update" "$rc" "$target" <<'PY'
import json
import os
import sys

report_path, repo_root, baseline_path, update, rc, target = (
    sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] == "1",
    int(sys.argv[5]), sys.argv[6].strip(),
)

AREAS = ["services", "plugins", "core", "extensions"]
TOLERANCE = 0.5  # percentage points


def pct(covered, statements):
    return round(100.0 * covered / statements, 1) if statements else 0.0


try:
    with open(report_path, encoding="utf-8") as fh:
        data = json.load(fh)
except OSError as exc:
    print(f"coverage report missing ({exc})")
    sys.exit(rc or 1)

tally = {area: [0, 0] for area in AREAS}
for fname, info in data["files"].items():
    if not os.path.isabs(fname):
        fname = os.path.join(repo_root, fname)
    rel = os.path.relpath(fname, repo_root)
    if rel.startswith(".."):
        continue
    top = rel.replace(os.sep, "/").split("/")[0]
    if top in tally:
        summary = info["summary"]
        tally[top][0] += summary["num_statements"]
        tally[top][1] += summary["covered_lines"]

measured = {}
for area in AREAS:
    statements, covered = tally[area]
    measured[area] = {"statements": statements, "covered": covered,
                      "percent": pct(covered, statements)}
statements = sum(tally[a][0] for a in AREAS)
covered = sum(tally[a][1] for a in AREAS)
measured["overall"] = {"statements": statements, "covered": covered,
                       "percent": pct(covered, statements)}

whole_run = target in ("tests/", "")
rows = AREAS + ["overall"]

if update:
    if rc != 0:
        print("\ntests failed: the baseline was not rewritten", file=sys.stderr)
        sys.exit(rc)
    payload = {
        "area_source": AREAS,
        "tolerance_pp": TOLERANCE,
        "areas": {area: measured[area] for area in rows},
    }
    with open(baseline_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    print(f"\nbaseline rewritten: {baseline_path}")
    for area in rows:
        print(f"  {area:<12} {measured[area]['statements']:>7} stmts "
              f"{measured[area]['percent']:>6}%")
    sys.exit(rc)

try:
    with open(baseline_path, encoding="utf-8") as fh:
        baseline = json.load(fh)["areas"]
except OSError:
    if not whole_run:
        baseline = {}
    else:
        print(f"\nno baseline at {baseline_path}; run `tests/run.sh cov --update` first",
              file=sys.stderr)
        sys.exit(1)

# area left-aligned, numbers right-aligned, one field width per column
HEAD = ("area", "statements", "covered", "percent", "baseline", "delta")
WIDTH = (12, 12, 10, 9, 10, 8)
if not whole_run:
    HEAD = HEAD[:4]
    WIDTH = WIDTH[:4]


def table_line(cells):
    out = ""
    for index, (cell, width) in enumerate(zip(cells, WIDTH)):
        out += cell.ljust(width) if index == 0 else cell.rjust(width)
    return out.rstrip()


print()
print(table_line(HEAD))
print("-" * sum(WIDTH))
drops = []
for area in rows:
    got = measured[area]
    base = baseline.get(area)
    base_pct = base["percent"] if base else None
    cells = [area, str(got["statements"]), str(got["covered"]), f"{got['percent']:.1f}%"]
    if whole_run:
        cells.append("n/a" if base_pct is None else f"{base_pct:.1f}%")
        cells.append("" if base_pct is None else f"{got['percent'] - base_pct:+.1f}")
    print(table_line(cells))
    if not whole_run or base_pct is None:
        continue
    if got["percent"] < base_pct - TOLERANCE:
        drops.append(f"{area}: {got['percent']:.1f}% < {base_pct:.1f}% "
                     f"(floor {base_pct - TOLERANCE:.1f}%)")

print()
if not whole_run:
    sys.stdout.flush()
    print(f"subset run ({target.split()[0]} ...): coverage of a subset is not comparable "
          f"with the whole-suite baseline, floor not evaluated")
    sys.exit(rc)
if rc != 0:
    sys.stdout.flush()
    print("tests failed: coverage floor not evaluated", file=sys.stderr)
    sys.exit(rc)
if drops:
    sys.stdout.flush()
    print("coverage floor breached:", file=sys.stderr)
    for line in drops:
        print(f"  {line}", file=sys.stderr)
    print(f"tolerance is {TOLERANCE} percentage points per area "
          f"(tests/coverage-baseline.json)", file=sys.stderr)
    sys.exit(1)
print(f"coverage floor holds (tolerance {TOLERANCE} points per area)")
PY
}

# --- dispatch ----------------------------------------------------------------

if [ $# -eq 0 ]; then
    printf 'usage: tests/run.sh <component|list|all|changed|cov> [args...]\n' >&2
    printf 'components:\n' >&2
    component_names | tr '\n' ' ' >&2
    printf '\n' >&2
    exit 2
fi

cmd=$1
shift
case $cmd in
    list) cmd_list ;;
    all) cmd_all "$@" ;;
    changed) cmd_changed ;;
    cov) cmd_cov "$@" ;;
    -*) printf 'run.sh: unknown command %s\n' "'$cmd'" >&2; exit 2 ;;
    *) cmd_component "$cmd" "$@" ;;
esac
