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
from covary import connect, marginals, joint, denominators, UNIT, DBS
import glob

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
        "with the joint n. An empty result means the design is not identified and "
        "no amount of analysis code will fix it."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "variables": {
                "type": "array", "items": {"type": "string"}, "minItems": 1,
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
                "description": "Only report strata with at least this joint n.",
            },
        },
        "required": ["variables"],
    },
}


def suggest(db, name, k=5):
    """An agent that guesses a variable name gets a dead end otherwise."""
    like = name[:4] + "%"
    rows = db.execute(
        "select distinct variable from bm where variable like ? limit ?", (like, k)).fetchall()
    return [r[0] for r in rows]


def run(variables, dataset=None, min_n=1):
    if not glob.glob(DBS):
        return "No covary index found. Run pack.py to build it.", True

    db = connect(dataset)
    marg = marginals(db, variables, dataset)
    missing = [v for v in variables if v not in {r[0] for r in marg}]
    if missing:
        out = [f"Not in the index: {', '.join(missing)}."]
        for m in missing:
            s = suggest(db, m)
            if s:
                out.append(f"  names starting like {m!r}: {', '.join(s)}")
        return "\n".join(out), True

    lines = ["Per variable, ignoring co-administration:"]
    for v, ds, n, lo, hi, ns in marg:
        span = lo if lo == hi else f"{lo} .. {hi}"
        lines.append(f"  {v}  dataset={ds}  n={n}  {span}, {ns} strat{'um' if ns == 1 else 'a'}")

    datasets = {r[1] for r in marg}
    if len(datasets) > 1 and not dataset:
        lines.append(
            f"\nThese variables span {', '.join(sorted(datasets))}. A stratum belongs to one "
            "dataset, so this set can never have a joint n. Set `dataset` to one of them.")

    rows = joint(db, variables, dataset, min_n)
    if not rows:
        lines.append(
            f"\nNOT IDENTIFIED. No stratum has all of these on the same respondents "
            f"(min_n={min_n}). These variables were never administered together. "
            "Choose different variables or a different dataset. Do not proceed with "
            "this design.")
        return "\n".join(lines), False

    denom = denominators(db, dataset)
    lines.append(f"\nUsable strata, every variable present on the same respondent (min_n={min_n}):")
    if any("|" in st for _, st, _ in rows):
        groups = {}
        for ds, st, n in rows:
            head, _, tail = st.partition("|")
            groups.setdefault((ds, head), []).append((tail or head, n))
        for (ds, head), subs in sorted(groups.items()):
            subs.sort(key=lambda x: -x[1])
            of = denom.get((ds, head))
            lines.append(
                f"  {ds} {head}: {len(subs)}{f' of {of}' if of else ''} "
                f"{UNIT.get(ds, 'strata')}, joint n={sum(n for _, n in subs)}")
            lines.append("    " + ", ".join(f"{t} ({n})" for t, n in subs))
    else:
        for ds, st, n in rows:
            lines.append(f"  {ds} {st}: joint n={n}")

    lines.append(
        "\nPresence means non-missing on the actual variable, so a respondent who "
        "skipped a module or an exam is correctly absent.")
    return "\n".join(lines), False


def handle(req):
    m, rid = req.get("method"), req.get("id")
    if m == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": req.get("params", {}).get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "covary", "version": "0.1.0"}}}
    if m == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [TOOL]}}
    if m == "tools/call":
        p = req.get("params", {})
        if p.get("name") != TOOL["name"]:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32602, "message": f"no such tool: {p.get('name')}"}}
        a = p.get("arguments", {})
        try:
            text, is_err = run(a.get("variables", []), a.get("dataset"), a.get("min_n", 1))
        except Exception as e:
            text, is_err = f"covary failed: {e}", True
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
