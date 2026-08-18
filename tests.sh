#!/bin/bash
# Regression tests for covary. No framework, run it directly:  ./tests.sh
#
# Every case here is a bug that shipped at least once. Exit codes are the
# contract: 0 usable, 1 not usable, 2 name not found.

cd "$(dirname "$0")" || exit 1
PY=python3
pass=0; fail=0

t() {  # t <expected-exit> <description> <args...>
  want=$1; desc=$2; shift 2
  $PY covary.py "$@" > /tmp/covary_t.out 2>&1
  got=$?
  if [ "$got" = "$want" ]; then pass=$((pass+1)); printf '  ok   %s\n' "$desc"
  else fail=$((fail+1)); printf '  FAIL %s (want exit %s, got %s)\n' "$desc" "$want" "$got"
       sed 's/^/       /' /tmp/covary_t.out | head -5; fi
}

g() {  # g <description> <pattern> <args...>  output must contain pattern
  desc=$1; pat=$2; shift 2
  if $PY covary.py "$@" 2>&1 | grep -q -- "$pat"; then
    pass=$((pass+1)); printf '  ok   %s\n' "$desc"
  else fail=$((fail+1)); printf '  FAIL %s (missing %q)\n' "$desc" "$pat"; fi
}

echo "exit code contract"
t 0 "gss split ballot, usable years exist"        numgiven socfrend --dataset gss
t 1 "gss never co-administered"                   big5a1 numgiven --dataset gss
t 0 "gss three variables with a threshold"        numgiven talkto1 friend1 --dataset gss --min 300
t 0 "nhanes fasting subsample"                    RIAGENDR BMXBMI LBXGLU --dataset nhanes
t 0 "nhanes dietary is indexed"                    DR1TKCAL BMXBMI LBXGLU --dataset nhanes
t 1 "nhanes disjoint cycles"                      SDJ1REPN ALQ121 --dataset nhanes
t 0 "brfss state optional modules"                GUNLOAD ACEDEPRS --dataset brfss
t 2 "unknown name is not a dead design"           nosuchvariable --dataset gss
t 1 "cross-dataset set can never be joint"        numgiven BMXBMI

echo "bugs that shipped once"
t 0 "repeated name must not force a false NONE"   happy socfrend happy --dataset gss
t 1 "--min 0 must not pass a joint n of zero"     PREGNANT PROSTATE --dataset brfss --min 0
t 1 "high --min is not a claim about the world"   numgiven socfrend --dataset gss --min 99999
t 0 "case is corrected, not rejected"             NUMGIVEN socfrend --dataset gss

echo "output contract"
g "known joint n reproduces (gss 1985)"   "n=1526"  numgiven socfrend --dataset gss
g "known joint n reproduces (gss 2024)"   "n=711"   numgiven socfrend --dataset gss
g "no-overlap pair is reported"           "no respondent in common" PREGNANT PROSTATE --dataset brfss --min 0
g "high --min points at the real answer"  "below your threshold" numgiven socfrend --dataset gss --min 99999
g "case correction is announced"          "reading NUMGIVEN as numgiven" NUMGIVEN socfrend --dataset gss
g "suggests near names"                   "socfrend" socfriend --dataset gss
g "brfss rolls up rather than listing all" "of 52 states" GUNLOAD ACEDEPRS --dataset brfss

echo "tier 3 features"
t 0 "--find with a hit"                           --find gunload --dataset brfss
t 2 "--find with no hit"                          --find zzzznope
t 1 "per-stratum --min drops a poolable year"     GUNLOAD ACEDEPRS --dataset brfss --min 2000
t 0 "--min-year keeps it, states pooled"          GUNLOAD ACEDEPRS --dataset brfss --min-year 2000
g "leave-one-out names the culprit"       "without big5a1" big5a1 numgiven socfrend --dataset gss
g "leave-one-out gives the reachable n"   "gss 1985 n=1526" big5a1 numgiven socfrend --dataset gss
g "absence is attributed per year"        "2021         5 strata never collected ACEDEPRS" \
  GUNLOAD ACEDEPRS --dataset brfss --why
g "mechanism is not asserted"             "codebook decides" PREGNANT PROSTATE --dataset brfss --min 0
g "truncation is announced with its escape" "CLI: --all" big5a1 numgiven socfrend --dataset gss
g "--all defeats truncation"              "1972" big5a1 numgiven socfrend --dataset gss --all

if python3 covary.py PREGNANT PROSTATE --dataset brfss --json | python3 -c '
import json,sys; d=json.load(sys.stdin)
assert d["ok"] is False and d["usable"] == []
assert d["leave_one_out"][0]["drop"] == "PROSTATE"
assert d["collected_but_no_overlap"]["disjoint"] == 1
' 2>/dev/null; then
  pass=$((pass+1)); echo "  ok   --json parses and carries the verdict"
else fail=$((fail+1)); echo "  FAIL --json"; fi

echo "round two bugs"
t 0 "case-variant duplicate must not force a false NONE"  happy HAPPY socfrend --dataset gss
t 2 "case-ambiguous name refuses to guess a dataset"      Sex Marital
t 0 "exact spelling beats case folding"                   sex marital --dataset gss
t 1 "leave-one-out must not promise below --min"          numgiven socfrend --dataset gss --min 5000
g "says so when no single drop helps"  "no single variable is responsible" numgiven socfrend --dataset gss --min 5000
g "leave-one-out header names the threshold" "at min 1" lonely1 socfrend health --dataset gss
g "gss 2004 split ballot is reachable from a command" "2004" numgiven socfrend --dataset gss --min 0
g "disjointness is reported, not diagnosed" "no respondent in common" numgiven socfrend --dataset gss --min 0
g "zero count does not contradict the line above it" "co-occur below your threshold" DR1TKCAL DPQ010 --dataset nhanes --min 99999

# the split ballot the README opens with must never be called a skip pattern
# The founding example is a split ballot. The tool must never assert a mechanism
# for it, and must never claim the questions were administered together.
if python3 covary.py numgiven agape1 --dataset gss --min 0 2>&1 \
     | grep -qiE "WERE administered|signature of a skip pattern|likely a skip pattern"; then
  fail=$((fail+1)); echo "  FAIL a split ballot is described as a skip pattern"
else pass=$((pass+1)); echo "  ok   no mechanism asserted for a split ballot"; fi

echo "nhanes pooled strata"
t 0 "pooled-file variable joins its own cycle"  SSALB RIAGENDR --dataset nhanes
g "pooled span no longer appears as a stratum"  "1999-2000" SSALB --dataset nhanes
# 1999-2004 and 2007-2012 are the same respondents as their component cycles, so
# they must not survive as strata of their own
if python3 covary.py SSALB --dataset nhanes 2>&1 | grep -qE "1999-2004|2007-2012"; then
  fail=$((fail+1)); echo "  FAIL a pooled span is still its own stratum"
else pass=$((pass+1)); echo "  ok   pooled spans dissolved into their cycles"; fi

g "warns when strata share people under different ids" "same" RIAGENDR BMXBMI --dataset nhanes

# A variable published in both a per-cycle and a pooled NHANES table used to land
# twice after rehoming, inflating pop above the popcount. Cheap proxy for it here;
# pack.py --verify does the exhaustive version.
if python3 - <<'PY' 2>/dev/null; then pass=$((pass+1)); echo "  ok   no duplicate keys in the packed index"
import sqlite3, glob, os, sys
for f in glob.glob(os.path.join(os.path.dirname("."), "covary_*.db")):
    db = sqlite3.connect(f)
    n = db.execute("select count(*) - count(distinct stratum||char(1)||variable) from bm").fetchone()[0]
    if n: sys.exit(1)
PY
else fail=$((fail+1)); echo "  FAIL duplicate (stratum, variable) keys in the packed index"; fi

echo "mcp server contract"
m() {  # m <description> <pattern> <json>
  desc=$1; pat=$2; json=$3
  if printf '%s\n' "$json" | $PY mcp_server.py 2>&1 | grep -q -- "$pat"; then
    pass=$((pass+1)); printf '  ok   %s\n' "$desc"
  else fail=$((fail+1)); printf '  FAIL %s (missing %q)\n' "$desc" "$pat"; fi
}
m "no arguments is a protocol error, not a verdict" '"code": -32602' \
  '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"check_covariation","arguments":{}}}'
m "bad dataset is rejected, server survives" '"code": -32602' \
  '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"check_covariation","arguments":{"variables":["numgiven"],"dataset":"BRFSS"}}}'
m "batch request does not crash" '"code": -32600' '[{"jsonrpc":"2.0","id":1,"method":"tools/list"}]'
m "non-object params does not kill the process" '"code": -32602' \
  '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":"x"}'
m "over-long variable name is rejected" '"code": -32602' \
  '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"check_covariation","arguments":{"variables":["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]}}}'
m "real query still answers" 'VA' \
  '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"check_covariation","arguments":{"variables":["GUNLOAD","ACEDEPRS"],"dataset":"brfss"}}}'

# the server must answer request 2 after a bad request 1
if printf '%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"check_covariation","arguments":{"variables":["x"],"dataset":"NOPE"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | $PY mcp_server.py | grep -q '"id": 2'; then
  pass=$((pass+1)); echo "  ok   server survives a bad call and answers the next"
else fail=$((fail+1)); echo "  FAIL server died on a bad call"; fi

rm -f /tmp/covary_t.out
echo
echo "$pass passed, $fail failed"
[ "$fail" = 0 ]
