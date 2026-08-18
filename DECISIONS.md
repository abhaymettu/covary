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

NHANES and BRFSS keep refusals as codes (7/9, 77/99) and leave skips blank. GSS
needed checking because `gssr` maps sentinels to NA itself; every sentinel label,
`iap` included, resolves to NA. `audit.R` asserts this rather than assuming it, so
the assumption breaks loudly if `gssr` ever changes.

**Where that definition stops, corrected 2026-08-18.** An earlier version of this
file claimed non-missing is exactly "this question was put to this respondent".
That does not follow, and user testing produced the counterexample: BRFSS
`PREGNANT` and `PROSTATE`, 2011 Hawaii, same instrument and same 7,606 people,
joint n of zero because of a sex filter. A respondent routed past an item by their
own earlier answer WAS administered the module. Presence is item-level evidence and
the claims built on it were design-level.

The error runs pessimistic, which is the direction this file already calls
disqualifying in the translation-bug write-up below. Two things changed rather than
one: the wording everywhere now says what the index can support, and `joint()`
detects the filter signature. Within a stratum where every requested variable was
collected, a joint n of zero with `popcount(a|b) == pop_a + pop_b` means no
respondent appears in more than one of them.

**That is all it means, corrected again 2026-08-18.** The first version of this
paragraph said perfect disjointness was "a skip pattern and not a split ballot".
That is backwards. A split ballot partitions respondents, so perfect disjointness
is its definition, and the check fired on GSS `numgiven` x `socfrend` in 2004 and
announced that those questions WERE administered together. Version one of this
tool was wrong pessimistically and said so in its output. Version two was wrong
optimistically and did not, which is worse, because nothing downstream contradicts
a confident yes. The check now reports the observation and names both mechanisms
as possible. The comparison is also pairwise rather than mutual, since requiring
every variable to be disjoint from every other meant adding one ordinary covariate
silently deleted the finding.

## NHANES

- **All continuous cycles**, 1999-2000 through 2021-2023. The question people
  bring is "which cycle can I use", so truncating to recent cycles removes the
  answer.
- **Dietary is now included, and my original reason for excluding it was wrong.**
  I excluded `DR*` and `DS*` because they carry several rows per SEQN and I
  assumed presence would not mean the same thing there. The exhaustive audit
  showed that reasoning does not hold: NHANES has plenty of multi-row tables
  outside dietary, with audiometry (`AUXAR_*`) at about 12 rows per respondent,
  and the index handles them correctly because presence dedups by respondent.
  So the exclusion was never principled, only untested, and `nhanes_scope.R`
  existed to keep two copies of a rule that should not have existed at all. It
  is deleted. Nothing is now excluded by table name; a table with no `SEQN` is
  still skipped, because there is no respondent to be present.
- **Pooled files are filed by respondent identity, corrected 2026-08-18.** The
  original rule was "pooled files keep their own span, they are a different
  respondent set, and folding them would overstate joint n". That was true of one
  pooled file and false of the others, and I generalised from the one I checked,
  which is the mistake this whole tool exists to catch.

  Measured across the index: `1999-2004` has 22,284 respondents and all 22,284 of
  them are already in 1999-2000, 2001-2002 or 2003-2004. `2007-2012` likewise, all
  380. CDC keeps the original SEQNs for those. Only `2017-2020` is renumbered, and
  it shares zero identifiers with `2017-2018`.

  Filing a shared-SEQN respondent under the pooled label put them in a stratum
  nothing else could reach, because bit positions are assigned per stratum and no
  AND can cross that boundary. `SSALB x RIAGENDR` reported a joint n of 539 against
  a true 21,846 and exited 0. A 40x understatement, in the pessimistic direction
  this file calls disqualifying, on exactly the rare-biomarker-by-demographics
  design that would send someone to this tool in the first place. 85 NHANES
  variables exist only in pooled files.

  So a respondent's stratum is now the cycle whose own files contain that SEQN, and
  a pooled file contributes its variables to whichever cycle each respondent
  already belongs to. This is exact, not an approximation: the SEQNs are the same
  people. Where no cycle claims the SEQN, as with the renumbered pre-pandemic file,
  the pooled span correctly stays its own stratum. `NHANES_POOLED_PARTS` in `nhanes_strata.R` is the rule, and it is genuinely
  shared: the builder rehomes with it, the audit sums across the same cycles.
  An earlier version of that file also carried a `nhanes_rehome()` helper and this
  paragraph said the rule was shared. Nothing called it, the builder had its own
  inline copy, and the two had already drifted. Deleted. That is the third time a
  document here described code that did not run, after the hand-assembled
  `audit.log` and the retracted sentence left in `audit.R`, so it is worth naming
  as a pattern rather than a slip.

  One consequence worth stating: presence is correct after rehoming, but a
  variable from a six-year pooled specimen file now carries a two-year cycle
  label, and its sample design is still the six-year pool. covary answers
  co-administration, not whether a cycle-level analysis of that variable is
  sound.

  `2017-2020` still describes some of the same physical people as `2017-2018` under
  identifiers that cannot be matched, so the marginal counts them twice and the two
  must never be pooled. Presence cannot detect that, so covary warns when both
  appear in one result. A warning is the honest limit here.
- **A cycle with any unfetchable table is not written.** See the bugs below.
- Tables with no `SEQN` are skipped. They are pooled-sample lab files keyed on
  `SAMPLEID` or `POOLID`, where one row is a pool of many people, plus food, drug
  and variable code lookups. A pooled sample has no individual respondent, so it
  cannot answer a co-administration question. The count is deliberately not written
  down here: it was recorded as 33, adding dietary brought more lookup tables in,
  and a hardcoded count in prose drifts silently away from the code. `audit.R`
  reports how many of the manifest's tables it actually checked on every run, which
  is the number that cannot go stale.

- **GSS `mode` is documented, not indexed.** GSS went multimode in 2021: that
  year is 293 phone, 218 multimode, 3,521 web and no in-person at all, and 2022
  and 2024 are mixed. A 2021 n therefore reads as a like-for-like continuation of
  a phone series when it is not. It stays out of the stratum for the same reason
  BRFSS `QSTVER` does: presence is per respondent, so a joint n is exact at any
  grain, and mode is an implementation detail inside a year rather than the thing
  that decides what was collected there. What mode changes is comparability, and
  comparability is codebook territory this tool already disclaims.

  One consequence is worth stating because it runs in covary's favour and was not
  designed in: gssr maps `skipped on web` to NA along with `iap`, so an item a
  web respondent was never shown is correctly absent for that respondent rather
  than silently present. The audit now asserts that sentinel specifically, since
  the whole GSS presence definition rests on it.

- **`ballot` is not in the stratum either**, though split ballots are the
  mechanism covary exists to catch. It does not need to be: a joint n is a
  popcount over respondents, so a ballot split shows up as the zero it is without
  the stratum having to name it. Naming it would triple GSS strata to restate
  what the number already says.

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
- **The FIPS state map lives in `fips.R`**, sourced by the builder and the audit.
  It used to be pasted into both, which is the same drift that caused one of the
  audit bugs below.

## Two storage layers

```
index/        2.0GB parquet, one row per (respondent, variable). Reproducible
              from source, checked by audit.R, gitignored, deletable.
covary_*.db   132MB, one bitmap per (stratum, variable). Derived from index/.
              This is the shipped artifact.
```

Presence is one bit. Storing it as a row with a repeated string `unit_id` costs
about 160 bytes a bit, which is how the parquet index reached 2.0GB and stopped
being something anyone would download. A 2.0GB build is the same hassle this tool
exists to remove, wearing a different hat.

SQLite rather than a bespoke binary file, because a hand-rolled format is the
easiest place to be silently wrong. `sqlite3` and `zlib` are standard library, so
the query path installs nothing. One file per dataset because a single file was
131MB at the time, over GitHub's 100MB limit, and because someone who only wants
GSS takes 7.7MB rather than the whole 132MB.

A joint n is `popcount(bitmap_a & bitmap_b)`. A three-variable BRFSS query across
689 strata and 5.9M respondents answers in 0.036s.

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
now exhaustive across every bitmap.

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
   Being quietly pessimistic converts a known unknown into a false certainty,
   which is worse than no tool.
2. **The demo queries were blind to it.** `RIAGENDR`, `BMXBMI` and `LBXGLU` are
   continuous, so every headline number stayed correct while questionnaire
   variables were short. The validation looked perfect because it never touched
   the broken path.

Fix: read the XPT raw with `haven::read_xpt`. Presence does not need labels, so
it does not need translation.

**A whole NHANES rebuild that produced nothing, and said so cheerfully.** Found
2026-08-18 while adding dietary. `mclapply` forks a child per table, and macOS
kills any forked child once the Objective-C runtime has initialized, which loading
`arrow` and `haven` does. Every child died. `mclapply` returns `NULL` for a dead
child, and `NULL` was also `one_table`'s way of saying "this table has no
respondent id, skip it", so 1,597 dead children read as 1,597 empty tables and
every cycle was skipped with "nothing usable". Exit status 0.

Two fixes, and the second is the one that matters. The build re-execs itself once
with `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES`, which is the workaround. And
`NULL` is no longer a legal return: "no respondent id" is now `"SKIP"`, so
anything `NULL` is a crashed child and is fatal for the cycle, like an unfetchable
table already was. The lesson is not about macOS. It is that a sentinel meaning
"nothing here, carry on" must never be the same value a crash produces.

**The audit's own bugs, of which there were more than the index had.** Worth
recording plainly, because both looked exactly like data bugs and the instinct to
trust the audit over the data would have been wrong twice.

1. It compared one year of BRFSS source against thirteen years of index, having
   filtered by variable but never by year, so everything appearing in more than
   one year failed.
2. It compared raw row counts against the index's distinct-respondent counts, so
   every NHANES table with repeated rows per SEQN reported a phantom undercount.
   This one only surfaced in exhaustive mode; the sampled runs had never drawn
   one of those tables.

The second is the reason exhaustive mode exists. It also corrected a factual
belief I had written down about the dietary files, above.

## What a zero is allowed to mean

A NONE is a verdict, and for a long time it was the last thing covary said. That
is the wrong place to stop: the analyst's next question is never "is it dead", it
is "what do I give up". So a NONE now carries three more things, all computed from
bitmaps that were already in memory.

- **Leave one out.** The best stratum reachable by dropping each single variable,
  best drop first. Single drops only. Every subset is 2^n answers nobody reads,
  and in practice one variable carries the whole failure.
- **Absence, attributed.** Which requested variable was never collected in each
  stratum that dropped out, rolled up per year. BRFSS 2021 is the case that made
  this necessary: five states ran `GUNLOAD` without `ACEDEPRS`, eleven ran
  `ACEDEPRS` without `GUNLOAD`, none ran both. "The module never ran here" and
  "both ran and no respondent has both" have different remedies, and covary used
  to print them identically.
- **Collected but disjoint, counted separately** from absent, using the filter
  signature above.

Strata where none of the requested variables were collected are counted in one
line rather than listed. A stratum with none of them is not about the question.

## Grain

`--min` thresholds a single stratum. That is the wrong grain for BRFSS, where the
analyst pools states within a year and would have kept a small state that a
per-stratum threshold discards. `--min-year` thresholds the rolled-up group
instead. Both exist because both are real: a state fixed effect needs the
per-stratum number, a pooled estimate needs the group total.

## Deliberately not doing

- **ATUS.** bls.gov returns 403 behind Akamai even with a browser user agent.
  Cut rather than worked around.
- ~~**A question-level semantic layer**, matching variables by what they ask
  rather than by name.~~ **Built 2026-08-18, in a much smaller form than the one
  parked.** The parked version assumed covary would have to do the semantics, and
  deferred it as the unsolved research problem in this space. It is not covary's
  problem. The consumer of this output is an agent, or a researcher with an LLM
  open in the next tab, and either performs the question-to-variable step itself
  given searchable text. What was missing was an asset, not a model: the index
  stored names and bits and no text at all. So the layer is a `labels` table
  from each agency's own published wording, plus FTS5. See
  `docs/superpowers/specs/2026-08-18-covary-labels-design.md`.

  What stays out of scope for the same reason it always was: covary does not
  answer research questions. It surfaces which variables are about a topic and
  which strata measured them together. The inferential step stays with the
  researcher.

  Labels are never evidence about co-administration. Presence bits remain the
  only source of truth for a joint n.
- **Harmonization.** CLOSER and Maelstrom own that and are well funded. The
  contribution here is co-administration.
