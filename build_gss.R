# GSS -> index/gss.parquet, the shared co-occurrence schema.
#
# ICPSR's Social Science Variables Database searches 5M variables across 17k
# studies and can tell you which study contains a variable. It cannot tell you
# whether two variables were administered to the SAME respondents, and ICPSR's
# own docs name this as the limitation and prescribe reading the codebook.
#
# It does not need a codebook. Non-missingness per respondent is in the data and
# the answer is an intersection.
#
# Schema, one row per (respondent, variable) they answered:
#   dataset  "gss"
#   stratum  survey year, the unit within which co-administration is decided
#   unit_id  respondent row index, unique within (dataset, stratum)
#   variable

suppressPackageStartupMessages({library(gssr); library(arrow); library(dplyr)})
data(gss_all)

vars <- setdiff(names(gss_all), c("year", "id"))
cat("variables:", length(vars), " respondents:", nrow(gss_all), "\n")

pres <- lapply(vars, function(v) {
  idx <- which(!is.na(gss_all[[v]]))
  if (!length(idx)) return(NULL)
  tibble(dataset = "gss",
         stratum = as.character(gss_all$year[idx]),
         unit_id = as.character(idx),
         variable = v)
}) |> bind_rows() |> arrange(variable, stratum)

dir.create("index", showWarnings = FALSE)
write_parquet(pres, "index/gss.parquet", compression = "zstd")
cat("wrote index/gss.parquet:", nrow(pres), "variable-respondent pairs\n")
cat("size:", round(file.size("index/gss.parquet") / 1e6, 1), "MB\n")
