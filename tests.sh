#!/bin/bash
# Regression tests for covary. No framework, run it directly:  ./tests.sh
#
# Every case here is a bug that shipped at least once. Exit codes are the
# contract: 0 usable, 1 no stratum has them all, 2 unanswerable as asked.

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
t 2 "cross-dataset set is unanswerable, not dead"  numgiven BMXBMI

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
g "finds a transposition, not just a prefix" "DR1TKCAL" DR1TKCLA --dataset nhanes
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
g "truncation is announced with its escape" "more; --why" big5a1 numgiven socfrend --dataset gss
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

echo "round three bugs"
# every one of these is a caveat or threshold that reached one interface and not
# its sibling. The pattern, three rounds running, was fixing the path a report
# named rather than the function both paths call.
g "min-year is named in the header"        "pooled min" numgiven socfrend --dataset gss --min-year 2000
t 1 "min-year excludes without lying"      numgiven socfrend happy --min-year 5000 --dataset gss
g "min-year advice names the right flag"   "min-year" numgiven socfrend happy --min-year 5000 --dataset gss
g "scattered years are listed, not ranged" "1975, 1980" away9 numgiven --dataset gss
g "double-counted marginal is footnoted"   "twice" RIAGENDR BMXBMI --dataset nhanes
g "overlapping strata are warned about"    "the same people" RIAGENDR BMXBMI --dataset nhanes

# leave-one-out must never offer n=0 as reachable
if python3 covary.py numgiven socfrend lonely1 --dataset gss --min 0 2>&1 \
     | grep -qE "without .* n=0"; then
  fail=$((fail+1)); echo "  FAIL leave-one-out offers a joint n of 0 as reachable"
else pass=$((pass+1)); echo "  ok   leave-one-out never offers n=0"; fi

# --min 0 must carry zero strata into json, not only into the text
if python3 covary.py numgiven socfrend --dataset gss --min 0 --json | python3 -c '
import json,sys; d=json.load(sys.stdin)
assert any(z["stratum"] == "2004" for z in d["zero"]), "gss 2004 missing from json"
' 2>/dev/null; then pass=$((pass+1)); echo "  ok   --min 0 --json carries the zero strata"
else fail=$((fail+1)); echo "  FAIL --min 0 --json drops the zero strata"; fi

# the agent interface must receive the same caveats as the CLI
if printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"check_covariation","arguments":{"variables":["RIAGENDR","BMXBMI"],"dataset":"nhanes"}}}' \
   | python3 mcp_server.py | grep -q "the same people"; then
  pass=$((pass+1)); echo "  ok   mcp gets the overlap warning too"
else fail=$((fail+1)); echo "  FAIL mcp is missing the overlap warning"; fi

# --why must show strictly more than the default. The old test grepped for a
# substring the default output also contains, so it passed on a literal no-op.
if [ "$(python3 covary.py numgiven socfrend --dataset gss --why 2>&1 | grep -c 'never collected')" \
     -gt "$(python3 covary.py numgiven socfrend --dataset gss 2>&1 | grep -c 'never collected')" ]; then
  pass=$((pass+1)); echo "  ok   --why shows strictly more than the default"
else fail=$((fail+1)); echo "  FAIL --why shows no more than the default"; fi

# No hint may name a state the caller already set. Three separate hints did:
# "Run with --min 0" on a --min 0 run, "--why for all of them" on a --why run,
# and the MCP equivalent, which is a loop instruction to an autonomous agent.
if python3 covary.py PREGNANT PROSTATE --dataset brfss --min 0 2>&1 | grep -qi "run with --min 0"; then
  fail=$((fail+1)); echo "  FAIL hint says 'run with --min 0' to a caller who passed it"
else pass=$((pass+1)); echo "  ok   no --min 0 hint when --min 0 was passed"; fi

if python3 covary.py numgiven socfrend --dataset gss --why 2>&1 | grep -q "more; --why"; then
  fail=$((fail+1)); echo "  FAIL hint says '--why' to a caller who passed --why"
else pass=$((pass+1)); echo "  ok   no --why hint when --why was passed"; fi

if printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"check_covariation","arguments":{"variables":["PREGNANT","PROSTATE"],"dataset":"brfss","min_n":0}}}' \
   | python3 mcp_server.py | grep -q "call again with min_n 0"; then
  fail=$((fail+1)); echo "  FAIL mcp tells an agent to call again with the arguments it just used"
else pass=$((pass+1)); echo "  ok   mcp does not loop an agent back on its own arguments"; fi
g "readme opening example reproduces"      "1985, 1987, 2004, 2024" numgiven socfrend

# a file may hold many strata, but a stratum must not span files, or the cheap
# per-file duplicate check cannot see the duplicate
if .venv/bin/python pack.py --verify 2>&1 | grep -q "spans"; then
  fail=$((fail+1)); echo "  FAIL a stratum spans more than one index file"
else pass=$((pass+1)); echo "  ok   no stratum spans more than one index file"; fi

echo "nhanes pooled strata"
t 0 "pooled-file variable joins its own cycle"  SSALB RIAGENDR --dataset nhanes
g "pooled span no longer appears as a stratum"  "1999-2000" SSALB --dataset nhanes
# 1999-2004 and 2007-2012 are the same respondents as their component cycles, so
# they must not survive as strata of their own
if python3 covary.py SSALB --dataset nhanes 2>&1 | grep -qE "1999-2004|2007-2012"; then
  fail=$((fail+1)); echo "  FAIL a pooled span is still its own stratum"
else pass=$((pass+1)); echo "  ok   pooled spans dissolved into their cycles"; fi



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

# The README transcript drifted from real output twice, in a repo whose whole
# subject is numbers drifting from their source. Assert it instead of proofreading it.
if python3 - <<'PY' 2>/dev/null; then pass=$((pass+1)); echo "  ok   README transcript matches real output"
import subprocess
r = open("README.md").read()
k = "$ python3 covary.py PREGNANT PROSTATE --dataset brfss"
blk = r[r.index(k):]
blk = blk[:blk.index("```")].split("\n", 1)[1].rstrip()
real = subprocess.run(["python3", "covary.py", "PREGNANT", "PROSTATE",
                       "--dataset", "brfss"], capture_output=True, text=True).stdout.rstrip()
assert blk == real
PY
else fail=$((fail+1)); echo "  FAIL README transcript no longer matches real output"; fi

# A name absent from this index is not a name absent from the survey. HSSEX is a
# real NHANES III variable and this index starts in 1999; the tool said "not
# found" and never mentioned it had a bound, while coverage sat in the payload
# and printed on a sibling branch.
g "not_found states the index bound"  "this index covers" HSSEX --dataset nhanes
t 2 "a bad --dataset is unanswerable, not a verdict"  numgiven --dataset nope
t 2 "an unknown name is unanswerable"                 numgivn --dataset gss

t 2 "a negative threshold is rejected, not echoed"  PREGNANT PROSTATE --dataset brfss --min -1

echo "payload, not prose"
# Four review rounds found the same defect under four names: a fact in the text
# and not in the structure. These assert the payload, because asserting the
# string is what let every one of them through.
j() {  # j <description> <python-assert-body> <args...>
  desc=$1; body=$2; shift 2
  if python3 covary.py "$@" --json 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
$body" 2>/dev/null; then pass=$((pass+1)); printf '  ok   %s\n' "$desc"
  else fail=$((fail+1)); printf '  FAIL %s\n' "$desc"; fi
}
j "recoverable design says so in the payload" \
  'assert d["reason"]=="below_threshold" and d["best_below_threshold"]["n"]>0' \
  RIAGENDR LBXGLU --dataset nhanes --min 999999
j "impossible design is a different reason" \
  'assert d["reason"]=="never_together" and d["best_below_threshold"] is None' \
  PROSTATE SSBSUGR2 --dataset brfss
j "ambiguous names emit a payload, not silence" 'assert d["reason"]=="ambiguous"' Sex Marital
j "unknown name payload carries a reason" \
  'assert d["reason"]=="not_found" and d["suggestions"]' numgivn
j "cross-dataset is its own reason" 'assert d["reason"]=="cross_dataset"' numgiven BMXBMI
j "mode note is in the payload" \
  'assert any(isinstance(n,dict) and n["kind"]=="gss_mode" for n in d["notes"])' \
  happy socfrend --dataset gss
j "overlap warning is in the payload" \
  'assert any(w["kind"]=="same_people_different_ids" for w in d["warnings"])' \
  RIAGENDR BMXBMI --dataset nhanes
j "declared exit matches the contract" 'assert d["exit"]==1' numgiven socfrend --dataset gss --min 99999

# The property, not its name: every decision-relevant fact in the payload must
# reach the text. The signature check below is kept as a cheap guard, but it is
# what passed while "4 of 52 states" lived only in the prose.
if python3 check_completeness.py > /tmp/covary_comp.out 2>&1; then
  pass=$((pass+1)); echo "  ok   every payload fact reaches the reader"
else fail=$((fail+1)); echo "  FAIL payload facts never reach the reader"; sed 's/^/       /' /tmp/covary_comp.out | tail -3; fi

# render() must not be able to reach the database: if it cannot, it cannot state
# a fact the payload lacks. This is the structural guarantee, not a spot check.
if python3 -c "
import inspect, covary
src = inspect.getsource(covary.render)
assert 'db.' not in src and 'db,' not in src, 'render() touches a database handle'
assert 'db' not in inspect.signature(covary.render).parameters
" 2>/dev/null; then pass=$((pass+1)); echo "  ok   render() cannot reach the data, only the payload"
else fail=$((fail+1)); echo "  FAIL render() can reach the data and bypass the payload"; fi

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
# An unknown name is the most likely thing an agent does with this tool, and the
# whole not-found branch was dead over MCP for a round because all_names was not
# imported. 65 tests passed with seven MCP cases and none sent a bad name.
m "unknown name over mcp suggests a real one" 'did you mean' \
  '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"check_covariation","arguments":{"variables":["numgivn"]}}}'
m "unknown name over mcp is an error, not a verdict" '"isError": true' \
  '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"check_covariation","arguments":{"variables":["numgivn"]}}}'
m "padded names are stripped, not rejected" 'numgiven' \
  '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"check_covariation","arguments":{"variables":["  numgiven  ","socfrend"],"dataset":"gss"}}}'

# no CLI flag may appear in an agent-facing reply. Three rounds running, one
# leaked from a location the previous scrub did not know about.
if printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"check_covariation","arguments":{"variables":["FIREARM5","ACEDEPRS"],"dataset":"brfss","min_n":0}}}' \
   | python3 mcp_server.py | grep -qE '\-\-why|\-\-all|\-\-min|\-\-dataset'; then
  fail=$((fail+1)); echo "  FAIL a CLI flag leaked into an agent reply"
else pass=$((pass+1)); echo "  ok   no CLI flag leaks into an agent reply"; fi

m "real query still answers" 'VA' \
  '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"check_covariation","arguments":{"variables":["GUNLOAD","ACEDEPRS"],"dataset":"brfss"}}}'

# the server must answer request 2 after a bad request 1
if printf '%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"check_covariation","arguments":{"variables":["x"],"dataset":"NOPE"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | $PY mcp_server.py | grep -q '"id": 2'; then
  pass=$((pass+1)); echo "  ok   server survives a bad call and answers the next"
else fail=$((fail+1)); echo "  FAIL server died on a bad call"; fi


echo "text layer"
# The index is the product and the text is an enhancement, so every case above
# must still pass with labels.db missing. Checked by moving it, not by trusting
# the try/except: an exception swallowed in labels_db() would look identical.
if [ -f labels.db ]; then
  mv labels.db labels.db.hidden
  if $PY covary.py numgiven socfrend --dataset gss 2>&1 | grep -q "n=1526"; then
    pass=$((pass+1)); echo "  ok   index answers unchanged with no labels.db"
  else fail=$((fail+1)); echo "  FAIL a missing labels.db broke the index"; fi
  $PY covary.py --search anything --dataset gss > /dev/null 2>&1
  if [ $? = 2 ]; then pass=$((pass+1)); echo "  ok   --search without labels.db exits 2"
  else fail=$((fail+1)); echo "  FAIL --search without labels.db did not exit 2"; fi
  mv labels.db.hidden labels.db
else
  echo "  skip labels.db absent, build it: Rscript build_labels.R && $PY pack_labels.py"
fi

if [ -f labels.db ]; then
  t 0 "--search finds a name from a phrase"   --search "spend evening with friends" --dataset gss
  t 2 "--search with no hit exits 2"          --search "zzzqqqxxnotaword" --dataset gss
  g "label prints under the variable"  "number of persons mentioned"  numgiven --dataset gss
  # The wrong-two-variables failure the README confesses. These three are
  # different questions and the index alone cannot tell them apart.
  g "firearm storage is distinguishable"  "LOADULK2"  --search "firearm stored loaded" --dataset brfss
  g "search is restricted to indexed names"  "socfrend"  --search "spend evening with friends" --dataset gss
  # An operator character used to make FTS5 raise rather than return nothing.
  t 0 "punctuation in a query is not a crash"  --search "cost of care - out of pocket??" --dataset gss
  g "json carries the label"  '"description"'  numgiven --dataset gss --json

  # The two co-administration transcripts have been checked against real output
  # since they first drifted. The search transcript was written by hand in the
  # same edit that added this section and was wrong on the first try, so it gets
  # the same guard rather than the same trust.
  if $PY - <<'EOF'
import subprocess, sys
r = open("README.md").read()
k = '$ python3 covary.py --search "how often do you see friends" --dataset gss'
blk = r[r.index(k):]
blk = blk[:blk.index("```")].split("\n", 1)[1].rstrip()
out = subprocess.run(["python3", "covary.py", "--search",
                      "how often do you see friends", "--dataset", "gss"],
                     capture_output=True, text=True).stdout
real = "\n".join(out.rstrip().split("\n")[:len(blk.split("\n"))])
sys.exit(0 if blk == real else 1)
EOF
  then pass=$((pass+1)); echo "  ok   README search transcript matches real output"
  else fail=$((fail+1)); echo "  FAIL README search transcript no longer matches"; fi

  # Coverage floor. A partially fetched source builds a labels.db that answers
  # some searches and silently misses whole regions of a survey, which no
  # single-query test can see.
  if $PY - <<'EOF'
import sqlite3, glob, os, sys
L = sqlite3.connect("file:labels.db?mode=ro", uri=True)
floors = {"gss": 90, "nhanes": 90, "brfss": 60}
bad = []
for f in sorted(glob.glob("covary_*.db")):
    ds = os.path.basename(f)[7:-3]
    idx = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
    names = {v for (v,) in idx.execute("select distinct variable from bm")}
    have = {v for (v,) in L.execute("select variable from labels where dataset=?", (ds,))}
    pct = 100.0 * len(names & have) / len(names)
    if pct < floors.get(ds, 0):
        bad.append(f"{ds} {pct:.1f}% < {floors[ds]}%")
print("; ".join(bad), file=sys.stderr)
sys.exit(1 if bad else 0)
EOF
  then pass=$((pass+1)); echo "  ok   label coverage is above the floor for every dataset"
  else fail=$((fail+1)); echo "  FAIL label coverage fell below the floor"; fi

  m "search_variables answers a plain-English query" 'GUNLOAD' \
    '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search_variables","arguments":{"query":"firearm storage in the home","dataset":"brfss"}}}'
  m "search_variables rejects an empty query" 'query is required' \
    '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search_variables","arguments":{"query":"  "}}}'
  m "a no-hit search must not read as absence" 'NOT report' \
    '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search_variables","arguments":{"query":"zzzqqqxxnotaword"}}}'
fi

rm -f /tmp/covary_t.out
echo
echo "$pass passed, $fail failed"
[ "$fail" = 0 ]
