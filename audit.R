# Audit the index against its SOURCES, not against itself.
#
# This exists because of a bug found 2026-08-18. A reader applied value-label
# translation, and a coded value with no matching codebook entry came back as NA,
# so presence was silently understated by up to 7.7% on questionnaire variables.
# The index was perfectly self-consistent the whole time, and the demo queries
# stayed correct because they happened to use continuous variables. No check
# reading only the index could have caught it.
#
# So the checks that matter re-read source files and recompute presence.
#
#   Rscript audit.R                # sampled, about a minute, for routine use
#   Rscript audit.R full           # EXHAUSTIVE, re-downloads everything, ~45 min
#   Rscript audit.R 20 100 2023    # sample harder, plus BRFSS 2023
#
# Sampled mode finds systematic breakage. It misses local breakage: an earlier
# version of pack.py --verify sampled 40 variable pairs out of 6,918 GSS
# variables, which is 0.02% of them, and missed a single flipped bit. Run full
# before publishing or after any change to a builder.
#
# Exit status 1 on any failure.

suppressPackageStartupMessages({
  library(arrow); library(dplyr); library(haven)
})

a <- commandArgs(trailingOnly = TRUE)
FULL     <- length(a) && a[1] == "full"
N_NHANES <- if (FULL) Inf else if (length(a) >= 1) as.integer(a[1]) else 6
N_GSS    <- if (FULL) Inf else if (length(a) >= 2) as.integer(a[2]) else 40
# "full" checks every BRFSS year; "full 2023" checks only that one, for when the
# rest have already passed exhaustively and you are re-verifying a fix.
BRFSS_YRS <- if (FULL && length(a) > 1) as.integer(a[-1]) else
             if (FULL) 2011:2023 else
             if (length(a) >= 3) as.integer(a[3]) else integer(0)

fails <- 0
LOGFILE <- tempfile()
sink(LOGFILE, split = TRUE)          # capture what we print, and still print it
fail <- function(...) { cat("  FAIL:", ..., "\n"); fails <<- fails + 1 }
ok   <- function(...) cat("  ok:", ..., "\n")
cat(if (FULL) "EXHAUSTIVE audit\n" else "sampled audit\n")

idx <- open_dataset("index", format = "parquet")

# ---- structural, cheap, not sufficient -------------------------------------
cat("\nstructural\n")

cols <- names(idx)
if (!identical(sort(cols), c("dataset", "stratum", "unit_id", "variable")))
  fail("schema is", paste(cols, collapse = ",")) else ok("schema")

# Duplicate keys. Checked in pack.py --verify rather than here, because doing it
# in R meant hashing 1.16 billion pasted strings and the first attempt sat on
# gigabytes for minutes. duckdb answers it as an aggregate per file.
#
# It is checked at all because removing it cost real time: rehoming NHANES pooled
# files put 530 rows in twice, since a variable can be published in both a
# per-cycle table and a pooled one, and the failure surfaced two hours later as
# "the packed db is corrupt", pointing at the wrong file.
blank <- idx |> filter(is.na(unit_id) | unit_id == "" | is.na(variable)) |>
  head(1) |> collect() |> nrow()
if (blank) fail("rows with a blank unit_id or variable") else ok("no blank keys")

# ---- what "presence" is allowed to mean ------------------------------------
# Presence is non-missing on the actual variable. That is only the right
# definition if NA means "not administered" and a refusal or a don't-know is a
# real value. If a source coded refusals as NA, presence would understate
# co-administration; if it coded skips as 7 or 9, presence would overstate it.
#
# GSS is the case that needs care, because gssr maps sentinels to NA itself.
# Verified below: every sentinel label, iap included, resolves to NA, so a GSS
# respondent counts as present only when actually asked and answering.
cat("\nwhat presence means\n")
suppressPackageStartupMessages(library(gssr))
data(gss_all)
lab <- attr(gss_all$numgiven, "labels")
sent <- names(lab)[is.na(lab)]
# "skipped on web" matters from 2021, when GSS went multimode: an item a web
# respondent was never shown must read as absent for that respondent, exactly
# like an iap. If gssr ever stops mapping it to NA, presence would start counting
# people who were not asked.
if (!all(c("iap", "don't know", "no answer", "skipped on web") %in% sent)) {
  fail("gssr sentinel handling changed, presence may now count unasked respondents")
} else {
  ok("gss: iap/dk/na/skipped-on-web all map to NA, so presence means asked and answered")
}

# NHANES and BRFSS keep refusals as codes (7/9, 77/99) and leave skips blank. That
# is NOT the same as "this question was put to this respondent": a respondent
# routed past an item by their own earlier answer was administered the module and
# is still blank. DECISIONS.md retracts the stronger claim; this file used to
# repeat it. Nothing here tests skip-vs-not-administered, in any dataset, and that
# gap is the honest limit of this audit.

# ---- GSS against source, exact ---------------------------------------------
cat("\ngss against gssr source, exact match\n")
gvars <- idx |> filter(dataset == "gss") |> distinct(variable) |> collect() |> pull(variable)
if (!FULL) gvars <- sample(gvars, min(N_GSS, length(gvars)))
before <- fails

gss_idx <- idx |> filter(dataset == "gss") |> count(variable, stratum) |> collect()
gss_idx <- split(gss_idx, gss_idx$variable)
for (v in gvars) {
  src <- table(gss_all$year[!is.na(gss_all[[v]])])
  g <- gss_idx[[v]]
  src_d <- setNames(as.integer(src), names(src))
  got_d <- if (is.null(g)) integer(0) else setNames(g$n, g$stratum)
  if (!identical(sort(names(src_d)), sort(names(got_d))) ||
      !all(src_d[names(got_d)] == got_d))
    fail("gss", v, "index and source disagree")
}
if (fails == before) ok(length(gvars), "gss variables match source exactly")

# ---- NHANES against source -------------------------------------------------
# The index dedups a variable appearing in several files of one cycle, so the
# index count is legitimately >= any single file's count. Undercounting is the
# dangerous direction and the one a silent reader bug produces.
cat("\nnhanes against cdc source, index must not undercount\n")
man <- read_parquet(".cache/nhanes_manifest.parquet")
source("nhanes_strata.R")   # the same rule the builder uses, not a lookalike
pick <- if (FULL) seq_len(nrow(man)) else sample(nrow(man), min(N_NHANES, nrow(man)))
before <- fails

nh_idx <- idx |> filter(dataset == "nhanes") |> count(stratum, variable) |> collect()
nh_key <- setNames(nh_idx$n, paste(nh_idx$stratum, nh_idx$variable))

checked <- 0
for (i in pick) {
  tb <- man$Table[i]; cyc <- man$Years[i]
  d <- NULL
  for (try in 1:3) {
    d <- tryCatch(read_xpt(paste0("https://wwwn.cdc.gov", man$DataURL[i])),
                  error = function(e) NULL)
    if (!is.null(d)) break
    Sys.sleep(2)
  }
  if (is.null(d)) { fail("could not fetch", tb, "to check it"); next }
  if (!"SEQN" %in% names(d)) next

  vars <- setdiff(names(d), "SEQN")
  # Distinct respondents, not rows. NHANES has tables with many rows per SEQN
  # well beyond the dietary files: AUXAR (audiometry) runs ~12 rows per person,
  # RXQANA ~1.04. The index counts a respondent once per variable, so comparing
  # against a raw row count reports a phantom undercount on every such table.
  src <- vapply(vars, function(v) length(unique(d$SEQN[!is.na(d[[v]])])), integer(1))
  src <- src[src > 0]
  # A pooled file's variables live in the cycles its respondents belong to, not
  # under the pooled label, so sum across those cycles. Looking under the pooled
  # label would report a correct index as missing every variable.
  cycles <- if (!is.null(NHANES_POOLED_PARTS[[cyc]])) NHANES_POOLED_PARTS[[cyc]] else cyc
  got <- vapply(names(src), function(v)
    sum(nh_key[paste(cycles, v)], na.rm = TRUE), numeric(1))
  short <- names(src)[got < src]
  if (length(short))
    fail(tb, cyc, "undercounts", length(short), "variables, worst:",
         paste(head(short, 5), collapse = ","))
  checked <- checked + 1
  if (FULL && checked %% 100 == 0) cat("  ...", checked, "of", length(pick), "tables\n")
}
if (fails == before) ok(checked, "nhanes tables, no variable undercounted")

# ---- BRFSS against source, exact -------------------------------------------
# One file per year, so unlike NHANES there is no multi-file dedup and the count
# must match exactly.
source("fips.R")

for (byr in BRFSS_YRS) {
  cat("\nbrfss", byr, "against cdc source, exact match\n")
  before <- fails
  tmp <- tempfile(); dir.create(tmp); zp <- file.path(tmp, "b.zip")
  url <- sprintf("https://www.cdc.gov/brfss/annual_data/%d/files/LLCP%dXPT.zip", byr, byr)
  if (!tryCatch({ download.file(url, zp, quiet = TRUE, mode = "wb"); TRUE },
                error = function(e) FALSE)) {
    fail("could not download brfss", byr); unlink(tmp, TRUE); next
  }
  unzip(zp, exdir = tmp)
  f <- list.files(tmp, pattern = "(?i)[.]xpt[ ]*$", full.names = TRUE)
  d <- read_xpt(f[1]); unlink(tmp, recursive = TRUE)

  strat <- paste0(byr, "|", FIPS[as.character(d[["_STATE"]])])
  bvars <- setdiff(names(d), c("_STATE", "SEQNO"))
  if (!FULL) bvars <- sample(bvars, min(N_GSS, length(bvars)))

  # This year's strata only. The index holds every BRFSS year, and comparing one
  # year of source against thirteen years of index fails everything that ran in
  # more than one year. That bug looked exactly like a data bug.
  bi <- idx |> filter(dataset == "brfss") |> count(variable, stratum) |> collect()
  bi <- bi[startsWith(bi$stratum, paste0(byr, "|")), ]
  bi <- split(bi, bi$variable)

  for (v in bvars) {
    src <- table(strat[!is.na(d[[v]])])
    g <- bi[[v]]
    src_d <- setNames(as.integer(src), names(src))
    got_d <- if (is.null(g)) integer(0) else setNames(g$n, g$stratum)
    if (!identical(sort(names(src_d)), sort(names(got_d))) ||
        !all(src_d[names(got_d)] == got_d))
      fail("brfss", byr, v, "index and source disagree")
  }
  rm(d); gc(verbose = FALSE)
  if (fails == before) ok(length(bvars), "brfss", byr, "variables match source exactly")
}

# ---- packed bitmap db against the parquet index ----------------------------
# covary_*.db is derived from the parquet index, so it gets its own check. Lives
# in pack.py because that is what produced it; called from here so there is one
# audit command rather than two.
if (length(list.files(".", pattern = "^covary_.*[.]db$"))) {
  cat("\npacked bitmap db against the parquet index\n")
  py <- if (file.exists(".venv/bin/python")) ".venv/bin/python" else "python3"
  o <- system2(py, c("pack.py", "--verify"), stdout = TRUE, stderr = TRUE)
  cat(paste0("  ", o, collapse = "\n"), "\n")
  if (any(grepl("FAILURES", o))) fail("packed db disagrees with the parquet index")
}

cat("\n", if (fails) paste(fails, "FAILURES") else "all checks passed", "\n", sep = "")
sink()

# Write the evidence file ourselves. It used to be assembled by hand, so README
# called it "the full output of Rscript audit.R full" when nothing in the repo
# produced it, and the binding between the log and the shipped dbs rested on
# whoever pasted the hashes. A reviewer cannot rerun this without the 2GB index,
# so the least this can do is be a real artifact of a real run.
if (FULL && !fails) {
  dbs <- sort(list.files(".", pattern = "^covary_.*[.]db$"))
  sums <- vapply(dbs, function(f)
    strsplit(system2("shasum", c("-a", "256", f), stdout = TRUE), " ")[[1]][1], character(1))
  writeLines(c(
    "covary audit evidence",
    paste("generated by:  Rscript audit.R full"),
    paste("generated at: ", format(Sys.time(), tz = "UTC", usetz = TRUE)),
    paste("R:            ", R.version.string),
    "",
    "sha256 of the index files this run verified:",
    paste0("  ", sums, "  ", dbs),
    "",
    "If a db file's hash differs from the line above, this log does not describe it.",
    "", "----", "", readLines(LOGFILE)), "audit.log")
  cat("wrote audit.log\n")
}
quit(status = if (fails) 1 else 0)
