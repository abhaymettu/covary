# covary

**Which variables ask what you mean, and which dataset-years measured them on the
same respondents?**

Two variables can each have n in the thousands in one survey year and a joint n
of exactly zero. There is no error and no warning. The analysis runs, the join
succeeds, and the research design is dead.

```bash
$ python3 covary.py numgiven socfrend
per variable, ignoring co-administration:
  numgiven     gss     n=5819     1985, 1987, 2004, 2024
               number of persons mentioned
  socfrend     gss     n=45294    1974 .. 2024, 29 strata
               spend evening with friends

jointly on the same respondents (min 1):
  gss     1985         n=1526
  gss     2024         n=711

strata that dropped out:
    gss     1974         1 stratum never collected numgiven
    gss     1975         1 stratum never collected numgiven
    gss     1977         1 stratum never collected numgiven
    gss     5 more collected none of them, so they are not about this question
    (24 more; --why for all of them)

  note: gss records an interview `mode` variable in 2024. A joint n does not tell you the mode
  was comparable across those years.
```

`numgiven` and `socfrend` have zero overlap in GSS 2004. They co-occur in 1985
and 2024. Checking one year and generalising is how a viable design gets thrown
away, which is what happened and why this exists.

That is the question this tool was built for. But you cannot ask it until you
know the survey calls the thing you mean `numgiven`, which is a wall everyone
new to a survey hits first, so covary answers that half too:

```bash
$ python3 covary.py --search "how often do you see friends" --dataset gss
  gss     bstvisit       how often does r visit best friend
  gss     frivisit       how often visit closest friend
  gss     newfrds        at these occasions, how often did r make new friends or acquaintences
  gss     bstcall        how often does r call best friend
```

Search finds the name, the index says whether it was measured alongside what
else you need. See [Finding the name in the first place](#finding-the-name-in-the-first-place).

## Why this gap is real

ICPSR's Social Science Variables Database indexes 5M variables across 17k
studies. Its own documentation names this as a limitation, that "a perfect
variable may not have been accompanied in the original data collection by other
variables you need for your intended analysis", and prescribes reading the
codebook by hand. CLOSER Discovery and Maelstrom solve *harmonization*, the same
construct across different studies, which is the orthogonal problem. The
statistics literature knows the condition and treats it as something to model
around after the fact rather than something to discover beforehand.

Surveys break co-occurrence structurally: GSS rotates modules across split
ballots, NHANES splits a cycle across ~130 files with subsamples, BRFSS optional
modules are chosen state by state.

## Install

```bash
git clone <this repo> && cd covary
python3 covary.py numgiven socfrend
```

That is the whole install. The query path is Python standard library only, no
virtualenv and no dependencies. The 132MB index is in the repo.

## Use

```bash
python3 covary.py numgiven talkto1 friend1 --min 300
python3 covary.py RIAGENDR BMXBMI LBXGLU --dataset nhanes
python3 covary.py GUNLOAD ACEDEPRS --dataset brfss
python3 covary.py --find gun --dataset brfss          # half-remembered a name
```

| flag | what it is for |
|---|---|
| `--min N` | minimum joint n per stratum |
| `--min-year N` | minimum joint n for a whole compound group, e.g. a BRFSS year pooled over its states. Per-stratum is the wrong grain when the analyst pools, which is what they do |
| `--why` | also report which variable was absent in each stratum that dropped out, and print each variable's verbatim item wording |
| `--all` | do not truncate the per-group list of strata |
| `--json` | machine-readable, so the tool composes in a pipeline |
| `--find PAT` | list indexed names containing PAT, then exit |
| `--search TEXT` | rank indexed variables by what they ask. `--find` is for a half-remembered name, `--search` is for when you do not know the name at all |

**A zero answers the wrong question, so covary answers the next one too.** When
nothing is usable it reports what dropping one variable would buy you, and why
each stratum dropped out:

```
$ python3 covary.py PREGNANT PROSTATE --dataset brfss
per variable, ignoring co-administration:
  PREGNANT     brfss   n=982726   2011 .. 2023, 689 strata
               PREGNANCY STATUS
  PROSTATE     brfss   n=2480     2011
               EVER TOLD YOU HAD PROSTATE CANCER

jointly on the same respondents (min 1):
  NONE. No respondent is non-missing on all of these in any stratum
  covered by this index at min 1.
  Before concluding these were never asked together: this index
  covers brfss 2011-2023, so an
  earlier or later administration is invisible here. And a
  question skipped by a filter is absent for that reason, not
  because it was left off the instrument. Run with --min 0
  to see strata where all were collected but no one has them all.

  1 stratum collected all of these and still has
  no respondent with all of them.
  1 of those contains a pair with no respondent in common.
  A split ballot and a skip pattern both look exactly like this, so
  the codebook decides which it is.

  best reachable by dropping one variable, at min 1:
    without PROSTATE     brfss 2018|NY n=6685
    without PREGNANT     brfss 2011|HI n=2480

strata that dropped out:
    brfss   2011         52 strata never collected PROSTATE
    brfss   2012         53 strata never collected PROSTATE
    brfss   2013         53 strata never collected PROSTATE
    (10 more; --why for all of them)
```

"The module never ran here" and "both modules ran and no respondent has both" are
different problems with different remedies. BRFSS 2021 is the case: five states
ran `GUNLOAD` without `ACEDEPRS`, eleven ran `ACEDEPRS` without `GUNLOAD`, and none
ran both. That is a fact about that year's module choices, not a gap in the
instrument, and covary used to report the two identically.

Exit status gates a pipeline rather than only informing a human, and the three
cases are distinct so a script can tell them apart:

| exit | meaning |
|---|---|
| 0 | at least one stratum has every variable on the same respondents |
| 1 | none does, at the `--min` you gave |
| 2 | the question cannot be answered as asked: a name was not found or is ambiguous, the named dataset does not exist, the set spans datasets so no stratum could hold it, or a `--find` matched nothing |

As an MCP server, which is the interface that matters since agents walk into
this failure constantly:

```bash
claude mcp add covary -- python3 "$PWD/mcp_server.py"
```

Two tools: `search_variables` goes from a plain-English topic to real variable
names, and `check_covariation` answers whether those names were administered
together. An agent reasoning from a research question rather than from a list of
names uses them in that order, and never has to guess a name.

`skills/covary/SKILL.md` tells an agent to check before designing an analysis.

## Finding the name in the first place

To ask whether two variables were co-administered you have to know the survey
calls the thing you mean `numgiven`. Everyone new to a survey hits that wall
before they hit the co-administration wall, and so does every agent.

```
$ python3 covary.py --search "firearm storage in the home" --dataset brfss
  brfss   FIREARM4       ANY FIREARMS IN HOME
  brfss   FIREARM5       ANY FIREARMS IN HOME
  brfss   GUNLOAD        ANY FIREARMS LOADED
  brfss   LOADULK2       ANY LOADED FIREARMS ALSO UNLOCKED
```

Three different questions, now distinguishable. The one-line description also
prints under every variable in normal output, and `--why` adds the verbatim item
wording where the agency published it.

**The text is the agencies' own, and its quality is theirs too.** GSS ships the
full interviewer script and NORC's subject tags via `gssrdoc`, so GSS search
works well. NHANES publishes a description per variable, and BRFSS publishes
only a terse upper-case SAS label, so BRFSS search is adequate rather than good.
No amount of code fixes that.

| dataset | labelled | source |
|---|---|---|
| GSS | 6,918 / 6,918 | `gssrdoc::gss_doc`, verbatim wording plus subject tags |
| NHANES | 11,935 / 12,388 | CDC per-component variable list |
| BRFSS | 1,037 / 1,037 | SAS label in the XPT header |

**Ranking is a text match and says nothing about co-administration.** Search
tells you which variables are about a topic. Whether they were put to the same
respondents is the other question, and it is the one the index answers.

The text lives in `labels.db`, which is derived, regenerable, and deliberately
not named `covary_*.db`: that glob is what `covary.py` unions into its views and
what `audit.log` records hashes for. A clone without it behaves exactly as covary
did before this existed, because the index is the product and the text is an
enhancement.

```bash
Rscript build_labels.R && python3 pack_labels.py
```

## What it catches

All figures below are all-year marginals as the tool prints them, not single-year
figures. Run the command yourself; the numbers should reproduce exactly.

| | marginal n, all years | joint n | mechanism |
|---|---|---|---|
| GSS `numgiven` x `socfrend` | 5,819 / 45,294 | 0 in 2004, 1,526 in 1985 | split ballot |
| NHANES `RIAGENDR BMXBMI` + `LBXGLU` | 128,809 / 109,407 / 39,753 | 2,842 to 4,659 per stratum | fasting subsample |
| BRFSS `GUNLOAD` x `ACEDEPRS` | 63,744 / 481,178 | 5,337 across 4 of 52 in 2023, plus 920 in 1 of 54 in 2022 | state optional modules |

Each variable's question prints beside it, which is what stops the next mistake
up the chain: `FIREARM5` is whether a firearm is kept in the home, `GUNLOAD` and
`LOADULK2` are the storage items. Confirming two of those were measured together
is no help if the construct you wanted was a third one.

## Coverage

| dataset | strata | variables | presence bits | stratum |
|---|---|---|---|---|
| GSS | 35 | 6,918 | 35.3M | year |
| NHANES | 12 | 12,388 | 160.1M | cycle, 1999-2000 to 2021-2023 |
| BRFSS | 689 | 1,037 | 1.01B | year\|state, 2011-2023 |

A stratum is defined by respondent identity, not by the file a variable arrived
in. NHANES publishes pooled files spanning several cycles, and where CDC keeps the
original SEQNs those respondents are filed under their own cycle rather than the
pooled label. Filing them separately once understated a real joint n by 40x.

covary calls BRFSS strata "states", which is loose: the denominator is 52 to 54
depending on the year and includes DC, Puerto Rico, Guam and the Virgin Islands.

`stratum` is the unit within which co-administration is decided. It is the year
for GSS, the cycle for NHANES, and `year|state` for BRFSS, because a BRFSS module
chosen by 13 states would otherwise look nationally available.

**Presence means non-missing on the actual variable**, not that a file exists. A
respondent who did the NHANES interview but skipped the MEC exam is correctly
absent from exam variables, and the fasting-lab subsample is correctly a fraction
of its cycle.

**The limit of that definition, stated plainly.** Presence is item-level evidence.
A question skipped because of a filter, one only asked of respondents who answered
yes to something earlier, is absent for that reason, and covary cannot tell it from
a question that was never on the instrument. BRFSS `PREGNANT` and `PROSTATE` in
2011 Hawaii were on the same questionnaire, put to the same 7,606 people, and have
a joint n of zero because of a sex filter. Run that pair with `--min 0` and covary reports that a pair of the variables has
no respondent in common at all.

It reports that and stops, because an earlier version went further and was wrong.
It claimed perfect disjointness was a skip-pattern signature "and a split ballot
does not look like that". A split ballot is a partition, so perfect disjointness
is exactly what it looks like, and the heuristic fired on GSS `numgiven` x
`socfrend` in 2004, the example this README opens with, telling the reader those
questions were administered together. They were not. Presence alone cannot
separate the two mechanisms, and the codebook is what does.

**GSS mode, for the same reason.** GSS went multimode in 2021: that year is 293
phone, 218 multimode, 3,521 web and no in-person interviews at all. `mode` is not
part of the stratum, so a 2021 n reads as a like-for-like continuation of a phone
series when it is not. Joint n is still exact, because presence is per respondent.
Comparability across 2018 and 2021 is not, and covary will not tell you so.

## Rebuilding

Only needed to add a survey year. Requires R and a Python venv with duckdb.

```bash
Rscript build_gss.R && Rscript build_nhanes.R && Rscript build_brfss.R
.venv/bin/python pack.py
Rscript audit.R
```

The text layer rebuilds separately and does not need the parquet index:

```bash
Rscript build_labels.R && python3 pack_labels.py
```

It writes `.cache/labels_<dataset>.csv` and packs them into `labels.db`, and it
is resumable per dataset, so delete the CSV for the one you want refetched.
`pack_labels.py` prints coverage against the presence index, which is the only
thing that catches a partially fetched source: that failure builds a `labels.db`
answering some searches while silently missing whole regions of a survey.

`index/` is a 2.0GB parquet layer, one row per (respondent, variable). It is the
readable source of truth that `audit.R` checks against CDC and gssr, and it is
gitignored and deletable. `covary_*.db` is derived from it: one bitmap per
(stratum, variable), where a joint n is a popcount of an AND.

## Data and attribution

The index is derived from three public-use survey series and ships in this repo.

| source | provider | via |
|---|---|---|
| General Social Survey | NORC at the University of Chicago | the `gssr` R package |
| NHANES | CDC / National Center for Health Statistics | public XPT files from wwwn.cdc.gov |
| BRFSS | CDC | public XPT files from cdc.gov/brfss |

**What is in the index, and what is not.** It records only whether a respondent
was non-missing on a variable. One bit. It contains no responses, no values, and
no demographics. That missingness pattern is computable by anyone from the same
public files, so nothing here is derivable from the index that is not already
derivable from the source.

NHANES and BRFSS are works of the United States government. The GSS is
distributed for public research use by NORC and asks to be cited. If you use this
index in published work, cite the underlying surveys, not this repository.

Nothing here is a substitute for the codebooks. covary answers whether variables
were measured together. It says nothing about what they mean, how they were
weighted, or whether they are comparable across years.

## Auditing

```bash
Rscript audit.R              # re-reads source files and recompares
Rscript audit.R 6 40 2023    # also verify BRFSS 2023 against CDC
```

The audit re-reads the original sources rather than checking the index against
itself, because a wrong index is still perfectly self-consistent. `full` mode
re-downloads everything and re-derives every count from the source files: every
GSS variable, every NHANES table in the manifest that has a respondent id, every
BRFSS variable in all thirteen years, and every bitmap. It prints the counts it
actually reached, and this page does not repeat them, because a number written in
prose drifts away from the code that produces it. That happened here: the table
count was recorded as 1,597 in one file and 1,544 in another, describing the same
run.

Three silent bugs were found this way and are written up in `DECISIONS.md`. The one
worth naming: a reader applied value-label translation, so any code without a
codebook entry came back as NA, understating presence by up to 7.7% on
questionnaire variables. Every headline number stayed correct, because those
happened to use continuous variables.

**The evidence ships, so you do not have to take this on faith.** `index/` is
gitignored because it is 2GB, so a fresh clone needs a rebuild before it can run
`audit.R` or `pack.py --verify`. The NHANES manifest that a rebuild needs is
committed, so the rebuild is a matter of time rather than of guessing what to
fetch. `audit.log` is written by `Rscript audit.R full` itself: the full run output,
dated, with the sha256 of each db file it verified at the top. It used to be
assembled by hand, which meant this page described an artifact no code produced
and the link between the log and the shipped files rested on whoever pasted the
hashes. Check that what you cloned is what was audited:

```bash
shasum -a 256 covary_*.db && head -12 audit.log
```

The audit has since had four bugs of its own, which is more than the index had.
Each looked exactly like a data bug. That is the argument for exhaustive mode and
for `fips.R`, which exists so the builder and the audit cannot disagree about the
state map. A copy of that map in both files had already caused one false audit
failure.
