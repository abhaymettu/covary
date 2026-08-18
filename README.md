# covary

**Given the variables an analysis needs, which dataset-years measured them on the
same respondents?**

Two variables can each have n in the thousands in one survey year and a joint n
of exactly zero. There is no error and no warning. The analysis runs, the join
succeeds, and the research design is dead.

```bash
$ python3 covary.py numgiven socfrend
per variable, ignoring co-administration:
  numgiven     gss     n=5819     1985 .. 2024, 4 strata
  socfrend     gss     n=45294    1974 .. 2024, 29 strata

jointly on the same respondents (min 1):
  gss     1985         n=1526
  gss     2024         n=711
```

`numgiven` and `socfrend` have zero overlap in GSS 2004. They co-occur in 1985
and 2024. Checking one year and generalising is how a viable design gets thrown
away, which is what happened and why this exists.

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
virtualenv and no dependencies. The 129MB index is in the repo.

## Use

```bash
python3 covary.py numgiven talkto1 friend1 --min 300
python3 covary.py RIAGENDR BMXBMI LBXGLU --dataset nhanes
python3 covary.py FIREARM5 ACEDEPRS --dataset brfss
```

Exit status is 1 when no stratum supports the full set, so it gates a pipeline
rather than only informing a human.

As an MCP server, which is the interface that matters since agents walk into
this failure constantly:

```bash
claude mcp add covary -- python3 "$PWD/mcp_server.py"
```

`skills/covary/SKILL.md` tells an agent to check before designing an analysis.

## Coverage

| dataset | strata | variables | presence bits | stratum |
|---|---|---|---|---|
| GSS | 35 | 6,918 | 35.3M | year |
| NHANES | 14 | 11,213 | 122.6M | cycle, 1999-2000 to 2021-2023 |
| BRFSS | 690 | 1,037 | 1.01B | year\|state, 2011-2023 |

`stratum` is the unit within which co-administration is decided. It is the year
for GSS, the cycle for NHANES, and `year|state` for BRFSS, because a BRFSS module
chosen by 13 states would otherwise look nationally available.

**Presence means non-missing on the actual variable**, not that a file exists. A
respondent who did the NHANES interview but skipped the MEC exam is correctly
absent from exam variables, and the fasting-lab subsample is correctly a fraction
of its cycle.

## What it catches

| | marginal n | joint n | mechanism |
|---|---|---|---|
| GSS `numgiven` x `socfrend` | 5,819 / 45,294 | 0 in 2004 | split ballot |
| NHANES + `LBXGLU` | ~8,000 / cycle | ~3,000 | fasting subsample |
| BRFSS `FIREARM5` x `ACEDEPRS` | 81,310 / 56,040 | 4 of 52 states | state optional modules |

## Rebuilding

Only needed to add a survey year. Requires R and a Python venv with duckdb.

```bash
Rscript build_gss.R && Rscript build_nhanes.R && Rscript build_brfss.R
.venv/bin/python pack.py
Rscript audit.R
```

`index/` is a 2.0GB parquet layer, one row per (respondent, variable). It is the
readable source of truth that `audit.R` checks against CDC and gssr, and it is
gitignored and deletable. `covary_*.db` is derived from it: one bitmap per
(stratum, variable), where a joint n is a popcount of an AND.

## Auditing

```bash
Rscript audit.R              # re-reads source files and recompares
Rscript audit.R 6 40 2023    # also verify BRFSS 2023 against CDC
```

The audit re-reads the original sources rather than checking the index against
itself, because a wrong index is still perfectly self-consistent. Three real
bugs, all silent, were found this way and are documented in `HANDOFF.md`. The
one worth naming: a reader that applied value-label translation returned NA for
any code without a codebook entry, understating presence by up to 7.7% on
questionnaire variables while every headline number stayed correct, because those
happened to use continuous variables.
