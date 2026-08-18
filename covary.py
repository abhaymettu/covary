#!/usr/bin/env python3
"""Which dataset-years can I actually use, given the variables my design needs?

ICPSR's variable database can tell you a study contains a variable. It cannot
tell you two variables were put to the SAME respondents, and ICPSR's own docs
name that limitation and send you to the codebook. Split ballots, rotating
modules and multi-file designs mean two variables can each have n in the
thousands in one stratum and a joint n of zero.

  covary.py numgiven socfrend                        gss, split ballot
  covary.py RIAGENDR BMXBMI LBXGLU --dataset nhanes  nhanes, fasting subsample
  covary.py FIREARM5 ACEDEPRS --dataset brfss        brfss, state optional modules
  covary.py numgiven socfrend socrel --min 300
  covary.py --find gun --dataset brfss               search names
  covary.py GUNLOAD ACEDEPRS --dataset brfss --json  machine-readable

Exit status is 1 when no stratum supports the whole set, so it works as a gate in
a pipeline rather than only as something a human reads.

Presence means non-missing on the actual variable. That is deliberately stricter
than "the file exists": a respondent who did the NHANES interview but skipped the
MEC exam is absent from exam variables, which is exactly what a joint n should
say.

Reads covary.db, which holds one bitmap per (stratum, variable). A joint n is a
popcount of an AND, so this needs nothing installed. Rebuild the db from the
parquet index with pack.py.
"""
import argparse, glob, json, os, sqlite3, sys, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
DBS = os.path.join(HERE, "covary_*.db")


def connect(dataset=None):
    """One db per dataset, unioned into a single read-only view so every query
    below stays dataset-agnostic. Naming the dataset skips the others entirely."""
    files = sorted(glob.glob(DBS))
    if dataset:
        want = os.path.join(HERE, f"covary_{dataset}.db")
        files = [f for f in files if f == want]
        if not files:
            sys.exit(f"no index for dataset {dataset!r}. have: "
                     f"{', '.join(os.path.basename(f)[7:-3] for f in sorted(glob.glob(DBS))) or 'none'}")
    if not files:
        sys.exit(f"no index: {DBS}\nbuild the parquet index, then run: python pack.py")

    db = sqlite3.connect(":memory:")
    for i, f in enumerate(files):
        db.execute(f"attach database ? as d{i}", (f"file:{f}?mode=ro",))
    for t in ("bm", "strata"):
        db.execute(f"create temp view {t} as " +
                   " union all ".join(f"select * from d{i}.{t}" for i in range(len(files))))
    return db

# What the second part of a compound stratum actually is, per dataset.
UNIT = {"brfss": "states"}


def resolve(db, vars_):
    """Fix case, and suggest near names for the rest.

    GSS variables are lowercase and NHANES and BRFSS are uppercase, so mixing them
    up is the normal case, not an edge case. NORC's own GSS Data Explorer displays
    NUMGIVEN while gssr stores numgiven. A tool that answers "not in the index" to
    the name printed in the official codebook is answering the wrong question.

    Case is corrected out loud rather than silently, since quiet correction is its
    own way of being wrong.
    """
    have = {r[0] for r in db.execute(
        "select distinct variable from bm where lower(variable) in ({})".format(
            ",".join("?" * len(vars_))), [v.lower() for v in vars_])}
    lower = {h.lower(): h for h in have}
    fixed, notes = [], []
    for v in vars_:
        h = lower.get(v.lower())
        if h and h != v:
            notes.append(f"  reading {v} as {h}")
        fixed.append(h or v)
    return fixed, notes


def all_names(db):
    """Every (dataset, variable) once, ~19k rows, served from the bm_var index.

    Fetched whole and matched in Python rather than one LIKE per name. The old
    shape was a full scan per unknown name, so 200 unknown names ran 200 scans
    and took 8.6 seconds; a cap of 8 lookups hid that rather than fixing it.
    One scan answers any number of names.
    """
    return db.execute("select distinct dataset, variable from bm order by 2, 1").fetchall()


def suggest(db, name, k=6, names=None):
    """A half-remembered name should not be a dead end. Shared with mcp_server."""
    pre = name[:4].lower()
    seen, out = set(), []
    for _, v in (names if names is not None else all_names(db)):
        if v.lower().startswith(pre) and v not in seen:
            seen.add(v); out.append(v)
            if len(out) == k:
                break
    return out


def find(db, pattern):
    """Substring search over variable names, for a half-remembered name."""
    p = pattern.lower()
    return [(ds, v) for ds, v in all_names(db) if p in v.lower()]


def marginals(db, vars_, dataset):
    """Per variable, ignoring co-administration. Straight from stored popcounts,
    so no bitmap is decompressed."""
    q = (f"select variable, dataset, sum(pop), min(substr(stratum,1,instr(stratum||'|','|')-1)),"
         f"       max(substr(stratum,1,instr(stratum||'|','|')-1)), count(*)"
         f"  from bm where variable in ({','.join('?' * len(vars_))})"
         f" group by variable, dataset order by variable, dataset")
    return db.execute(q, list(vars_)).fetchall()


def joint(db, vars_, dataset, min_n):
    """Joint n per stratum: popcount of the AND across every requested variable.

    Returns (dataset, stratum, n, filtered). `filtered` marks a stratum where every
    requested variable was collected, no respondent has them all, and the sets are
    perfectly disjoint. That is the signature of a skip pattern, not a split ballot:
    the questions WERE administered, and respondents were routed past one of them by
    their own earlier answer. BRFSS PREGNANT x PROSTATE in 2011 Hawaii is the worked
    case, one instrument and 7,606 people.

    Strata with n of 0 are returned only when min_n is 0, since they are never
    usable. They are worth showing because "collected but disjoint" and "never
    collected" are different problems with different remedies.
    """
    q = (f"select dataset, stratum, variable, pop, bits from bm"
         f" where variable in ({','.join('?' * len(vars_))})")
    by = {}
    for ds, st, var, pop, bits in db.execute(q, list(vars_)):
        by.setdefault((ds, st), {})[var] = (pop, bits)

    out = []
    for (ds, st), got in by.items():
        if len(got) < len(vars_):
            continue  # some requested variable never ran in this stratum
        # An AND cannot exceed the smallest input, so skip before decompressing.
        if min(p for p, _ in got.values()) < min_n:
            continue
        acc = None
        for _, b in sorted(got.values()):  # smallest first, the AND shrinks fastest
            v = int.from_bytes(zlib.decompress(b), "little")
            acc = v if acc is None else acc & v
            if acc == 0:
                break
        n = acc.bit_count() if acc else 0
        if n > 0:
            if n >= min_n:
                out.append((ds, st, n, False))
        elif min_n == 0:
            # Disjoint check: a union whose popcount equals the sum of popcounts
            # means no respondent appears in more than one of these variables.
            tot = union = 0
            acc2 = None
            for p, b in got.values():
                v = int.from_bytes(zlib.decompress(b), "little")
                tot += p
                acc2 = v if acc2 is None else acc2 | v
            union = acc2.bit_count() if acc2 else 0
            out.append((ds, st, 0, union == tot))
    return sorted(out)


def leave_one_out(db, vars_, dataset, k=3):
    """When nothing works, which single variable is responsible?

    That is the decision an analyst is actually making on a NONE: not "is this
    design dead" but "what do I give up to make it live". Reported as the best
    stratum reachable by dropping each one name, best drop first.

    Single drops only. Every subset is 2^n answers nobody reads, and the useful
    case is one variable carrying the whole failure.
    """
    if len(vars_) < 2:
        return []
    out = []
    for v in vars_:
        rest = [x for x in vars_ if x != v]
        best = max(joint(db, rest, dataset, 1), key=lambda r: r[2], default=None)
        if best:
            out.append((v, best[0], best[1], best[2]))
    return sorted(out, key=lambda r: -r[3])[:k]


def absences(db, vars_, dataset, datasets):
    """Why strata dropped out: which requested variables were never collected there.

    "The module did not run here" and "both modules ran and no respondent has
    both" are different problems with different remedies, and covary used to
    report them identically. BRFSS 2021 GUNLOAD x ACEDEPRS is the worked case:
    state-years ran one module or the other and none ran both, which is a fact
    about that year's module choices, not a gap in the instrument.

    Rolled up by (dataset, absent set), or a BRFSS answer is 690 lines. Returns
    [(dataset, absent_tuple, n_strata, first_head, last_head)], worst first.
    """
    by = {}
    q = "select dataset, stratum, variable from bm where variable in ({})".format(
        ",".join("?" * len(vars_)))
    for ds, st, v in db.execute(q, list(vars_)):
        by.setdefault((ds, st), set()).add(v)

    groups, none_at_all = {}, {}
    for ds, st, _ in db.execute("select dataset, stratum, n_units from strata"):
        if ds not in datasets:
            continue
        got = by.get((ds, st), set())
        if len(got) == len(vars_):
            continue          # all collected here; the failure is not absence
        head = st.partition("|")[0]
        if not got:
            # Not informative one line at a time: a stratum with none of the
            # requested variables just is not about this question. Counted once.
            none_at_all[ds] = none_at_all.get(ds, 0) + 1
            continue
        key = (ds, head, tuple(v for v in vars_ if v not in got))
        groups[key] = groups.get(key, 0) + 1
    rows = sorted((ds, head, ab, n) for (ds, head, ab), n in groups.items())
    return rows, none_at_all


def show_absences(why, cap=12):
    rows, none_at_all = why
    out = []
    for ds, head, absent, n in (rows[:cap] if cap else rows):
        out.append(f"    {ds:<7} {head:<12} {n} strat{'um' if n == 1 else 'a'}"
                   f" never collected {', '.join(absent)}")
    if cap and len(rows) > cap:
        out.append(f"    +{len(rows) - cap} more groups (CLI: --all)")
    for ds, n in sorted(none_at_all.items()):
        out.append(f"    {ds:<7} {n} more collected none of them, so they are not"
                   f" about this question")
    return out


def as_dropped(why):
    rows, none_at_all = why
    return {"partial": [{"dataset": ds, "stratum_group": head, "absent": list(ab),
                         "strata": n} for ds, head, ab, n in rows],
            "none_of_them": none_at_all}


def denominators(db, dataset):
    """How many strata exist per (dataset, leading part), the 'of 52' figure."""
    q = ("select dataset, substr(stratum,1,instr(stratum||'|','|')-1), count(*)"
         "  from strata group by 1, 2")
    return {(d, h): n for d, h, n in db.execute(q).fetchall()}


def show_joint(rows, denom, cap=12):  # rows: (dataset, stratum, n, filtered)
    out = []
    def print(*a):  # noqa: A001 - collect instead of emitting, callers join
        out.append(" ".join(str(x) for x in a))
    """Compound strata roll up, or a BRFSS query prints hundreds of lines.

    A rolled-up line is the actionable answer anyway: "4 of 52 states" is what
    decides whether a design generalises, and the state list is what you filter
    the extract on.
    """
    if not any("|" in st for _, st, _, _ in rows):
        for ds, st, n, filt in rows:
            note = "   collected but disjoint, likely a skip pattern" if filt else ""
            print(f"  {ds:<7} {st:<12} n={n}{note}")
        return out

    groups = {}
    for ds, st, n, filt in rows:
        head, _, tail = st.partition("|")
        groups.setdefault((ds, head), []).append((tail or head, n, filt))

    for (ds, head), subs in sorted(groups.items()):
        subs.sort(key=lambda x: -x[1])
        of = denom.get((ds, head))
        shown = ", ".join(t for t, _, _ in (subs[:cap] if cap else subs))
        if cap and len(subs) > cap:
            shown += f", +{len(subs) - cap} more, use --all"
        total = sum(n for _, n, _ in subs)
        print(f"  {ds:<7} {head:<12} {len(subs)}"
              f"{f' of {of}' if of else ''} {UNIT.get(ds, 'strata')}  n={total}")
        print(f"          {shown}")
        if any(f for _, _, f in subs):
            print("          collected but disjoint, likely a skip pattern rather than"
                  " a design gap")
    return out


def main():
    p = argparse.ArgumentParser(description="Joint availability of variables on the same respondents.")
    p.add_argument("variables", nargs="*")
    p.add_argument("--min", type=int, default=1,
                   help="minimum usable joint n per stratum. 0 shows zero-overlap "
                        "strata, which still do not count as usable")
    p.add_argument("--min-year", type=int, default=0, metavar="N",
                   help="minimum joint n for a whole compound group, e.g. a BRFSS "
                        "year pooled over its states. Per-stratum --min is the "
                        "wrong grain when the analyst pools, which is what they do")
    p.add_argument("--dataset", help="restrict to one dataset: gss, nhanes, brfss")
    p.add_argument("--find", metavar="PATTERN",
                   help="list indexed variable names containing PATTERN, then exit")
    p.add_argument("--all", action="store_true",
                   help="do not truncate the per-group list of strata")
    p.add_argument("--why", action="store_true",
                   help="also report why strata dropped out, which is reported "
                        "automatically when nothing is usable")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    a = p.parse_args()
    if not a.variables and not a.find:
        p.error("give at least one variable, or --find PATTERN")
    cap = None if a.all else 12
    say = (lambda *x: None) if a.json else print

    db = connect(a.dataset)

    if a.find:
        hits = find(db, a.find)
        if a.json:
            print(json.dumps({"find": a.find,
                              "matches": [{"dataset": d, "variable": v} for d, v in hits]},
                             indent=2))
        else:
            for d, v in hits:
                print(f"  {d:<7} {v}")
            print(f"{len(hits)} name{'' if len(hits) == 1 else 's'} matching {a.find!r}")
        return 0 if hits else 2

    # Dedupe. joint() keys results by variable name, so a repeated name made the
    # match count fall short of len(vars_) and silently discarded every stratum,
    # reporting a live design as dead. Order preserved for stable output.
    a.variables = list(dict.fromkeys(a.variables))

    a.variables, notes = resolve(db, a.variables)
    for n in notes:
        say(n)
    marg = marginals(db, a.variables, a.dataset)

    missing = [v for v in a.variables if v not in {r[0] for r in marg}]
    if missing:
        names = all_names(db)          # one scan, however many names are unknown
        near = {m: suggest(db, m, names=names) for m in missing}
        if a.json:
            print(json.dumps({"variables": a.variables, "ok": False,
                              "not_found": missing, "suggestions": near}, indent=2))
            return 2
        where = f"dataset {a.dataset}" if a.dataset else "any indexed dataset"
        print(f"not found in {where}: {', '.join(missing)}", file=sys.stderr)
        if a.dataset:
            print("  it may be real but belong to another dataset; retry without "
                  "--dataset", file=sys.stderr)
        for m in missing:
            if near[m]:
                print(f"  names starting like {m!r}: {', '.join(near[m])}", file=sys.stderr)
            else:
                print(f"  nothing starts like {m!r}; try --find", file=sys.stderr)
        return 2   # a bad name is not a dead design; keep the exit codes distinct

    say("per variable, ignoring co-administration:")
    for v, ds, n, lo, hi, ns in marg:
        span = lo if lo == hi else f"{lo} .. {hi}"
        say(f"  {v:<12} {ds:<7} n={n:<8} {span}, {ns} strat{'um' if ns == 1 else 'a'}")

    datasets = {r[1] for r in marg}
    if len(datasets) > 1 and not a.dataset:
        say(f"\n  note: these variables span {', '.join(sorted(datasets))}. A stratum belongs to")
        say("  one dataset, so a cross-dataset set can never have a joint n. Use --dataset.")

    rows = joint(db, a.variables, a.dataset, a.min)
    if a.min_year:
        # Pooling grain. An analyst who pools states across a BRFSS year cares
        # about the year total, and a per-stratum threshold drops small states
        # they would have kept.
        tot = {}
        for ds, st, n, _ in rows:
            tot[(ds, st.partition("|")[0])] = tot.get((ds, st.partition("|")[0]), 0) + n
        rows = [r for r in rows
                if tot[(r[0], r[1].partition("|")[0])] >= a.min_year]
    usable = [r for r in rows if r[2] > 0]

    def as_strata(rs):
        return [{"dataset": d, "stratum": s, "n": n, "collected_but_disjoint": f}
                for d, s, n, f in rs]

    if usable:
        why = absences(db, a.variables, a.dataset, datasets) if a.why else None
        if a.json:
            print(json.dumps({
                "variables": a.variables, "dataset": a.dataset, "min": a.min,
                "min_year": a.min_year or None, "notes": [n.strip() for n in notes],
                "marginals": [{"variable": v, "dataset": d, "n": n, "first": lo,
                               "last": hi, "strata": ns} for v, d, n, lo, hi, ns in marg],
                "usable": as_strata(usable), "dropped": as_dropped(why) if why else None, "ok": True,
            }, indent=2))
            return 0
        print(f"\njointly on the same respondents (min {a.min}"
              f"{f', pooled min {a.min_year}' if a.min_year else ''}):")
        print("\n".join(show_joint(usable, denominators(db, a.dataset), cap)))
        if why and (why[0] or why[1]):
            print("\nstrata that dropped out:")
            print("\n".join(show_absences(why, cap)))
        return 0

    # Nothing usable. Say only what the index can support, then say what to do
    # about it. "Never administered together" is a claim about the world and it
    # was wrong three ways: below a high --min, outside the indexed year range,
    # and for questions skipped by a filter rather than absent from the instrument.
    zeros = rows if a.min == 0 else joint(db, a.variables, a.dataset, 0)
    disjoint = sum(1 for r in zeros if r[3])
    why = absences(db, a.variables, a.dataset, datasets)
    loo = leave_one_out(db, a.variables, a.dataset)
    near = joint(db, a.variables, a.dataset, 1)
    best = max(near, key=lambda r: r[2]) if near else None

    if a.json:
        print(json.dumps({
            "variables": a.variables, "dataset": a.dataset, "min": a.min,
            "min_year": a.min_year or None, "notes": [n.strip() for n in notes],
            "marginals": [{"variable": v, "dataset": d, "n": n, "first": lo,
                           "last": hi, "strata": ns} for v, d, n, lo, hi, ns in marg],
            "usable": [], "zero": as_strata(zeros), "dropped": as_dropped(why),
            "collected_but_no_overlap": {"strata": len(zeros), "disjoint": disjoint},
            "below_threshold": ({"dataset": best[0], "stratum": best[1], "n": best[2]}
                                if best else None),
            "leave_one_out": [{"drop": d, "dataset": ds, "stratum": st, "n": n}
                              for d, ds, st, n in loo],
            "ok": False,
        }, indent=2))
        return 1

    print(f"\njointly on the same respondents (min {a.min}):")
    if a.min == 0 and zeros:
        # --min 0 asked to see them; they are still not a usable design.
        print("\n".join(show_joint(zeros, denominators(db, a.dataset), cap)))
        print("\n  No usable stratum: every one above has a joint n of 0.")
    else:
        print("  NONE. No respondent is non-missing on all of these in any stratum")
        print(f"  covered by this index{f' at a joint n of {a.min} or more' if a.min > 1 else ''}.")
        if best:
            print(f"  They DO co-occur below your threshold. Largest is "
                  f"{best[0]} {best[1]} at n={best[2]}. Lower --min to see them.")
        else:
            print("  Check the indexed range before concluding the questions never")
            print("  co-occurred: gss 1972-2024, nhanes 1999-2023, brfss 2011-2023.")

    if zeros:
        s = "stratum" if len(zeros) == 1 else "strata"
        print(f"\n  {len(zeros)} {s} collected all of these and still {'has' if len(zeros) == 1 else 'have'}")
        print("  no respondent with all of them.")
        if disjoint:
            print(f"  {disjoint} of those {'is' if disjoint == 1 else 'are'} perfectly disjoint,"
                  " the signature of a skip pattern")
            print("  rather than a module that never ran. These questions WERE administered.")
    if loo:
        print("\n  dropping one variable:")
        for d, ds, st, n in loo:
            print(f"    without {d:<12} {ds} {st} n={n}")
    if why[0] or why[1]:
        print("\n  why the rest dropped out:")
        print("\n".join(show_absences(why, cap)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
