# Decisions

Why this is built the way it is, written as the choices were made.

## Why the question is worth an index

I checked whether this already existed before building it.

**ICPSR SSVD** indexes 5M variables across 17k studies. Its documentation names
this exact gap as a limitation: "a perfect variable may not have been accompanied
in the original data collection by other variables you need for your intended
analysis", and prescribes reading the codebook by hand.

**CLOSER Discovery** (8 UK cohorts, DDI-Lifecycle) and **Maelstrom Mica/Opal**
both solve harmonization: the same construct measured differently across studies.
That is the orthogonal problem. Neither answers co-administration within a study.

The statistics literature knows the condition, "variables never jointly
observed", but treats it as a modelling problem: statistical matching, fractional
imputation, pooling incomplete datasets. The field built remediation, not
discovery.

So the contribution is narrow on purpose. Not a dataset search engine, not
harmonization. One question: were these measured on the same respondents.

## Schema

```
dataset   "gss" | "nhanes" | "brfss"
stratum   the unit within which co-administration is decided
unit_id   respondent identity, unique within (dataset, stratum)
variable
```

`stratum` is a free-form string rather than a year, and it is the only real
design decision in the schema. It is the year for GSS, the cycle for NHANES, and
`year|state` for BRFSS. Making it a string means `covary.py` never has to know
what a stratum means for a given dataset.

## Presence means non-missing on the actual variable

Not "the file exists". A respondent who completed the NHANES interview but
skipped the MEC exam is absent from exam variables, and the fasting-lab subsample
is a fraction of its cycle. Defining presence per variable handles both without a
weights table.

This is only correct if NA means "not administered" while a refusal is a real
value. NHANES and BRFSS keep refusals as codes (7/9, 77/99) and leave skips
blank, so non-missing is exactly "this question was put to this respondent". GSS
needed checking because `gssr` maps sentinels to NA itself; every sentinel label,
`iap` included, resolves to NA. `audit.R` asserts this rather than assuming it,
so the assumption breaks loudly if `gssr` ever changes.

## NHANES

- **All continuous cycles**, 1999-2000 through 2021-2023. The question people
  bring is "which cycle can I use", so truncating to recent cycles removes the
  answer.
- **Dietary excluded.** `DR*` and `DS*` files are day-level or supplement-level
  with several rows per SEQN, so presence there would not mean what it means
  everywhere else. CDC's dietary listing does not cover 2021-2023, so there is a
  `^(DR|DS)` prefix backstop alongside it.
- **Pooled files keep their own span.** `2017-2020` pre-pandemic and `1999-2004`
  are separate strata, not folded into a two-year cycle. They are a different
  respondent set, and folding them would overstate joint n.
- **A cycle with any unfetchable table is not written.** See the bugs below.
- Tables with no `SEQN` are skipped: 33 of them, all pooled-sample lab files keyed
  on `SAMPLEID`/`POOLID` where one row is a pool of many people, plus food, drug
  and variable code lookups. A pooled sample has no individual respondent, so it
  cannot answer a co-administration question.

## BRFSS

BRFSS is why `stratum` is a string. Its optional modules are chosen state by
state, so a variable can be nationally absent and locally universal in the same
year.

- **`stratum` is `"<year>|<state>"`, `unit_id` is `SEQNO`.** `SEQNO` is not unique
  within a year, only within a state. A year-level stratum would have silently
  merged different respondents from different states into one, which is a
  correctness bug, not just a reporting one.
- **`QSTVER` is deliberately not in the stratum.** Presence is per respondent, so
  a joint n is exact at any grain. Version would triple the strata to describe an
  implementation detail inside a state rather than a policy difference between
  states.
- **2011 onward.** Pre-2011 404s on the `LLCP<year>XPT.zip` pattern and uses
  different naming and weighting.
- **Compound strata roll up in the display.** Otherwise a core-variable query
  prints hundreds of lines. "4 of 52 states" is the actionable answer anyway.
- CDC ships the file with a trailing space in the name (`LLCP2023.XPT `), so the
  builder matches loosely.

## Two storage layers

```
index/        2.0GB parquet, one row per (respondent, variable). Reproducible
              from source, checked by audit.R, gitignored, deletable.
covary_*.db   129MB, one bitmap per (stratum, variable). Derived from index/.
              This is the shipped artifact.
```

Presence is one bit. Storing it as a row with a repeated string `unit_id` costs
about 160 bytes a bit, which is how the parquet index reached 2.0GB and stopped
being something anyone would download. A 2.0GB build is the same hassle this tool
exists to remove, wearing a different hat.

SQLite rather than a bespoke binary file, because a hand-rolled format is the
easiest place to be silently wrong. `sqlite3` and `zlib` are standard library, so
the query path installs nothing. One file per dataset because a single file was
131MB, over GitHub's 100MB limit, and because someone who only wants GSS should
take 7.7MB rather than 129MB.

A joint n is `popcount(bitmap_a & bitmap_b)`. A three-variable BRFSS query across
690 strata and 5.4M respondents answers in 0.036s.

## Auditing

The audit re-reads the original source files and recomputes presence. It does not
check the index against itself, because a wrong index is still perfectly
self-consistent. Every bug below was silent, and none would have been caught by
any check that read only the index.

Sampled mode is for routine use. `Rscript audit.R full` re-downloads everything
and checks every variable, and is what runs before publishing or after any change
to a builder.

Sampling finds systematic breakage and misses local breakage. The first version
of `pack.py --verify` sampled 40 variable pairs out of 6,918 GSS variables, which
is 0.02% of them, and missed a single flipped bit. Two of its three checks are
now exhaustive across all 222,373 bitmaps.

## Bugs found by auditing, all silent

**NHANES dropped files.** CDC failed to serve two real 2005-2006 questionnaire
files. The build wrote the cycle anyway, losing 299,301 pairs, and the resume
logic would then have skipped that cycle forever. Now a cycle with any
unfetchable table is not written at all.

**NHANES translation nulling.** The `nhanesA` reader applies value-label
translation, and a coded value with no matching codebook entry comes back as NA.
`SMQ_J` lost 7.7% of its presence, `DIQ_J` 3.2%: 2,175,059 pairs and one entire
variable. No error, no log line, and numbers that were internally consistent and
plausible.

Two things made it disqualifying rather than annoying:

1. **It ran the wrong direction.** Understated presence means reporting a design
   as underpowered when it is not, and failing a `--min` gate that should pass.
   The pitch here is "trust this instead of the codebook", so being quietly
   pessimistic converts a known unknown into a false certainty.
2. **The demo queries were blind to it.** `RIAGENDR`, `BMXBMI` and `LBXGLU` are
   continuous, so every headline number stayed correct while questionnaire
   variables were short. The validation looked perfect because it never touched
   the broken path.

Fix: read the XPT raw with `haven::read_xpt`. Presence does not need labels, so
it does not need translation.

**The audit's own bug.** It compared one year of BRFSS source against thirteen
years of index, having filtered by variable but never by year, so everything
appearing in more than one year failed. Worth recording because it looked exactly
like a data bug, and the instinct to trust the audit over the data would have
been wrong.

## Deliberately not doing

- **ATUS.** bls.gov returns 403 behind Akamai even with a browser user agent.
  Cut rather than worked around.
- **A question-level semantic layer**, matching variables by what they ask rather
  than by name. It sits on top of this index, and building it first is the wrong
  order.
- **Harmonization.** CLOSER and Maelstrom own that and are well funded. The
  contribution here is co-administration.
