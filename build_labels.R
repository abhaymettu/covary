# Variable text -> .cache/labels_<dataset>.csv, the input to pack_labels.py.
#
# The presence index stores names and bits and no text at all, so covary can
# confirm two variables were co-administered while the analyst is asking about
# the wrong two. FIREARM5 is whether a firearm is in the home; GUNLOAD is how it
# is stored. Nothing in the output distinguished them. This is where the text to
# distinguish them comes from.
#
# Schema, one row per (dataset, variable):
#   dataset      gss | nhanes | brfss
#   variable     as it appears in the index
#   description  one line, safe to print under a variable name
#   question     verbatim item wording where the agency published it, else ""
#   source       what produced the row, so a bad row is traceable to a fetch
#
# Nothing here is evidence about co-administration and none of it may be used to
# infer one. Presence bits remain the only source of truth for a joint n.
#
# Resumable per dataset: a CSV that already exists is left alone. Delete the one
# you want rebuilt.

suppressPackageStartupMessages({
  library(haven); library(rvest); library(readr); library(dplyr)
})

dir.create(".cache", showWarnings = FALSE)
ONLY <- commandArgs(TRUE)
want <- function(ds) (!length(ONLY) || ds %in% ONLY) &&
  !file.exists(file.path(".cache", paste0("labels_", ds, ".csv")))

# One writer so every dataset lands in the same shape. Empty is never written:
# a zero-row CSV would satisfy the resume check and silently pin the dataset to
# nothing on every later run, which is the failure this build path invites.
# BRFSS ships at least one SAS label with a byte that is not valid UTF-8, which
# makes gsub() error on the whole vector rather than on the one string. Re-encode
# the offending entries instead of dropping the dataset.
clean <- function(x) {
  x <- as.character(x)
  bad <- is.na(iconv(x, "UTF-8", "UTF-8"))
  x[bad] <- iconv(x[bad], "latin1", "UTF-8", sub = "")
  trimws(gsub("[[:space:]]+", " ", ifelse(is.na(x), "", x)))
}

emit <- function(ds, df) {
  df <- df |>
    mutate(across(everything(), clean)) |>
    filter(variable != "", !is.na(variable)) |>
    distinct(variable, .keep_all = TRUE) |>
    mutate(dataset = ds) |>
    select(dataset, variable, description, question, source) |>
    arrange(variable)
  if (!nrow(df)) { cat("  ", ds, ": nothing, not writing\n"); return(invisible()) }
  out <- file.path(".cache", paste0("labels_", ds, ".csv"))
  write_csv(df, out, na = "")
  cat("  ", ds, ":", nrow(df), "variables ->", out, "\n")
}

# ---- GSS ---------------------------------------------------------------------
# gssrdoc ships the documentation as data, so this needs no network and is the
# best text of the three: the verbatim interviewer script, plus NORC's own
# subject tags, which are what makes a topic search work at all.
if (want("gss")) {
  cat("gss: gssrdoc::gss_doc\n")
  suppressPackageStartupMessages(library(gssrdoc))
  data(gss_doc, package = "gssrdoc")
  flat <- function(col) vapply(col, function(x)
    paste(unlist(x), collapse = " "), character(1))
  subj <- vapply(gss_doc$subject_df, function(x) {
    v <- unlist(x); v <- v[!is.na(v) & nzchar(v)]
    if (!length(v)) "" else paste("Topics:", paste(unique(v), collapse = ", "))
  }, character(1))
  emit("gss", tibble(
    variable    = as.character(gss_doc$variable),
    description = flat(gss_doc$description),
    question    = trimws(paste(flat(gss_doc$question), subj)),
    source      = "gssrdoc::gss_doc"))
}

# ---- NHANES ------------------------------------------------------------------
# CDC's per-component variable list, five pages for the whole survey. The XPT
# headers carry SAS labels too, but read_xpt has no range requests, so reading
# 1,544 headers means downloading roughly 30GB to get worse text than this.
#
# A variable recurs across cycles with wording that drifts, so keep the longest
# description: the short ones are truncations of the same item.
if (want("nhanes")) {
  cat("nhanes: wwwn.cdc.gov variable list\n")
  comps <- c("Demographics", "Dietary", "Examination", "Laboratory", "Questionnaire")
  got <- lapply(comps, function(cp) {
    u <- paste0("https://wwwn.cdc.gov/nchs/nhanes/search/variablelist.aspx?Component=", cp)
    tb <- NULL
    for (try in 1:3) {
      tb <- tryCatch(html_table(read_html(u))[[1]], error = function(e) NULL)
      if (!is.null(tb) && nrow(tb) > 10) break
      cat("   ", cp, "attempt", try, "failed\n"); Sys.sleep(5)
    }
    # A component that fails is not silently dropped. Half a survey's text would
    # still pass every smoke test and only show up as search quietly missing things.
    if (is.null(tb) || nrow(tb) < 10) stop("could not fetch NHANES component ", cp)
    cat("   ", cp, nrow(tb), "rows\n")
    tibble(variable = as.character(tb[[1]]), description = as.character(tb[[2]]))
  })
  emit("nhanes", bind_rows(got) |>
         mutate(description = ifelse(is.na(description), "", description)) |>
         arrange(variable, desc(nchar(description))) |>
         mutate(question = "", source = "wwwn.cdc.gov/nchs/nhanes/search/variablelist.aspx"))
}

# ---- BRFSS -------------------------------------------------------------------
# The SAS label in the XPT header. BRFSS publishes real item wording only in the
# 508-tagged codebook PDFs, and parsing thirteen years of those to improve text
# that is already adequate is not worth it.
#
# n_max = 1 because labels are header attributes: the download is unavoidable,
# the parse of a million rows is not.
if (want("brfss")) {
  cat("brfss: XPT headers\n")
  got <- list()
  for (yr in 2011:2023) {
    # Each year is a ~100MB download for a header. Cache the extracted labels so
    # a failure further down this script costs seconds, not the whole 1.3GB again.
    cf <- file.path(".cache", sprintf("brfss_labels_%d.rds", yr))
    if (file.exists(cf)) {
      got[[length(got) + 1]] <- readRDS(cf)
      cat("    ", yr, "cached\n"); next
    }
    td <- tempfile(); dir.create(td); z <- file.path(td, "b.zip")
    u <- sprintf("https://www.cdc.gov/brfss/annual_data/%d/files/LLCP%dXPT.zip", yr, yr)
    ok <- tryCatch({ download.file(u, z, quiet = TRUE, mode = "wb"); TRUE },
                   error = function(e) FALSE)
    if (!ok) { cat("    ", yr, "download failed\n"); unlink(td, TRUE); next }
    unzip(z, exdir = td)
    # CDC ships the file with a trailing space in the name, so match loosely.
    f <- list.files(td, pattern = "(?i)[.]xpt[ ]*$", full.names = TRUE)
    if (!length(f)) { cat("    ", yr, "no xpt\n"); unlink(td, TRUE); next }
    d <- tryCatch(read_xpt(f[1], n_max = 1), error = function(e) NULL)
    unlink(td, recursive = TRUE)
    if (is.null(d)) { cat("    ", yr, "unreadable\n"); next }
    lab <- vapply(d, function(x) { a <- attr(x, "label"); if (is.null(a)) "" else a },
                  character(1))
    cat("    ", yr, ncol(d), "variables\n")
    one <- tibble(variable = names(d), description = unname(lab), yr = yr)
    saveRDS(one, cf)
    got[[length(got) + 1]] <- one
  }
  if (!length(got)) stop("no BRFSS year could be read")
  # Latest year wins: a label reworded in 2023 describes the item as it now
  # stands, and the older wording adds nothing a search would want.
  emit("brfss", bind_rows(got) |> arrange(variable, desc(yr)) |>
         mutate(question = "", source = "BRFSS XPT SAS label"))
}

cat("done\n")
