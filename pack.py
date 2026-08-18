#!/usr/bin/env python3
"""Pack the parquet index into covary.db, one bitmap per (stratum, variable).

Presence is one bit. Storing it as a row with a repeated string unit_id costs
about 160 bytes a bit, which is how the parquet index reached 2.5GB and stopped
being something anyone would download. The bits themselves are 212MB, and they
compress well because module structure is blocky.

So this is not a compression trick, it is the representation the index should
have had. build_index.R said so in its first comment: "one bit per respondent
per variable ... any joint-n query is an AND over two bit vectors".

SQLite rather than a bespoke binary file, because a hand-rolled format is the
easiest place to be subtly and silently wrong, and this project has already lost
two rounds to silent wrongness. sqlite3 and zlib are both stdlib, so the query
path needs nothing installed.

The parquet index stays the reproducible source of truth. covary.db is derived
from it and is checked against it by audit.R.

  .venv/bin/python pack.py            # all datasets
  .venv/bin/python pack.py gss        # one, for a quick test

One file per dataset, covary_<dataset>.db. A single file would be 131MB, over
GitHub's 100MB limit, but the split earns its keep anyway: someone who only wants
GSS takes 10MB instead of 131MB, and a rebuilt dataset does not rewrite the
others.

Layout:
  bm(dataset, stratum, variable, pop, bits)   bits = zlib(little-endian bitmap)
  strata(dataset, stratum, n_units)           denominator for "n of N"

Bit positions are assigned per stratum by sorting unit_id, so they are stable
for a given parquet index but carry no meaning outside their own stratum. Nothing
reads unit_id back, only counts, so the roster itself is not stored.
"""
import glob, os, sqlite3, sys, zlib
import duckdb  # build time only, the query path is stdlib

HERE = os.path.dirname(os.path.abspath(__file__))
IDX = os.path.join(HERE, "index", "*.parquet")
DB = os.path.join(HERE, "covary_{}.db")  # one per dataset


def pack(only=None):
    con = duckdb.connect()
    con.execute("set memory_limit='6GB'")

    where = "where dataset = ?" if only else ""
    args = [IDX] + ([only] if only else [])
    strata = con.execute(
        f"select distinct dataset, stratum from read_parquet(?) {where} order by 1, 2",
        args).fetchall()
    print(f"{len(strata)} strata")

    dbs = {}

    def dbfor(ds):
        if ds not in dbs:
            path = DB.format(ds)
            if os.path.exists(path):
                os.remove(path)
            d = sqlite3.connect(path)
            d.executescript("""
              pragma journal_mode=off; pragma synchronous=off;
              create table bm(dataset text, stratum text, variable text, pop int, bits blob);
              create table strata(dataset text, stratum text, n_units int);
            """)
            dbs[ds] = d
        return dbs[ds]

    total_bits = 0
    for i, (ds, st) in enumerate(strata, 1):
        # Bit position per respondent, assigned within this stratum only.
        rows = con.execute("""
          with s as (select * from read_parquet(?) where dataset = ? and stratum = ?),
               r as (select unit_id, cast(row_number() over (order by unit_id) - 1 as int) pos
                     from (select distinct unit_id from s))
          select s.variable, r.pos from s join r on s.unit_id = r.unit_id
          order by s.variable
        """, [IDX, ds, st]).fetchall()
        if not rows:
            continue

        n_units = max(p for _, p in rows) + 1
        nbytes = (n_units + 7) // 8

        cur_var, buf, pop = None, None, 0
        out = []
        for var, pos in rows:
            if var != cur_var:
                if cur_var is not None:
                    out.append((ds, st, cur_var, pop, zlib.compress(bytes(buf), 6)))
                cur_var, buf, pop = var, bytearray(nbytes), 0
            buf[pos >> 3] |= 1 << (pos & 7)
            pop += 1
        out.append((ds, st, cur_var, pop, zlib.compress(bytes(buf), 6)))

        db = dbfor(ds)
        db.executemany("insert into bm values (?,?,?,?,?)", out)
        db.execute("insert into strata values (?,?,?)", (ds, st, n_units))
        total_bits += len(rows)
        if i % 25 == 0 or i == len(strata):
            db.commit()
            print(f"  {i}/{len(strata)} {ds} {st}  {len(out)} bitmaps", flush=True)

    for ds, db in dbs.items():
        db.commit()
        # Only an index on variable: every query filters by variable first, and a
        # second index cost 10MB to serve no query.
        db.execute("create index bm_var on bm(variable)")
        db.execute("vacuum")
        db.close()
        p = DB.format(ds)
        print(f"wrote {p}: {os.path.getsize(p)/1e6:.1f} MB")
    print(f"{total_bits:,} bits total")


def verify(n_pairs=40, seed=0):
    """Does the packed db answer the same as the parquet index it came from?

    covary.db is derived, and a derivation is exactly where this project has
    already been silently wrong twice.

    Two of these three checks are EXHAUSTIVE, deliberately. A first version
    sampled random variable pairs and missed a single flipped bit, because 40
    pairs out of 6,918 GSS variables covers about 0.02% of them. Sampling finds
    systematic breakage and misses local breakage, so the cheap checks cover
    everything and only the expensive one samples.

      1. every bitmap: popcount(decompress(bits)) == stored pop   [exhaustive]
      2. every bitmap: stored pop == the parquet count            [exhaustive]
      3. sampled variable pairs: joint n matches parquet          [sampled]

    Check 3 is the only one that exercises the AND, which is why it survives.
    """
    import random, zlib as _z
    rng = random.Random(seed)
    con = duckdb.connect(); con.execute("set memory_limit='6GB'")
    bad = 0

    for path in sorted(glob.glob(DB.format("*"))):
        ds = os.path.basename(path)[7:-3]
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)

        # 1. every stored bitmap agrees with its own popcount
        n_bm = 0
        for st, v, pop, bits in db.execute("select stratum, variable, pop, bits from bm"):
            n_bm += 1
            if int.from_bytes(_z.decompress(bits), "little").bit_count() != pop:
                bad += 1
                print(f"  CORRUPT {ds} {st} {v}: bits disagree with stored pop")
        print(f"{ds}: {n_bm:,} bitmaps self-consistent")

        # 2. every stored pop agrees with the parquet index
        pq = con.execute(
            "select stratum, variable, count(*) from read_parquet(?) where dataset = ?"
            " group by 1, 2", [IDX, ds]).fetchall()
        pq_d = {(st, v): n for st, v, n in pq}
        bm_d = {(st, v): p for st, v, p in
                db.execute("select stratum, variable, pop from bm")}
        if bm_d != pq_d:
            for k in sorted(set(bm_d) | set(pq_d)):
                if bm_d.get(k) != pq_d.get(k):
                    bad += 1
                    print(f"  MISMATCH {ds} {k}: bitmap={bm_d.get(k)} parquet={pq_d.get(k)}")
        print(f"{ds}: {len(pq_d):,} popcounts match parquet")

        # 3. the AND itself, on sampled pairs
        vars_ = [r[0] for r in db.execute("select distinct variable from bm")]
        for _ in range(n_pairs):
            a, b = rng.sample(vars_, 2)
            got = {}
            for st, v, bits in db.execute(
                    "select stratum, variable, bits from bm where variable in (?,?)", (a, b)):
                got.setdefault(st, {})[v] = bits
            bm_ans = {}
            for st, d in got.items():
                if len(d) < 2:
                    continue
                n = (int.from_bytes(_z.decompress(d[a]), "little")
                     & int.from_bytes(_z.decompress(d[b]), "little")).bit_count()
                if n:
                    bm_ans[st] = n
            pq_ans = dict(con.execute("""
              select stratum, count(*) from (
                select stratum, unit_id from read_parquet(?)
                where dataset = ? and variable in (?, ?)
                group by stratum, unit_id having count(distinct variable) = 2)
              group by stratum
            """, [IDX, ds, a, b]).fetchall())
            if bm_ans != pq_ans:
                bad += 1
                print(f"  MISMATCH joint {ds} {a} x {b}")
        print(f"{ds}: {n_pairs} sampled joint-n queries match parquet")
        db.close()

    print("FAILURES:" if bad else "packed db agrees with the parquet index", bad or "")
    return 1 if bad else 0


if __name__ == "__main__":
    if "--verify" in sys.argv:
        sys.exit(verify())
    if not glob.glob(IDX):
        sys.exit("no parquet index to pack")
    pack(sys.argv[1] if len(sys.argv) > 1 else None)
