#!/usr/bin/env python3
"""Does the text say everything the payload knows, and nothing it does not?

The signature check this replaces asserted that render() has no `db` parameter.
That was true and useless: the denominator reached the text through a private
`_denom` key the serializer stripped, so "4 of 52 states", the number that
decides whether a BRFSS design generalises, existed only in the prose. The test
passed because it grepped the source for `db.` and a laundered key has no `db`
in it. It checked the name of the property rather than the property.

This checks the property: for a spread of query shapes, every leaf value in the
payload appears in the rendered text, unless it is on a stated allowlist of
things the text deliberately summarises or omits.
"""
import subprocess, json, sys

# What the text deliberately summarises rather than prints, each with a reason.
# Kept short on purpose: an allowlist that grows to cover every failure is the
# same as no check. Truncation is in scope only in that it must be ANNOUNCED,
# which show_joint and show_absences do.
SUMMARISED = {
    "dropped":       "absence rows, truncated to the first few unless --why",
    "denominators":  "swept per group would flag every year in the dataset; the"
                     " ones that matter are checked by denominator_shown() below",
    "usable":        "compound strata roll up to a count and a member list",
    "zero":          "same rollup",
    # These said "container; its leaves are checked". They were not: leaves()
    # skipped the whole subtree, so six of ten entries were carrying a
    # justification for an exemption they were not performing. Now the
    # containers are traversed and only the named leaves above are exempt.
    "collected_but_no_overlap": "printed as counts, and omitted when zero",
}

# Rendered as wording rather than as a value, each with a reason. This list was
# one collective label over a mixed bag, and it exempted `first` and `last` -
# which are the leaves of `coverage`, the key added specifically so a bound would
# reach the reader. So the mechanism built to make this class extinct carried an
# exemption sized exactly to the surviving instance. Every entry now says what it
# is and why, and anything that is a value rather than machinery is gone.
MACHINERY = {
    "ok":       "the verdict, rendered as wording",
    "reason":   "ditto",
    "exit":     "a process exit code, not prose",
    "is_error": "a protocol flag, not prose",
    "dataset":  "named on every row",
    "min":      "rendered inside the threshold label",
    "min_year": "ditto, and only when set",
    "kind":     "note and warning types render as sentences",
    "shares_with": "rendered inside the warning sentence",
    "double_counts_people": "rendered as a marker",
    "pair_with_no_overlap": "rendered as a phrase",
    "strata_list": "rendered joined",
    "first":    "a coverage/marginal endpoint; coverage_shown() checks it on the"
                " branches where it bounds the verdict, and a marginal's span is"
                " rendered as a range or a list",
    "last":     "ditto",
}

QUERIES = [
    ["numgiven", "socfrend", "--dataset", "gss"],
    ["GUNLOAD", "ACEDEPRS", "--dataset", "brfss"],
    ["RIAGENDR", "BMXBMI", "--dataset", "nhanes"],
    ["PREGNANT", "PROSTATE", "--dataset", "brfss", "--min", "0"],
    ["numgiven", "socfrend", "--dataset", "gss", "--min", "99999"],
    ["PROSTATE", "SSBSUGR2", "--dataset", "brfss"],
    ["numgiven", "BMXBMI"],
    ["numgivn"],
    ["Sex", "Marital"],
]


def leaves(o, key=None, skip=()):
    if isinstance(o, dict):
        for k, v in o.items():
            if k in skip:
                continue
            yield from leaves(v, k, skip)
    elif isinstance(o, list):
        for v in o:
            yield from leaves(v, key, skip)
    elif o is not None and o != "" and o != []:
        yield key, o


def denominator_shown(D, text):
    """Every group that has a usable row must show its 'N of M' in the text.

    Checked specifically rather than swept, because sweeping put denominators on
    the skip list and the check then passed with the denominator hidden. That is
    the defect this file exists for: "4 of 52 states" decides whether a BRFSS
    result generalises, and it once lived only in the prose. An allowlist that
    grows to cover a failure is the same as no check.
    """
    dm = {(d["dataset"], d["group"]): d["strata"] for d in D.get("denominators", [])}
    groups = {(u["dataset"], u["stratum"].partition("|")[0])
              for u in D.get("usable", []) if "|" in u["stratum"]}
    missing = []
    for g in groups:
        n = dm.get(g)
        if n is not None and f"of {n}" not in text:
            missing.append((g, n))
    return missing


# coverage bounds a verdict only where the verdict is "absent". Elsewhere the
# index span is noise. So the exemption is conditional and checked, rather than
# blanket: on these branches it MUST be rendered.
COVERAGE_REQUIRED_ON = {"not_found", "never_together"}


def coverage_shown(D, text):
    """The index's year span must reach the reader wherever it bounds the answer.

    HSSEX is a real NHANES III variable and this index starts in 1999. The tool
    said "not found", offered an unrelated near match, and never mentioned it had
    a bound, while `coverage` sat in the same payload and printed on a sibling
    branch. That is the whole failure class this file exists for, and it survived
    six rounds because `first` and `last` were on a blanket allowlist.
    """
    if D.get("reason") not in COVERAGE_REQUIRED_ON:
        return []
    return [c for c in D.get("coverage", [])
            if c["first"] not in text or c["last"] not in text]


def main():
    bad = 0
    for q in QUERIES:
        text = subprocess.run(["python3", "covary.py"] + q,
                              capture_output=True, text=True)
        text = text.stdout + text.stderr
        js = subprocess.run(["python3", "covary.py"] + q + ["--json"],
                            capture_output=True, text=True).stdout
        try:
            D = json.loads(js)
        except json.JSONDecodeError:
            print(f"  FAIL {' '.join(q)}: --json emitted no parseable payload")
            bad += 1
            continue
        for c in coverage_shown(D, text):
            print(f"  FAIL {' '.join(q)}: reason={D['reason']} but the "
                  f"{c['dataset']} span {c['first']}-{c['last']} never reaches the text")
            bad += 1
        for g, n in denominator_shown(D, text):
            print(f"  FAIL {' '.join(q)}: {g[0]} {g[1]} has {n} strata, "
                  f"text never says 'of {n}'")
            bad += 1
        for k, v in leaves(D, skip=set(SUMMARISED)):
            if k in MACHINERY or isinstance(v, bool):
                continue
            s = f"{v:,}" if isinstance(v, int) and v >= 1000 else str(v)
            if s not in text and str(v) not in text:
                print(f"  FAIL {' '.join(q)}: payload has {k}={v!r}, text does not")
                bad += 1
    print("  ok   every payload leaf reaches the text" if not bad
          else f"  {bad} payload facts never reach the reader")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
