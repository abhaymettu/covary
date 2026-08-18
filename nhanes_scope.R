# Which NHANES tables are in scope, defined ONCE.
#
# build_nhanes.R and audit.R both need this and they disagreed. The build used
# CDC's dietary listing plus a prefix backstop; the audit used the prefix alone,
# so it flagged 15 tables as missing from the index that the build had correctly
# excluded: the P_-prefixed pre-pandemic dietary files, and the FFQ/DTQ food
# frequency files, none of which match "^(DR|DS)".
#
# A scope rule that lives in two places will drift, and when it drifts the audit
# reports the index as broken while the index is fine.

nhanes_dietary <- function(man) {
  # CDC's own listing where it answers, which covers the P_ and FFQ names a
  # prefix rule cannot know about.
  listed <- unique(unlist(lapply(
    c(seq(1999, 2017, by = 2), "P", 2021, 2023),
    function(y) tryCatch(nhanesA::nhanesTables("DIET", y, namesonly = TRUE),
                         error = function(e) character(0)))))
  # Backstop, because the listing returns nothing for some cycles (2021-2023).
  # Every table it adds is a recall, a supplement file, or a food-code lookup.
  man$Table %in% listed | grepl("^(P_)?(DR|DS)", man$Table) | grepl("^(FFQ|DTQ)", man$Table)
}
