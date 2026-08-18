#!/usr/bin/env python3
"""MCP server over the covary index. One tool: given variables, which strata?

The CLI is for humans. This is the interface that matters, because the failure
covary catches is one an agent walks into constantly: pick plausible variables,
write the analysis, discover afterwards that nothing measured them together.

Raw JSON-RPC on stdio rather than the mcp package, so the server keeps the same
property as covary.py, which is that it needs nothing installed.

  python3 mcp_server.py          # speaks MCP on stdin/stdout

Register it with:
  claude mcp add covary -- python3 /absolute/path/to/mcp_server.py
"""
import json, os, sqlite3, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from covary import (connect, marginals, joint, denominators, show_joint, resolve,
                    suggest, all_names, absences, show_absences, leave_one_out, DBS)
import glob

DATASETS = ("gss", "nhanes", "brfss")
MAX_VARS = 64


def validate(a):
    """Reject a malformed call BEFORE it can reach run().

    Without this, a call with no arguments at all returned the tool's most
    authoritative verdict, NOT IDENTIFIED, with isError false. A blind test had a
    model tell a researcher their design was not feasible off exactly that. A
    validation failure must never be answerable with a substantive verdict.
    """
    if not isinstance(a, dict):
        return "arguments must be an object"
    v = a.get("variables")
    if not isinstance(v, list) or not v:
        return "variables is required and must be a non-empty array of names"
    if not all(isinstance(x, str) and x.strip() for x in v):
        return "every entry in variables must be a non-empty string"
    if any(len(x) > 64 for x in v):
        # names are echoed back; an 8MB "name" produced an 8MB reply
        return "a variable name cannot exceed 64 characters"
    if len(v) > MAX_VARS:
        return f"at most {MAX_VARS} variables per call, got {len(v)}"
    d = a.get("dataset")
    if d is not None and d not in DATASETS:
        return f"dataset must be one of {', '.join(DATASETS)}, got {d!r}"
    m = a.get("min_n", 1)
    if not isinstance(m, int) or isinstance(m, bool) or m < 0:
        return "min_n must be a non-negative integer"
    return None

TOOL = {
    "name": "check_covariation",
    "description": (
        "Before designing an analysis on GSS, NHANES or BRFSS, check whether the "
        "variables it needs were actually administered to the SAME respondents. "
        "Two variables can each have n in the thousands in one year and a joint n "
        "of zero: GSS rotates modules across split ballots, NHANES splits across "
        "component files and subsamples, BRFSS optional modules are chosen state "
        "by state. Returns the strata (GSS year, NHANES cycle, BRFSS year|state) "
        "where every requested variable is non-missing on the same respondent, "
        "with the joint n. Presence means non-missing on the variable itself, so a "
        "respondent who skipped a module or an exam is correctly absent. An empty "
        "result means no respondent in this index has all of them, which usually "
        "means the design is not estimable as stated. Before telling a user their "
        "design is dead, check the returned notes: the questions may co-occur below "
        "the min_n you passed, may have been asked outside the indexed years, or may "
        "be separated by a skip pattern or a split ballot rather than absent."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "variables": {
                "type": "array", "items": {"type": "string"},
                "minItems": 1, "maxItems": 64,
                "description": "Variable names as they appear in the dataset, e.g. "
                               "['numgiven','socfrend'] or ['RIAGENDR','BMXBMI','LBXGLU']",
            },
            "dataset": {
                "type": "string", "enum": ["gss", "nhanes", "brfss"],
                "description": "Restrict to one dataset. A stratum belongs to one "
                               "dataset, so a set spanning datasets never has a joint n.",
            },
            "min_n": {
                "type": "integer", "default": 1,
                "description": "Only report strata with at least this joint n. "
                               "0 also reveals strata where every variable was "
                               "collected but no respondent has them all.",
            },
        },
        "required": ["variables"],
    },
}


def run(variables, dataset=None, min_n=1):
    if not glob.glob(DBS):
        return "No covary index found. Run pack.py to build it.", True

    variables = list(dict.fromkeys(variables))   # a repeat used to force a false NONE
    db = connect(dataset)
    variables, notes, ambiguous = resolve(db, variables)   # case, shared with the CLI
    if ambiguous:
        return "\n".join(
            [f"Ambiguous: {v!r} differs only by case from {', '.join(c)}, which are "
             f"in different datasets." for v, c in ambiguous]
            + ["Set `dataset`, or spell the name as that dataset spells it."]), True
    marg = marginals(db, variables, dataset)
    missing = [v for v in variables if v not in {r[0] for r in marg}]
    if missing:
        where = f"in dataset {dataset}" if dataset else "in any indexed dataset"
        out = [f"Not found {where}: {', '.join(missing)}."]
        if dataset:
            out.append("  A variable can be real but belong to another dataset. "
                       "Retry without `dataset` to check all of them.")
        names = all_names(db)      # one scan, however many names are unknown
        for m in missing:
            sg = suggest(db, m, names=names)
            if sg:
                out.append(f"  names starting like {m!r}: {', '.join(sg)}")
        return "\n".join(out), True

    lines = list(notes) + ["Per variable, ignoring co-administration:"]
    for v, ds, n, lo, hi, ns in marg:
        span = lo if lo == hi else f"{lo} .. {hi}"
        lines.append(f"  {v}  dataset={ds}  n={n}  {span}, {ns} strat{'um' if ns == 1 else 'a'}")

    datasets = {r[1] for r in marg}
    if len(datasets) > 1 and not dataset:
        lines.append(
            f"\nThese variables span {', '.join(sorted(datasets))}. A stratum belongs to one "
            "dataset, so this set can never have a joint n. Set `dataset` to one of them.")

    if len(datasets) > 1 and not dataset:
        # The span note above IS the answer. Printing NOT USABLE plus a
        # leave-one-out under it invites exactly the misread this tool exists
        # to prevent.
        return "\n".join(lines), False

    rows = joint(db, variables, dataset, min_n)
    usable = [r for r in rows if r[2] > 0]
    if not usable:
        near = joint(db, variables, dataset, 1)
        lines.append(
            f"\nNOT USABLE. No respondent is non-missing on all of these in any "
            f"stratum this index covers"
            + (f" at a joint n of {min_n} or more." if min_n > 1 else "."))
        if near:
            best = max(near, key=lambda r: r[2])
            lines.append(
                f"They DO co-occur below your threshold. Largest is {best[0]} "
                f"{best[1]} at n={best[2]}. Lower min_n rather than abandoning this.")
        else:
            lines.append(
                "Before concluding these were never asked together, check two things. "
                "This index covers gss 1972-2024, nhanes 1999-2023, brfss 2011-2023, "
                "so an earlier or later administration is invisible here. And a "
                "question skipped because of a filter, for example one only asked of "
                "respondents who answered yes earlier, is absent for that reason and "
                "not because it was left off the instrument. Call again with min_n 0 "
                "to see strata where all of these were collected but no respondent "
                "has them all.")
            zero = [r for r in joint(db, variables, dataset, 0) if r[3]]
            if zero:
                lines.append(
                    f"Note: {len(zero)} stratum or strata collected all of these and "
                    "contain a pair of variables with no respondent in common. A split "
                    "ballot and a skip pattern both look exactly like this and cannot "
                    "be told apart from presence alone, so the codebook decides which "
                    "it is. Do not report either mechanism to the user as established.")
        # What to give up, and where each stratum went. The decision on a NOT
        # USABLE is which variable to drop, not whether to give up, and an agent
        # that is only told "no" will report the design as dead.
        loo = leave_one_out(db, variables, dataset, min_n)
        if loo:
            lines.append(f"\nBest reachable by dropping one variable "
                         f"(at min_n={min_n}):")
            for d, ds, st, n in loo:
                lines.append(f"  without {d}: {ds} {st} n={n}")
        why = absences(db, variables, dataset, datasets)
        if why[0] or why[1]:
            lines.append("\nWhere the strata went, absent variable named per group:")
            lines.extend(show_absences(why))
        return "\n".join(lines), False

    why = absences(db, variables, dataset, datasets)
    denom = denominators(db, dataset)
    lines.append(f"\nUsable strata, every variable present on the same respondent (min_n={min_n}):")
    lines.extend(l.replace("(CLI: --all)", "(ask again with fewer variables to see all)")
                  .replace(", use --all", "")
                 for l in show_joint(usable, denom, cap=12))
    if why[0] or why[1]:
        lines.append("\nStrata that dropped out, absent variable named per group:")
        lines.extend(show_absences(why))
    lines.append(
        "\nPresence means non-missing on the actual variable, so a respondent who "
        "skipped a module or an exam is correctly absent.")
    return "\n".join(lines), False


def handle(req):
    if not isinstance(req, dict):   # a JSON-RPC batch is a list; do not crash on it
        return {"jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "batch requests are not supported"}}
    m, rid = req.get("method"), req.get("id")
    p = req.get("params")
    if p is None:
        p = {}
    if not isinstance(p, dict):
        # Guarding `req` and not `params` left the same crash one field down.
        # A str or list here used to reach .get() and take the process with it,
        # losing every later request on a long-lived stdio connection.
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32602, "message": "params must be an object"}}
    if m == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": p.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "covary", "version": "0.1.0"}}}
    if m == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [TOOL]}}
    if m == "tools/call":
        if p.get("name") != TOOL["name"]:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32602, "message": f"no such tool: {p.get('name')}"}}
        a = p.get("arguments", {})
        bad = validate(a)
        if bad:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32602, "message": bad}}
        try:
            text, is_err = run(a["variables"], a.get("dataset"), a.get("min_n", 1))
        except BaseException as e:
            # BaseException, not Exception: covary.connect() calls sys.exit() on a
            # bad dataset, and SystemExit used to kill the server mid-session so the
            # client hung on a request that would never be answered.
            text, is_err = f"covary failed: {type(e).__name__}: {e}", True
        return {"jsonrpc": "2.0", "id": rid,
                "result": {"content": [{"type": "text", "text": text}], "isError": is_err}}
    if rid is None:
        return None  # a notification, nothing to answer
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method: {m}"}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(req)
        if resp is not None:
            print(json.dumps(resp), flush=True)


if __name__ == "__main__":
    main()
