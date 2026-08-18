# covary: a text layer over the presence index

Status: approved 2026-08-18. Supersedes the "question-level semantic layer"
entry parked in `DECISIONS.md:321` and `HANDOFF.md:619`.

## The problem this actually solves

covary answers "were these variables measured on the same respondents". To ask
that question you must already know the survey calls the thing you mean
`numgiven`. Everyone new to a survey hits that wall before they hit the
co-administration wall, and so does every agent: the MCP server today requires
the caller to produce a real variable name from nothing.

`README.md` already confesses the sharper version of this:

> covary will happily confirm that two variables were measured together while
> you are asking about the wrong two.

`FIREARM5` is whether a firearm is kept in the home. `GUNLOAD` and `LOADULK2`
are the storage items. Nothing in the current output distinguishes them, because
the index stores names and bits and no text at all.

## What was parked, and why the parked version was wrong

The parked idea was a semantic layer mapping a plain-English question onto
candidate variables, deferred as "the actual unsolved research problem in this
space". That framing assumed covary would have to do the semantics.

It does not. The consumer of covary's output is an agent, or a researcher with
an LLM open in the next tab. Give either one searchable question text and they
perform the question-to-variable step themselves. The unsolved research problem
was never covary's to solve. The missing piece is an asset, not a model: the
index has no text in it.

## What ships

Three things, in value order.

1. A `labels` table: `(dataset, variable, description, question, source)`.
2. Labels printed beside every variable in normal output, so you catch the
   wrong-two-variables error before you design around it.
3. Full-text search over that text, as `covary.py --search` and an MCP
   `search_variables` tool, so a name can be reached from a phrase.

## Where the text comes from

Verified 2026-08-18 by fetching each source.

| dataset | source | coverage | quality |
|---|---|---|---|
| GSS | `gssrdoc::gss_doc`, installed locally, no network | 6,942 of 6,942 | verbatim interviewer script, plus a one-line description and subject tags |
| NHANES | 5 component pages at `wwwn.cdc.gov/nchs/nhanes/search/variablelist.aspx` | ~60k rows over all components | question text where CDC recorded it, otherwise a variable description |
| BRFSS | SAS label in the XPT header, `attr(x, "label")` | 350 of 350 on 2023 | terse and upper case, e.g. `CORRECT TELEPHONE NUMBER?` |

The quality is uneven and the tool should not pretend otherwise. GSS search will
feel good. BRFSS search will feel adequate. That is a property of what the
agencies published, not something more code fixes.

Rejected sources, with reasons:

- **NHANES XPT headers.** `read_xpt` has no range requests, so reading 1,544
  headers means downloading roughly 30GB. The variable list pages are 5 requests
  and carry better text.
- **BRFSS codebook PDFs.** Real question text lives there, but parsing 13 years
  of 508-tagged PDF to improve already-adequate labels is not worth it. Revisit
  only if search demonstrably fails on BRFSS.
- **Embeddings and LLM reranking.** Not until a real query fails FTS5.

## Design

### Storage: a separate `labels.db`

Labels go in their own SQLite file, deliberately **not** named `covary_*.db`.

Two reasons, both load-bearing. `covary.py:34` globs `covary_*.db` and unions
whatever it finds into `bm` and `strata` views, so a fourth file matching that
glob would break `connect()`. And `audit.log` records the sha256 of each
`covary_*.db` it verified, with `shasum -a 256 covary_*.db` documented as the
check a user runs; adding a table to those files would invalidate that chain for
a change the audit does not cover.

`labels.db` is derived, regenerable, and carries no presence data. It is not
audited by `audit.R` and must not claim to be.

Schema:

```sql
CREATE TABLE labels(dataset TEXT, variable TEXT, description TEXT,
                    question TEXT, source TEXT,
                    PRIMARY KEY (dataset, variable));
CREATE VIRTUAL TABLE labels_fts USING fts5(
    variable, description, question,
    content='labels', tokenize='porter unicode61');
```

`content='labels'` keeps FTS5 as an index over the base table rather than a
second copy of the text.

### Build path

`build_labels.R` writes one CSV per dataset into `.cache/`, resumable, skipping
a dataset whose CSV already exists. `pack_labels.py` reads the three CSVs and
writes `labels.db`.

CSV rather than parquet on purpose: the query path is stdlib-only Python by
project constraint, and `csv` is stdlib while parquet would drag in an
extension. GSS question text contains embedded newlines and quotes; both sides
quote correctly.

### Read path

`covary.py` opens `labels.db` lazily and read-only. **A missing `labels.db` is
not an error.** Every existing behaviour must work unchanged without it, because
the index is the product and the text is an enhancement. A clone that has not
run `pack_labels.py` prints exactly what it prints today.

Marginal output gains an indented line per variable:

```
per variable, ignoring co-administration:
  numgiven     gss     n=5819     1985, 1987, 2004, 2024
               number of persons mentioned
  socfrend     gss     n=45294    1974 .. 2024, 29 strata
               spend evening with friends
```

Prefer `description` for this line because it is one line by construction.
Question text can run to a paragraph and belongs behind `--detail` and in
`--json`, not in the default view.

`--search PATTERN` runs FTS5 and prints ranked `(dataset, variable,
description)`. It is the text sibling of `--find`, which stays as substring
matching over names and is not replaced: `--find` answers "I half remember the
name", `--search` answers "I do not know the name".

Exit codes follow the existing contract. `--search` with no hits exits 2, the
same as `--find` with no match, because it is the same class of outcome: the
question cannot be answered as asked.

### MCP

One new tool, `search_variables(query, dataset=None, limit=20)`, returning
`(dataset, variable, description)`. This is the piece that makes the whole thing
work for agents: an agent goes query text, then candidate names, then
`check_covariation`, without ever guessing a name.

`skills/covary/SKILL.md` gains the instruction to search before checking when
the caller does not already have names.

## Non-goals

- covary does not answer research questions. It surfaces which variables are
  about a topic and which years measured them together. The inferential step
  stays with the researcher.
- No harmonization. CLOSER and Maelstrom own that.
- Labels are not evidence about co-administration and are never used to infer
  it. Presence bits remain the only source of truth for a joint n.

## Testing

Added to `tests.sh`:

1. Every behaviour tested today still passes with `labels.db` absent.
2. `--search "important matters"` returns `numgiven` for GSS.
3. `--search` with a nonsense string exits 2.
4. A variable present in the index but missing from `labels.db` prints its
   normal line with no label line and does not crash.
5. `--json` carries the label field when available and omits it when not.
6. Coverage floor: labels resolve for at least 90 percent of GSS and NHANES
   variables in the index, and at least 60 percent for BRFSS.

Test 6 is the one that catches a silently half-built `labels.db`, which is the
failure mode this build path actually has.

## Sequencing

1. `build_labels.R` plus `pack_labels.py`, ending in a populated `labels.db`.
2. Label lines in `covary.py` output.
3. `--search` and FTS5 query path.
4. `search_variables` MCP tool and the SKILL.md instruction.
5. Tests and README.

MVP is 1 through 3. Steps 4 and 5 finish it.
