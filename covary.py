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


def marginals(db, vars_, dataset):
    """Per variable, ignoring co-administration. Straight from stored popcounts,
    so no bitmap is decompressed."""
    q = (f"select variable, dataset, sum(pop), min(substr(stratum,1,instr(stratum||'|','|')-1)),"
         f"       max(substr(stratum,1,instr(stratum||'|','|')-1)), count(*)"
         f"  from bm where variable in ({','.join('?' * len(vars_))})"
         f" group by variable, dataset order by variable, dataset")
    return db.execute(q, list(vars_)).fetchall()


def joint(db, vars_, dataset, min_n):
    """Joint n per stratum: popcount of the AND across every requested variable."""
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
        if n >= min_n:
            out.append((ds, st, n))
    return sorted(out)


def denominators(db, dataset):
    """How many strata exist per (dataset, leading part), the 'of 52' figure."""
    q = ("select dataset, substr(stratum,1,instr(stratum||'|','|')-1), count(*)"
         "  from strata group by 1, 2")
    return {(d, h): n for d, h, n in db.execute(q).fetchall()}


def show_joint(rows, denom, cap=12):
    """Compound strata roll up, or a BRFSS query prints hundreds of lines.

    A rolled-up line is the actionable answer anyway: "4 of 52 states" is what
    decides whether a design generalises, and the state list is what you filter
    the extract on.
    """
    if not any("|" in st for _, st, _ in rows):
        for ds, st, n in rows:
            print(f"  {ds:<7} {st:<12} n={n}")
        return

    groups = {}
    for ds, st, n in rows:
        head, _, tail = st.partition("|")
        groups.setdefault((ds, head), []).append((tail or head, n))

    for (ds, head), subs in sorted(groups.items()):
        subs.sort(key=lambda x: -x[1])
        of = denom.get((ds, head))
        shown = ", ".join(t for t, _ in subs[:cap])
        if len(subs) > cap:
            shown += f", +{len(subs) - cap} more"
        print(f"  {ds:<7} {head:<12} {len(subs)}"
              f"{f' of {of}' if of else ''} {UNIT.get(ds, 'strata')}  n={sum(n for _, n in subs)}")
        print(f"          {shown}")


def main():
    p = argparse.ArgumentParser(description="Joint availability of variables on the same respondents.")
    p.add_argument("variables", nargs="+")
    p.add_argument("--min", type=int, default=1, help="minimum usable joint n per stratum")
    p.add_argument("--dataset", help="restrict to one dataset: gss, nhanes, brfss")
    a = p.parse_args()

    db = connect(a.dataset)
    marg = marginals(db, a.variables, a.dataset)

    missing = set(a.variables) - {r[0] for r in marg}
    if missing:
        sys.exit(f"not in {a.dataset or 'any indexed dataset'}: {', '.join(sorted(missing))}")

    print("per variable, ignoring co-administration:")
    for v, ds, n, lo, hi, ns in marg:
        span = lo if lo == hi else f"{lo} .. {hi}"
        print(f"  {v:<12} {ds:<7} n={n:<8} {span}, {ns} strat{'um' if ns == 1 else 'a'}")

    datasets = {r[1] for r in marg}
    if len(datasets) > 1 and not a.dataset:
        print(f"\n  note: these variables span {', '.join(sorted(datasets))}. A stratum belongs to")
        print("  one dataset, so a cross-dataset set can never have a joint n. Use --dataset.")

    rows = joint(db, a.variables, a.dataset, a.min)
    print(f"\njointly on the same respondents (min {a.min}):")
    if not rows:
        print("  NONE. These variables were never administered together.")
        print("  The design is not identified here. Pick different variables or a different dataset.")
        return 1
    show_joint(rows, denominators(db, a.dataset))
    return 0


if __name__ == "__main__":
    sys.exit(main())
