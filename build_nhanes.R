# NHANES -> index/nhanes_<cycle>.parquet, the shared co-occurrence schema.
#
# NHANES splits one cycle across ~130 component files. Two variables living in
# the same cycle says nothing about whether the same respondents answered both:
# a respondent can complete the household interview and never show up for the
# MEC exam, and subsample files (fasting labs, DXA) cover only a fraction of
# the cycle by design. Presence here is non-missing on the actual variable,
# which handles interview-vs-exam and subsampling without a weight table.
#
# Schema, one row per (respondent, variable) they answered:
#   dataset  "nhanes"
#   stratum  cycle as published, e.g. "2017-2018". Pooled files keep their own
#            span ("2017-2020", "1999-2004") because that is genuinely a
#            different respondent set, not a relabelling of a two-year cycle.
#   unit_id  SEQN, unique within (dataset, stratum)
#   variable
#
# Dietary is excluded: DR/DS files are day-level or supplement-level with
# several rows per SEQN, so presence there needs a dedup rule that would not
# mean the same thing as it does everywhere else.
#
# Resumable. A cycle whose parquet already exists is skipped, so a failed run
# is fixed by rerunning it.

suppressPackageStartupMessages({
  library(nhanesA); library(haven); library(arrow); library(dplyr); library(parallel)
})

CORES <- 4
dir.create("index", showWarnings = FALSE)

# CDC's listing page is flaky and fails by returning nothing rather than erroring,
# which silently produces an empty index. Cache it and refuse to continue on empty.
dir.create(".cache", showWarnings = FALSE)
MAN <- ".cache/nhanes_manifest.parquet"
if (file.exists(MAN)) {
  man <- read_parquet(MAN)
} else {
  for (try in 1:5) {
    man <- tryCatch(nhanesManifest("public"), error = function(e) NULL)
    if (!is.null(man) && nrow(man) > 100) break
    cat("manifest attempt", try, "failed, retrying\n"); Sys.sleep(10)
  }
  if (is.null(man) || nrow(man) < 100) stop("could not fetch the NHANES manifest")
  write_parquet(man, MAN)
}
cat("manifest:", nrow(man), "tables\n")

source("nhanes_scope.R")   # one definition, shared with audit.R
drop <- nhanes_dietary(man)
man <- man[!drop, ]
cat("tables:", nrow(man), "after dropping", sum(drop), "dietary\n")

# Read the XPT raw, with haven, NOT with nhanesFromURL. nhanesA applies value-label
# translation on the way in, and a coded value with no matching label entry comes
# back as NA. Measured 2026-08-18: SMQ_J loses 7.7% of its presence that way and
# DIQ_J 3.2%, which would understate every joint n touching a coded variable.
# Presence does not need labels, so it does not need translation.
#
# Returns a tibble, NULL for a table that has no respondent id at all (drug and
# food-code lookups), or the string "FAIL" when CDC would not serve the file.
# A transient fetch failure that is quietly dropped would silently shrink a joint
# n, which is the exact failure mode this whole index exists to catch, so a cycle
# with any FAIL is not written and gets retried on the next run.
one_table <- function(i) {
  tb <- man$Table[i]
  d <- NULL
  for (try in 1:3) {
    d <- tryCatch(read_xpt(paste0("https://wwwn.cdc.gov", man$DataURL[i])),
                  error = function(e) NULL)
    if (!is.null(d) && is.data.frame(d)) break
    Sys.sleep(3)
  }
  if (is.null(d) || !is.data.frame(d)) { cat("  FAIL", tb, "\n"); return("FAIL") }
  if (!"SEQN" %in% names(d)) {
    cat("  skip", tb, "(no SEQN, cols:", paste(head(names(d), 4), collapse=","), ")\n")
    return(NULL)
  }

  vars <- setdiff(names(d), "SEQN")
  seqn <- as.character(d$SEQN)
  bind_rows(lapply(vars, function(v) {
    idx <- which(!is.na(d[[v]]))
    if (!length(idx)) return(NULL)
    tibble(unit_id = seqn[idx], variable = v)
  }))
}

for (cyc in unique(man$Years)) {
  out <- file.path("index", paste0("nhanes_", cyc, ".parquet"))
  if (file.exists(out)) { cat("have", cyc, "\n"); next }

  rows <- which(man$Years == cyc)
  cat(cyc, ":", length(rows), "tables\n")
  got <- mclapply(rows, one_table, mc.cores = CORES)
  bad <- sum(vapply(got, identical, logical(1), "FAIL"))
  if (bad) { cat("  ", bad, "tables unfetchable, not writing this cycle\n"); next }

  pres <- bind_rows(got[!vapply(got, is.null, logical(1))])
  if (!nrow(pres)) { cat("  nothing usable, skipping\n"); next }

  # distinct because a variable can appear in more than one file within a cycle
  pres <- pres |>
    mutate(dataset = "nhanes", stratum = cyc) |>
    distinct(dataset, stratum, unit_id, variable) |>
    arrange(variable, unit_id)

  write_parquet(pres, out, compression = "zstd")
  cat("  wrote", out, ":", nrow(pres), "pairs,",
      round(file.size(out) / 1e6, 1), "MB\n")
}
