"""
.cache/labels_*.csv -> labels.db, the text layer over the presence index.

Deliberately NOT named covary_*.db, for two load-bearing reasons:

  * covary.py globs covary_*.db and unions whatever it finds into the bm and
    strata views. A fourth file matching that glob breaks connect().
  * audit.log records the sha256 of each covary_*.db it verified, and
    `shasum -a 256 covary_*.db` is documented as the check a user runs. Adding a
    table to those files would invalidate that chain for a change the audit does
    not cover.

labels.db is derived, regenerable, and unaudited. It carries no presence data,
and nothing in it is evidence about co-administration.

    python3 pack_labels.py

Stdlib only, matching the query path.
"""

import csv, glob, os, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "labels.db")
SRC = os.path.join(HERE, ".cache", "labels_*.csv")

# GSS question text runs to a paragraph and one item is far longer than csv's
# default field limit, which fails as a truncated read rather than an error.
csv.field_size_limit(10_000_000)

SCHEMA = """
create table labels(dataset text, variable text, description text,
                    question text, source text,
                    primary key (dataset, variable));
create virtual table labels_fts using fts5(
    variable, description, question,
    content='labels', tokenize='porter unicode61');
"""


def read_csvs():
    files = sorted(glob.glob(SRC))
    if not files:
        sys.exit(f"no label CSVs at {SRC}\nrun: Rscript build_labels.R")
    rows = []
    for f in files:
        with open(f, newline="", encoding="utf-8") as fh:
            got = list(csv.DictReader(fh))
        # An empty CSV would pack a dataset to nothing and still look like a
        # clean run, so refuse it here rather than ship a half-built index.
        if not got:
            sys.exit(f"{os.path.basename(f)} has no rows; delete it and rebuild")
        print(f"  {os.path.basename(f)}: {len(got)} rows")
        rows.extend(got)
    return rows


def coverage(db):
    """How much of the presence index this text actually reaches.

    The failure mode of this build path is a partially fetched source: it
    produces a db that answers some searches and silently misses whole regions
    of a survey. Comparing against the index is the only thing that catches it.
    """
    out = []
    for f in sorted(glob.glob(os.path.join(HERE, "covary_*.db"))):
        ds = os.path.basename(f)[7:-3]
        idx = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
        names = {v for (v,) in idx.execute("select distinct variable from bm")}
        idx.close()
        have = {v for (v,) in db.execute(
            "select variable from labels where dataset=?", (ds,))}
        hit = len(names & have)
        out.append((ds, hit, len(names), 100.0 * hit / len(names) if names else 0.0))
    return out


def main():
    rows = read_csvs()
    if os.path.exists(OUT):
        os.remove(OUT)
    db = sqlite3.connect(OUT)
    db.executescript(SCHEMA)
    db.executemany(
        "insert or replace into labels values (?,?,?,?,?)",
        [(r["dataset"], r["variable"], r.get("description") or "",
          r.get("question") or "", r.get("source") or "") for r in rows])
    db.execute("insert into labels_fts(rowid, variable, description, question) "
               "select rowid, variable, description, question from labels")
    db.commit()

    print(f"\n{OUT}: {db.execute('select count(*) from labels').fetchone()[0]} labels, "
          f"{os.path.getsize(OUT) / 1e6:.1f} MB")
    print("\ncoverage of the presence index:")
    for ds, hit, tot, pct in coverage(db):
        print(f"  {ds:<8} {hit:>6} / {tot:<6} {pct:5.1f}%")
    db.close()


if __name__ == "__main__":
    main()
