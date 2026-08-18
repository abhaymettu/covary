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
import argparse, glob, os, sqlite3, sys, zlib

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


def suggest(db, name, k=6):
    """A half-remembered name should not be a dead end. Shared with mcp_server."""
    rows = db.execute(
        "select distinct variable from bm where lower(variable) like ? limit ?",
        (name[:4].lower() + "%", k)).fetchall()
    return [r[0] for r in rows]


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
        shown = ", ".join(t for t, _, _ in subs[:cap])
        if len(subs) > cap:
            shown += f", +{len(subs) - cap} more"
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
    p.add_argument("variables", nargs="+")
    p.add_argument("--min", type=int, default=1,
                   help="minimum usable joint n per stratum. 0 shows zero-overlap "
                        "strata, which still do not count as usable")
    p.add_argument("--dataset", help="restrict to one dataset: gss, nhanes, brfss")
    a = p.parse_args()

    # Dedupe. joint() keys results by variable name, so a repeated name made the
    # match count fall short of len(vars_) and silently discarded every stratum,
    # reporting a live design as dead. Order preserved for stable output.
    a.variables = list(dict.fromkeys(a.variables))

    db = connect(a.dataset)
    a.variables, notes = resolve(db, a.variables)
    for n in notes:
        print(n)
    marg = marginals(db, a.variables, a.dataset)

    missing = [v for v in a.variables if v not in {r[0] for r in marg}]
    if missing:
        where = f"dataset {a.dataset}" if a.dataset else "any indexed dataset"
        print(f"not found in {where}: {', '.join(missing)}", file=sys.stderr)
        if a.dataset:
            print("  it may be real but belong to another dataset; retry without "
                  "--dataset", file=sys.stderr)
        for m in missing[:8]:
            sg = suggest(db, m)
            if sg:
                print(f"  names starting like {m!r}: {', '.join(sg)}", file=sys.stderr)
        return 2   # a bad name is not a dead design; keep the exit codes distinct

    print("per variable, ignoring co-administration:")
    for v, ds, n, lo, hi, ns in marg:
        span = lo if lo == hi else f"{lo} .. {hi}"
        print(f"  {v:<12} {ds:<7} n={n:<8} {span}, {ns} strat{'um' if ns == 1 else 'a'}")

    datasets = {r[1] for r in marg}
    if len(datasets) > 1 and not a.dataset:
        print(f"\n  note: these variables span {', '.join(sorted(datasets))}. A stratum belongs to")
        print("  one dataset, so a cross-dataset set can never have a joint n. Use --dataset.")

    rows = joint(db, a.variables, a.dataset, a.min)
    usable = [r for r in rows if r[2] > 0]
    print(f"\njointly on the same respondents (min {a.min}):")
    if rows and not usable:
        # --min 0 asked to see them; they are still not a usable design.
        print("\n".join(show_joint(rows, denominators(db, a.dataset))))
        print("\n  No usable stratum: every one above has a joint n of 0.")
        return 1
    if not usable:
        # Say only what the index can support. "Never administered together" is a
        # claim about the world and it was wrong three ways: below a high --min,
        # outside the indexed year range, and for questions skipped by a filter
        # rather than absent from the instrument.
        print("  NONE. No respondent is non-missing on all of these in any stratum")
        print(f"  covered by this index{f' at a joint n of {a.min} or more' if a.min > 1 else ''}.")
        near = joint(db, a.variables, a.dataset, 1)
        if near:
            best = max(near, key=lambda r: r[2])
            print(f"  They DO co-occur below your threshold. Largest is "
                  f"{best[0]} {best[1]} at n={best[2]}. Lower --min to see them.")
        else:
            print("  Check the indexed range before concluding the questions never")
            print("  co-occurred: gss 1972-2024, nhanes 1999-2023, brfss 2011-2023.")
            print("  Absence can also mean a question was skipped by a filter rather")
            print("  than missing from the instrument. Run with --min 0 to see strata")
            print("  where all of these were collected but no respondent has them all.")
        return 1
    print("\n".join(show_joint(usable, denominators(db, a.dataset))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
