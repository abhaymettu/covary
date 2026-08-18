---
name: covary
description: Use BEFORE designing, coding, or estimating any analysis on GSS, NHANES, or BRFSS, and before claiming a research design is feasible or dead. Checks whether the variables a design needs were administered to the same respondents. Also use when a sample size looks surprisingly small, when a merge or join across survey files yields fewer rows than expected, or when deciding which survey year, cycle, or state to analyse.
---

# covary

## The problem this prevents

Two variables can each have n in the thousands in a survey year and a joint n of
exactly **zero**. There is no error and no warning. The analysis runs, the join
succeeds, and the design is dead.

This happens by design, not by accident:

- **GSS** rotates modules across split ballots. A respondent gets ballot A or B,
  never both.
- **NHANES** splits a cycle across ~130 component files, and subsamples (fasting
  labs, DXA) cover a fraction of the cycle. A respondent can complete the
  interview and never attend the MEC exam.
- **BRFSS** optional modules are chosen state by state. A module with n in the tens
  of thousands nationally may have run in a handful of states.

ICPSR's own variable database documents this as a limitation and tells you to
read the codebook by hand. covary answers it from the data instead.

## When to run it

Run it **before** writing analysis code, not after a sample size looks wrong.

- Any new analysis on GSS, NHANES, or BRFSS
- Before saying a design is feasible OR infeasible. Both claims need checking:
  one year having zero overlap does not mean every year does.
- Choosing which year, cycle, or state to use
- A joint sample size came out smaller than expected

## How to run it

If the `covary` MCP server is available, call `check_covariation` with the
variable names and optionally a `dataset` and `min_n`.

Otherwise use the CLI, which needs no dependencies:

```bash
python3 /path/to/covary/covary.py numgiven socfrend
python3 /path/to/covary/covary.py RIAGENDR BMXBMI LBXGLU --dataset nhanes
python3 /path/to/covary/covary.py GUNLOAD ACEDEPRS --dataset brfss --min 500
python3 /path/to/covary/covary.py --find gun --dataset brfss     # search names
python3 /path/to/covary/covary.py DR1TKCAL BMXBMI --dataset nhanes --json
```

Exit status is 1 when no stratum supports the whole set, so it gates a pipeline.

## How to read the answer

The **joint n** is the count of respondents who are non-missing on *every*
requested variable within one stratum. That is the real n the design has.

- **A stratum is listed** with joint n at or above what the design needs: usable.
  Restrict the analysis to those strata and say so explicitly.
- **NOT USABLE / NONE**: no respondent in the index is non-missing on all of them.
  Usually the design is not estimable as stated, so do not quietly proceed. But do
  not declare it dead before reading the notes covary returns with the result. Three
  things produce an empty answer and only one of them is fatal: the variables may
  co-occur below the `min_n` you passed, they may have been asked outside the
  indexed years (GSS 1972-2024, NHANES 1999-2023, BRFSS 2011-2023), or they may be
  separated by a skip pattern rather than by design. covary flags the last case when
  it can detect it.
- **What an empty answer now comes with**, and it is usually the actionable part.
  covary reports what dropping each single variable would buy ("without PROSTATE,
  brfss 2018|NY n=6685"), which is the decision actually in front of you. It also
  names which variable was absent in each stratum that dropped out, so "this module
  never ran here" is distinguishable from "both ran and no respondent has both".
  Report those to the user rather than only the verdict.
- **The marginal n is much larger than the joint n**: normal and important. Base
  every power calculation and every reported n on the joint figure.

For BRFSS, the answer is per `year|state`. "4 of 52 states" is not a footnote: a
result resting on four self-selected states cannot support a national claim, and
a state fixed effect has almost nothing to estimate from. `--min` thresholds one
state-year; if the plan is to pool states within a year, which is the usual plan,
use `--min-year` instead, because a per-stratum threshold discards small states
the analysis would have kept.

## Do not

- Do not infer co-administration from a variable appearing in a codebook or a
  file. Presence in a file is not presence on a respondent.
- Do not check one year and generalise to the dataset. That specific mistake is
  what caused this tool to be written.
- Do not treat covary as a substitute for weights, censoring, or sentinel-code
  handling. It answers one question: were these measured together.
