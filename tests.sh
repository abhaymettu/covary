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
g "filter signature is flagged"           "disjoint" PREGNANT PROSTATE --dataset brfss --min 0
g "high --min points at the real answer"  "below your threshold" numgiven socfrend --dataset gss --min 99999
g "case correction is announced"          "reading NUMGIVEN as numgiven" NUMGIVEN socfrend --dataset gss
g "suggests near names"                   "socfrend" socfriend --dataset gss
g "brfss rolls up rather than listing all" "of 52 states" GUNLOAD ACEDEPRS --dataset brfss

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
