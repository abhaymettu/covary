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
# Nothing is excluded by table. Dietary (DR/DS) was excluded until 2026-08-18 on
# the reasoning that several rows per SEQN would make presence mean something
# different there. That reasoning was wrong: presence is deduped to one row per
# (respondent, variable) below, and NHANES has multi-row tables well outside
# dietary (AUXAR runs ~12 rows per person) that were always handled correctly.
#
# Resumable. A cycle whose parquet already exists is skipped, so a failed run
# is fixed by rerunning it.

# macOS crashes any fork()ed child once the Objective-C runtime has initialized,
# which is every child mclapply makes here. Found 2026-08-18 the hard way: the
# children died, mclapply returned NULL for each, and NULL meant "no respondent
# id" to one_table(), so every cycle read as empty and was skipped with a cheerful
# "nothing usable". Setting this from inside R is too late, so re-exec once.
if (Sys.info()[["sysname"]] == "Darwin" &&
    Sys.getenv("OBJC_DISABLE_INITIALIZE_FORK_SAFETY") == "") {
  Sys.setenv(OBJC_DISABLE_INITIALIZE_FORK_SAFETY = "YES")
  quit(status = system2(file.path(R.home("bin"), "Rscript"),
                        c("build_nhanes.R", commandArgs(TRUE))))
}

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

# Read the XPT raw, with haven, NOT with nhanesFromURL. nhanesA applies value-label
# translation on the way in, and a coded value with no matching label entry comes
# back as NA. Measured 2026-08-18: SMQ_J loses 7.7% of its presence that way and
# DIQ_J 3.2%, which would understate every joint n touching a coded variable.
# Presence does not need labels, so it does not need translation.
#
# Returns a tibble, "SKIP" for a table that has no respondent id at all (drug and
# food-code lookups), or "FAIL" when CDC would not serve the file. A transient
# fetch failure that is quietly dropped would silently shrink a joint n, which is
# the exact failure mode this whole index exists to catch, so a cycle with any
# FAIL is not written and gets retried on the next run.
#
# NULL is deliberately not a return value here. mclapply returns NULL for a child
# that died, so anything NULL is a crash, not a result, and it is treated as FAIL.
# Overloading NULL to also mean "nothing to index" is what let a whole run of
# dead children pass for a run of empty tables.
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
    return("SKIP")
  }

  vars <- setdiff(names(d), "SEQN")
  seqn <- as.character(d$SEQN)
  bind_rows(lapply(vars, function(v) {
    idx <- which(!is.na(d[[v]]))
    if (!length(idx)) return(NULL)
    tibble(unit_id = seqn[idx], variable = v)
  }))
}

source("nhanes_strata.R")   # shared with audit.R

for (cyc in unique(man$Years)) {
  out <- file.path("index", paste0("nhanes_", cyc, ".parquet"))
  if (file.exists(out)) { cat("have", cyc, "\n"); next }

  rows <- which(man$Years == cyc)
  cat(cyc, ":", length(rows), "tables\n")
  got <- mclapply(rows, one_table, mc.cores = CORES)
  bad <- sum(!vapply(got, is.data.frame, logical(1)) &
             !vapply(got, identical, logical(1), "SKIP"))
  if (bad) { cat("  ", bad, "tables unfetchable or crashed, not writing this cycle\n"); next }

  pres <- bind_rows(got[vapply(got, is.data.frame, logical(1))])
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

# Pooled files keep the SEQNs of the cycles they were drawn from, so their rows
# belong in those cycles. Done as a final pass because a pooled span's respondents
# live in cycles that may not have been written when the pooled file was read.
# See nhanes_strata.R for why filing them separately understated a real joint n
# by 40x.
cat("rehoming pooled-file rows\n")
all_p <- bind_rows(lapply(list.files("index", "^nhanes_.*[.]parquet$", full.names = TRUE),
                          read_parquet))
homes <- all_p |>
  filter(!stratum %in% names(NHANES_POOLED_PARTS)) |>
  distinct(unit_id, .keep_all = FALSE) |>
  left_join(all_p |> filter(!stratum %in% names(NHANES_POOLED_PARTS)) |>
              distinct(unit_id, stratum), by = "unit_id")
homes <- homes[!duplicated(homes$unit_id), ]
names(homes)[2] <- "home"

moved <- all_p |> left_join(homes, by = "unit_id") |>
  mutate(stratum = if_else(stratum %in% names(NHANES_POOLED_PARTS) & !is.na(home),
                           home, stratum)) |>
  select(dataset, stratum, unit_id, variable) |>
  distinct()
cat("  rows:", nrow(all_p), "->", nrow(moved), "after rehoming and dedupe\n")

# One file per stratum. Writing rehomed rows back into the pooled-named file left
# a stratum spanning two files, which pack.py's per-file duplicate check cannot
# see, so a cross-file duplicate surfaced later as "the packed db is corrupt".
# A file is a stratum; that invariant is what makes the cheap check sufficient.
unlink(list.files("index", "^nhanes_.*[.]parquet$", full.names = TRUE))
for (st in sort(unique(moved$stratum))) {
  write_parquet(moved |> filter(stratum == st) |> arrange(variable, unit_id),
                file.path("index", paste0("nhanes_", st, ".parquet")),
                compression = "zstd")
}
cat("  wrote", length(unique(moved$stratum)), "stratum files\n")
