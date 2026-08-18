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
from covary import (connect, analyze, render, resolve, empty_payload, stamp,
                    REASONS, all_names, DBS)
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
    a["variables"] = [x.strip() for x in v]   # strip, do not merely test that it would
    if any(len(x) > 64 for x in v):
        # names are echoed back; an 8MB "name" produced an 8MB reply
        return "a variable name cannot exceed 64 characters"
    if len(v) > MAX_VARS:
        return f"at most {MAX_VARS} variables per call, got {len(v)}"
    d = a.get("dataset")
    if d is not None and d not in DATASETS:
        return f"dataset must be one of {', '.join(DATASETS)}, got {d!r}"
    if not isinstance(a.get("detail", False), bool):
        return "detail must be true or false"
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
            "detail": {
                "type": "boolean", "default": False,
                "description": "Return the full list of strata that dropped out "
                               "rather than the first few. Use when you need to know "
                               "exactly which strata lacked which variable.",
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


def run(variables, dataset=None, min_n=1, detail=False):
    if not glob.glob(DBS):
        return "No covary index found. Run pack.py to build it.", True

    variables = list(dict.fromkeys(variables))   # a repeat used to force a false NONE
    db = connect(dataset)
    variables, notes, ambiguous = resolve(db, variables)

    # The ambiguous branch used to hand-build its own sentences here, with
    # different capitalisation and a different closing line from the CLI's, for
    # the same condition. render() had a branch this interface could never reach.
    if ambiguous:
        D = empty_payload(db, variables, dataset, min_n, None)
        D.update(reason="ambiguous", ok=False,
                 ambiguous=[[v, c] for v, c in ambiguous])
        D = stamp(D)
    else:
        D = analyze(db, variables, dataset, min_n, min_year=None)

    D["notes"] = list(notes) + D["notes"]
    text = "\n".join(render(D, cap=None if detail else 12,
                             for_agent=True, detail=detail))
    if D["reason"] not in ("not_found", "ambiguous"):
        text += ("\n\nPresence means non-missing on the actual variable, so a "
                 "respondent who skipped a module or an exam is correctly absent. "
                 "Read any warning above before reporting a total to the user.")
    # isError from the same table the CLI's exit code comes from. It used to be
    # computed here from a one-member tuple, so cross_dataset was exit 2 on the
    # CLI and a clean success over MCP.
    return text, REASONS[D["reason"]]["is_error"]


def handle(req):
    if not isinstance(req, dict):   # a JSON-RPC batch is a list; do not crash on it
        return {"jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "batch requests are not supported"}}
    m, rid = req.get("method"), req.get("id")
    if rid is None and m and m.startswith("notifications/"):
        return None
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
            text, is_err = run(a["variables"], a.get("dataset"),
                               a.get("min_n", 1), a.get("detail", False))
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
