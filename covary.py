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

Exit status: 0 when at least one stratum carries every variable, 1 when none
does, 2 when the question cannot be answered as asked (a name is unknown or
ambiguous, or the set spans datasets so no stratum could hold it). --json carries
the same verdict as `reason` and `exit`.

Presence means non-missing on the actual variable. That is deliberately stricter
than "the file exists": a respondent who did the NHANES interview but skipped the
MEC exam is absent from exam variables, which is exactly what a joint n should
say.

Reads covary.db, which holds one bitmap per (stratum, variable). A joint n is a
popcount of an AND, so this needs nothing installed. Rebuild the db from the
parquet index with pack.py.
"""
import argparse, difflib, glob, json, os, sqlite3, sys, zlib

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

# Strata that contain the same physical people under identifiers that cannot be
# matched. CDC renumbers SEQN for the pre-pandemic release, so 2017-2020 shares no
# identifier with 2017-2018 while covering those respondents plus the partial
# 2019-2020 collection. Presence cannot detect this, and the marginal is a plain
# sum, so a reader who adds these strata counts real people twice. Merging is not
# possible here, unlike the pooled spans whose SEQNs are shared; a warning is the
# honest limit.
PHYSICAL_OVERLAP = {("nhanes", "2017-2020"): ("2017-2018",)}


def mode_note(db, rows):
    """GSS records how each respondent was interviewed, from 2004 on.

    A joint n says nothing about whether the administration mode was comparable
    across the years it spans. GSS 2021 leaned heavily on web after the pandemic
    interrupted in-person fieldwork, and a 2021 n sitting beside a 1985 n in the
    same column invites a comparison the tool cannot support.

    This points at the variable rather than claiming which years are which. The
    values are in the data; asserting a characterisation from memory is how the
    disjointness heuristic ended up backwards.
    """
    yrs = sorted({st for ds, st, *_ in rows if ds == "gss"})
    if not yrs:
        return None
    have = {r[0] for r in db.execute(
        "select distinct stratum from bm where dataset='gss' and variable='mode'")}
    hit = [y for y in yrs if y in have]
    return hit if hit else None


def overlap_warning(rows):
    """Strata in this result that describe some of the same people."""
    have = {(ds, st) for ds, st, *_ in rows}
    out = []
    for (ds, st), others in PHYSICAL_OVERLAP.items():
        if (ds, st) in have:
            clash = [o for o in others if (ds, o) in have]
            if clash:
                out.append((ds, st, clash))
    return out


def resolve(db, vars_):
    """Fix case, and refuse to guess when case alone is ambiguous.

    GSS variables are lowercase and NHANES and BRFSS are uppercase, so mixing them
    up is the normal case. NORC's own Data Explorer prints NUMGIVEN while gssr
    stores numgiven.

    Eleven names exist in more than one dataset differing only by case: age, sex,
    sex1, marital, children, race2, nummen, numwomen, physhlth, feelnerv, internet.
    An earlier version built a dict keyed on the lowered name, so one spelling won
    arbitrarily and `covary.py sex marital` answered from BRFSS. Five correctly
    typed GSS names came back as a dead design because two were silently swapped.
    A coin flip is the worst possible behaviour here, so an ambiguous name is now
    an error that names both candidates and asks for --dataset.
    """
    rows = db.execute(
        "select distinct dataset, variable from bm where lower(variable) in ({})".format(
            ",".join("?" * len(vars_))), [v.lower() for v in vars_]).fetchall()
    by_lower = {}
    for ds, name in rows:
        by_lower.setdefault(name.lower(), set()).add(name)

    fixed, notes, ambiguous = [], [], []
    for v in vars_:
        cands = by_lower.get(v.lower(), set())
        if v in cands or not cands:
            fixed.append(v)                       # exact hit, or unknown, handled later
        elif len(cands) == 1:
            h = next(iter(cands))
            notes.append(f"  reading {v} as {h}")
            fixed.append(h)
        else:
            ambiguous.append((v, sorted(cands)))
            fixed.append(v)
    return fixed, notes, ambiguous


def all_names(db):
    """Every (dataset, variable) once, ~19k rows, served from the bm_var index.

    Fetched whole and matched in Python rather than one LIKE per name. The old
    shape was a full scan per unknown name, so 200 unknown names ran 200 scans
    and took 8.6 seconds; a cap of 8 lookups hid that rather than fixing it.
    One scan answers any number of names.
    """
    return db.execute("select distinct dataset, variable from bm order by 2, 1").fetchall()


def suggest(db, name, k=6, names=None):
    """A half-remembered name should not be a dead end.

    Prefix matching alone cannot find a transposition, which is the typo people
    actually make: DR1TKCLA is one swap from DR1TKCAL and shares its first four
    characters with seventy other names, so a prefix scan returned six of them
    alphabetically and never the right one. Combine both: near-spellings first,
    then prefix matches to fill, since a wrong first syllable is the other common
    case and edit distance is bad at it.
    """
    rows = names if names is not None else all_names(db)
    vars_ = [v for _, v in rows]                 # all_names gives (dataset, variable)
    up = {}
    for v in vars_:
        up.setdefault(v.upper(), v)
    close = difflib.get_close_matches(name.upper(), list(up), n=k, cutoff=0.7)
    out = [up[c] for c in close]
    pre = name[:4].lower()
    for v in vars_:
        if len(out) >= k:
            break
        if v.lower().startswith(pre) and v not in out:
            out.append(v)
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

    Returns (dataset, stratum, n, disjoint). `disjoint` marks a stratum where every
    requested variable was collected, no respondent has them all, and no respondent
    appears in more than one of them.

    That is an OBSERVATION, not a diagnosis, and an earlier version of this code got
    it exactly backwards. It claimed perfect disjointness was the signature of a skip
    pattern "and a split ballot does not look like that". A split ballot is a
    partition, so perfect disjointness is precisely what it looks like. The heuristic
    fired on GSS 2004 numgiven x socfrend, the example the README opens with, and
    told the reader the questions WERE administered together. They were not. Being
    confidently wrong in the optimistic direction is worse than the pessimism it
    replaced, because nothing downstream contradicts it.

    So `disjoint` now means only what it measures: these respondent sets do not
    overlap at all. A skip pattern and a ballot rotation both produce it, and the
    codebook is what tells them apart.

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
            # Pairwise, not mutual. Requiring every variable to be disjoint from
            # every other meant adding one universally-asked covariate silently
            # deleted the finding, which is what specifying a real model does.
            vs = [int.from_bytes(zlib.decompress(b), "little") for _, b in got.values()]
            pairwise = any(
                (vs[i] & vs[j]) == 0 and vs[i] and vs[j]
                for i in range(len(vs)) for j in range(i + 1, len(vs)))
            out.append((ds, st, 0, pairwise))
    return sorted(out)


def usable_strata(db, vars_, dataset, min_n, min_year):
    """The single definition of "usable". Returns (all_rows, usable_rows).

    Both thresholds live here because they did not, and that cost three bugs. The
    text branch, the JSON branch and leave_one_out each applied their own subset:
    --min got audited after round two and --min-year did not, so leave-one-out
    promised n=2690 under a query whose pooled threshold was 5000. A fix that
    lands on the flag a report happens to name will keep missing its siblings.
    """
    rows = joint(db, vars_, dataset, min_n)
    out = [r for r in rows if r[2] > 0]
    if min_year:
        tot = {}
        for ds, st, n, _ in out:
            k = (ds, st.partition("|")[0])
            tot[k] = tot.get(k, 0) + n
        out = [r for r in out if tot[(r[0], r[1].partition("|")[0])] >= min_year]
    return rows, out


def threshold_label(min_n, min_year):
    """One phrasing of the thresholds, so no message can describe a different one."""
    bits = []
    if min_n != 1:
        bits.append(f"min {min_n}")
    if min_year:
        bits.append(f"pooled min {min_year}")
    return ", ".join(bits) if bits else "min 1"


def leave_one_out(db, vars_, dataset, min_n=1, min_year=None, k=3):
    """When nothing works, which single variable is responsible?

    That is the decision an analyst is actually making on a NONE: not "is this
    design dead" but "what do I give up to make it live". Reported as the best
    stratum reachable by dropping each one name, best drop first.

    Single drops only. Every subset is 2^n answers nobody reads, and the useful
    case is one variable carrying the whole failure.

    Honours min_n. It did not, so the header said dropping a variable "makes it
    usable again" over numbers below the threshold the caller had just set. A blind
    test showed a model read n=2701 under that header and told the user it was
    "comfortably over 5000". Every other assertion here was audited against min_n
    after the first round; this one was added afterwards and skipped it.
    """
    if len(vars_) < 2:
        return []
    out = []
    for v in vars_:
        rest = [x for x in vars_ if x != v]
        _, u = usable_strata(db, rest, dataset, min_n, min_year)
        best = max(u, key=lambda r: r[2], default=None)
        # n of 0 is not "reachable". Dropping the variable produces the same NONE
        # with one fewer name in it.
        if best and best[2] > 0:
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
            note = "   a pair here has no respondent in common" if filt else ""
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
            print("          collected, with no respondent in more than one")
    return out


# How to tell the reader to ask for more. The CLI has flags; an agent has
# arguments. Scrubbing flag names out of finished text failed three rounds in a
# row because each round emitted one from a new place, so the phrasing is chosen
# where it is written instead.
HINTS = {
    # The agent phrasings name a capability the tool actually has. They used to
    # say "ask again with fewer variables", which answers a different question,
    # so the flag name was gone and the unreachable capability was not.
    "all":  ("use --all",             "call again with detail true"),
    "why":  ("--why for all of them", "call again with detail true"),
    "min0": ("run with --min 0",             "call again with min_n 0"),
}


def hint(key, for_agent):
    return HINTS[key][1 if for_agent else 0]


def analyze(db, vars_, dataset, min_n, min_year):
    """Compute the whole answer as data. Builds no sentences.

    This inversion is the fix for a defect found in four consecutive review
    rounds under four different names. Every earlier version appended to a list
    of lines and to a dict in two parallel manual passes, so every line was an
    opportunity to forget the dict, and the omission always landed on whichever
    caveat had been added most recently. Twice the docstring declared the class
    closed on the same visit that forgot it.

    So: nothing here emits text, and render() below reads nothing but this
    structure. A fact cannot reach the reader without being in the payload,
    because the text is a function of the payload.

    `reason` is the discriminator, and it is deliberately finer than ok/not ok:

      ok                      at least one stratum carries every variable
      below_threshold         they co-occur, but under the threshold you set
      never_together          no stratum has them all, at any threshold
      cross_dataset           the set spans datasets, so no stratum could
      not_found               a name is not in the index
      ambiguous              a name differs only by case across datasets
    """
    D = {"variables": list(vars_), "dataset": dataset, "min": min_n,
         "min_year": min_year, "notes": [], "warnings": [], "marginals": [],
         "usable": [], "zero": [], "dropped": None, "leave_one_out": [],
         "best_below_threshold": None, "not_found": [], "suggestions": {},
         "ambiguous": [], "cross_dataset": None}

    marg = marginals(db, vars_, dataset)
    found = {r[0] for r in marg}
    missing = [v for v in vars_ if v not in found]
    if missing:
        names = all_names(db)
        D.update(not_found=missing,
                 suggestions={m: suggest(db, m, names=names) for m in missing},
                 reason="not_found", ok=False, exit=2)
        return D

    datasets = {r[1] for r in marg}
    overlapping = []
    if len(datasets) > 1 and not dataset:
        result_rows = []
    else:
        _all, result_rows = usable_strata(db, vars_, dataset, min_n, min_year)
        overlapping = overlap_warning(result_rows)

    for v, ds, n, lo, hi, ns in marg:
        strata = [r[0] for r in db.execute(
            "select distinct substr(stratum,1,instr(stratum||'|','|')-1) from bm"
            " where variable = ? and dataset = ? order by 1", (v, ds))]
        vs = {r[0] for r in db.execute(
            "select distinct stratum from bm where variable = ? and dataset = ?", (v, ds))}
        dbl = any(d2 == ds and st in vs and any(o in vs for o in others)
                  and any(r[0] == d2 and r[1] == st for r in result_rows)
                  and any(r[0] == d2 and r[1] in others for r in result_rows)
                  for (d2, st), others in PHYSICAL_OVERLAP.items())
        D["marginals"].append(
            {"variable": v, "dataset": ds, "n": n, "first": lo, "last": hi,
             "strata": ns, "strata_list": strata if ns <= 8 else None,
             "double_counts_people": dbl})

    if len(datasets) > 1 and not dataset:
        D.update(cross_dataset=sorted(datasets), reason="cross_dataset",
                 ok=False, exit=2)
        return D

    rows, usable = usable_strata(db, vars_, dataset, min_n, min_year)
    why = absences(db, vars_, dataset, datasets)
    zeros = [r for r in (rows if min_n == 0 else joint(db, vars_, dataset, 0))
             if r[2] == 0]

    D["usable"] = [{"dataset": ds, "stratum": st, "n": n} for ds, st, n, _ in usable]
    D["zero"] = [{"dataset": ds, "stratum": st, "pair_with_no_overlap": bool(f)}
                 for ds, st, _, f in zeros]
    D["collected_but_no_overlap"] = {"strata": len(zeros),
                                     "disjoint": sum(1 for r in zeros if r[3])}
    D["dropped"] = as_dropped(why)
    D["_why"] = why                       # for the renderer only, stripped before json

    if usable:
        D.update(reason="ok", ok=True, exit=0)
    else:
        near = [r for r in joint(db, vars_, dataset, 1) if r[2] > 0]
        best = max(near, key=lambda r: r[2], default=None)
        if best:
            D["best_below_threshold"] = {"dataset": best[0], "stratum": best[1],
                                         "n": best[2]}
            D.update(reason="below_threshold", ok=False, exit=1)
        else:
            D.update(reason="never_together", ok=False, exit=1)
        D["leave_one_out"] = [{"drop": d, "dataset": ds2, "stratum": st, "n": n}
                              for d, ds2, st, n in
                              leave_one_out(db, vars_, dataset, min_n, min_year)]

    mn = mode_note(db, usable or rows)
    if mn:
        D["notes"].append({"kind": "gss_mode", "strata": mn})
    for ds, st, clash in overlapping:
        D["warnings"].append({"kind": "same_people_different_ids",
                              "dataset": ds, "stratum": st, "shares_with": clash})
    return D


def render(D, cap=12, for_agent=False, detail=False):
    """Turn the structure into lines. Reads D and nothing else.

    No database handle by design: if this function cannot reach the data, it
    cannot say anything the payload does not contain.
    """
    lim = cap if cap is not None else 10 ** 9
    L = []

    if D["reason"] == "not_found":
        where = f"dataset {D['dataset']}" if D["dataset"] else "any indexed dataset"
        L.append(f"not found in {where}: {', '.join(D['not_found'])}")
        if D["dataset"]:
            L.append("  it may be real but belong to another dataset; ask without "
                     "naming one")
        for m in D["not_found"]:
            sg = D["suggestions"].get(m)
            L.append(f"  did you mean: {', '.join(sg)}" if sg else
                     f"  no near match for {m!r}")
        return L

    if D["reason"] == "ambiguous":
        for v, cands in D["ambiguous"]:
            L.append(f"ambiguous: {v!r} differs only by case from "
                     f"{', '.join(cands)}, which are in different datasets")
        L.append("  name one dataset, or spell it as that dataset spells it")
        return L

    for n in D["notes"]:
        if isinstance(n, str):
            L.append(n)
    L.append("per variable, ignoring co-administration:")
    for m in D["marginals"]:
        span = (", ".join(m["strata_list"]) if m["strata_list"]
                else f"{m['first']} .. {m['last']}, {m['strata']} strata")
        dbl = "  <- counts some people twice, see warning below" if m["double_counts_people"] else ""
        L.append(f"  {m['variable']:<12} {m['dataset']:<7} n={m['n']:<8} {span}{dbl}")

    if D["reason"] == "cross_dataset":
        L.append("")
        L.append(f"These variables span {', '.join(D['cross_dataset'])}. A stratum "
                 "belongs to one dataset, so this set can never have a joint n. "
                 "Name one dataset.")
        return L

    label = threshold_label(D["min"], D["min_year"])
    L.append("")
    L.append(f"jointly on the same respondents ({label}):")

    if D["reason"] == "ok":
        rows = [(u["dataset"], u["stratum"], u["n"], False) for u in D["usable"]]
        L.extend(show_joint(rows, D.get("_denom", {}), cap))
        if D["min"] == 0 and D["zero"]:
            L.append("")
            L.append("also collected together, with no respondent having all of them:")
            L.extend(show_joint([(z["dataset"], z["stratum"], 0,
                                  z["pair_with_no_overlap"]) for z in D["zero"]],
                                D.get("_denom", {}), cap))
            L.append("  A split ballot and a skip pattern both look exactly like this,")
            L.append("  so presence cannot tell them apart. The codebook decides.")
    else:
        L.append("  NONE. No respondent is non-missing on all of these in any stratum")
        L.append(f"  covered by this index at {label}.")
        b = D["best_below_threshold"]
        if b:
            which = "--min-year" if (D["min_year"] and b["n"] >= D["min"]) else "--min"
            L.append(f"  They DO co-occur below your threshold. Largest is "
                     f"{b['dataset']} {b['stratum']} at n={b['n']}. Lower "
                     f"{which if not for_agent else 'the threshold'}, which is what "
                     "excluded them.")
        else:
            L.append("  Before concluding these were never asked together: this index")
            L.append("  covers gss 1972-2024, nhanes 1999-2023, brfss 2011-2023, so an")
            L.append("  earlier or later administration is invisible here. And a")
            L.append("  question skipped by a filter is absent for that reason, not")
            L.append(f"  because it was left off the instrument. {hint('min0', for_agent).capitalize()}")
            L.append("  to see strata where all were collected but no one has them all.")
        c = D.get("collected_but_no_overlap", {})
        if c.get("strata"):
            L.append("")
            L.append(f"  {c['strata']} stratum or strata collected all of these and still")
            L.append("  have no respondent with all of them.")
            if c.get("disjoint"):
                L.append(f"  {c['disjoint']} of those contain a pair with no respondent in common.")
                L.append("  A split ballot and a skip pattern both look exactly like this, so")
                L.append("  the codebook decides which it is.")
        if D["leave_one_out"]:
            L.append("")
            L.append(f"  best reachable by dropping one variable, at {label}:")
            for l in D["leave_one_out"]:
                L.append(f"    without {l['drop']:<12} {l['dataset']} {l['stratum']} n={l['n']}")
        elif len(D["variables"]) > 1:
            L.append("")
            L.append("  no single variable is responsible: dropping any one of them still")
            L.append(f"  leaves nothing at {label}.")

    why = D.get("_why")
    if why and (why[0] or why[1]):
        L.append("")
        L.append("strata that dropped out:")
        n_show = lim if (detail or cap is None) else min(3, lim)
        L.extend(show_absences(why, n_show))
        if len(why[0]) > n_show:
            L.append(f"    ({len(why[0]) - n_show} more; {hint('why', for_agent)})")

    for n in D["notes"]:
        if isinstance(n, dict) and n["kind"] == "gss_mode":
            L.append("")
            L.append(f"  note: gss records an interview `mode` variable in "
                     f"{', '.join(n['strata'])}. A joint n does not tell you the mode")
            L.append("  was comparable across those years.")
    for w in D["warnings"]:
        L.append("")
        L.append(f"  warning: {w['dataset']} {w['stratum']} and "
                 f"{', '.join(w['shares_with'])} contain the same people under")
        L.append("  different respondent ids. Do not pool them or add their n together,")
        L.append("  and note the per-variable n above sums both. Pick one.")

    if for_agent:
        L = [l.replace("(CLI: --all)", f"({hint('all', True)})")
              .replace(", use --all", "") for l in L]
    return L


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
            print(json.dumps({"find": a.find, "reason": "find", "ok": bool(hits),
                              "matches": [{"dataset": d, "variable": v}
                                          for d, v in hits]}, indent=2))
        else:
            for d, v in hits:
                print(f"  {d:<7} {v}")
            print(f"{len(hits)} name{'' if len(hits) == 1 else 's'} matching {a.find!r}")
        return 0 if hits else 2

    a.variables, notes, ambiguous = resolve(db, a.variables)
    # Dedupe AFTER resolve. joint() keys by name, so a repeat made the match count
    # fall short and discarded every stratum, reporting a live design as dead.
    a.variables = list(dict.fromkeys(a.variables))

    if ambiguous:
        D = {"variables": a.variables, "dataset": a.dataset, "min": a.min,
             "min_year": a.min_year, "reason": "ambiguous", "ok": False, "exit": 2,
             "ambiguous": [[v, c] for v, c in ambiguous], "notes": [], "warnings": []}
    else:
        D = analyze(db, a.variables, a.dataset, a.min, a.min_year)
    D["notes"] = [n.strip() for n in notes] + D["notes"]

    if a.json:
        # Every branch is a payload with a reason. Two of them used to bypass
        # this entirely: not_found was hand-built without a reason key, and
        # ambiguous printed to stderr and emitted no json at all, so a pipeline
        # doing `--json | jq` got a parse error instead of a verdict.
        print(json.dumps({k: v for k, v in D.items() if not k.startswith("_")},
                         indent=2))
    else:
        cap = None if a.all else 12
        D["_denom"] = denominators(db, a.dataset)
        out = render(D, cap, detail=a.why)
        stream = sys.stderr if D["reason"] in ("not_found", "ambiguous") else sys.stdout
        print("\n".join(out), file=stream)
    return D["exit"]


if __name__ == "__main__":
    sys.exit(main())
