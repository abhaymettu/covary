# BRFSS -> index/brfss_<year>.parquet, the shared co-occurrence schema.
#
# BRFSS is the reason `stratum` is a free-form string rather than a year. Its
# optional modules are chosen state by state, so a variable can be nationally
# absent and locally universal in the same year. A year-level answer would report
# "2023, n=15,000" for a module that only ever ran in twelve states, which reads
# as nationally usable and is not.
#
# Schema, one row per (respondent, variable) they answered:
#   dataset  "brfss"
#   stratum  "<year>|<state>", e.g. "2023|AL"
#   unit_id  SEQNO. Verified 2026-08-18: SEQNO is NOT unique within a year, only
#            within a state, so a year-level stratum would silently merge
#            different respondents from different states into one.
#   variable
#
# 2011 onward only. Pre-2011 uses a different file naming (CDBRFS*) and a
# different weighting methodology, and the LLCP<year>XPT.zip pattern 404s there.
#
# Questionnaire version (QSTVER, 8 values in 2023) is deliberately NOT part of
# the stratum. Presence is per respondent, so a joint n is exact whatever the
# grain; version would triple the strata to describe an implementation detail
# inside a state rather than a policy difference between states.
#
# Resumable. A year whose parquet already exists is skipped.
#
#   Rscript build_brfss.R            # 2011 to 2023
#   Rscript build_brfss.R 2023       # one year

suppressPackageStartupMessages({
  library(haven); library(arrow); library(dplyr)
})

a <- commandArgs(trailingOnly = TRUE)
YEARS <- if (length(a)) as.integer(a) else 2011:2023

source("fips.R")

dir.create("index", showWarnings = FALSE)

for (yr in YEARS) {
  out <- file.path("index", paste0("brfss_", yr, ".parquet"))
  if (file.exists(out)) { cat("have", yr, "\n"); next }

  tmp <- tempfile(); dir.create(tmp)
  zip <- file.path(tmp, "b.zip")
  url <- sprintf("https://www.cdc.gov/brfss/annual_data/%d/files/LLCP%dXPT.zip", yr, yr)
  cat(yr, "downloading\n")
  okd <- tryCatch({ download.file(url, zip, quiet = TRUE, mode = "wb"); TRUE },
                  error = function(e) FALSE)
  if (!okd) { cat("  FAIL download, skipping year\n"); unlink(tmp, TRUE); next }

  unzip(zip, exdir = tmp)
  # CDC ships the file with a trailing space in the name, so match loosely.
  f <- list.files(tmp, pattern = "(?i)[.]xpt[ ]*$", full.names = TRUE)
  if (!length(f)) { cat("  FAIL no xpt in archive\n"); unlink(tmp, TRUE); next }

  d <- tryCatch(read_xpt(f[1]), error = function(e) NULL)
  unlink(tmp, recursive = TRUE)
  if (is.null(d)) { cat("  FAIL unreadable xpt\n"); next }

  if (!all(c("_STATE", "SEQNO") %in% names(d))) {
    cat("  FAIL missing _STATE or SEQNO\n"); next
  }

  st <- FIPS[as.character(d[["_STATE"]])]
  if (anyNA(st)) {
    cat("  FAIL unmapped FIPS:",
        paste(unique(d[["_STATE"]][is.na(st)]), collapse = ","), "\n"); next
  }
  stratum <- paste0(yr, "|", st)
  seqno <- as.character(d$SEQNO)

  vars <- setdiff(names(d), c("_STATE", "SEQNO"))
  pres <- bind_rows(lapply(vars, function(v) {
    idx <- which(!is.na(d[[v]]))
    if (!length(idx)) return(NULL)
    tibble(stratum = stratum[idx], unit_id = seqno[idx], variable = v)
  }))
  rm(d); gc(verbose = FALSE)

  pres <- pres |> mutate(dataset = "brfss") |>
    select(dataset, stratum, unit_id, variable) |>
    arrange(variable, stratum)

  write_parquet(pres, out, compression = "zstd")
  cat("  wrote", out, ":", format(nrow(pres), big.mark = ","), "pairs,",
      round(file.size(out) / 1e6, 1), "MB,",
      length(unique(pres$stratum)), "strata\n")
  rm(pres); gc(verbose = FALSE)
}
