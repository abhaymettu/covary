# Which stratum does an NHANES respondent belong to?
#
# The answer is NOT "the cycle named on the file the variable came from", which is
# what the builder used to assume. NHANES publishes pooled files spanning several
# cycles, and for some of them CDC keeps the original SEQNs:
#
#   1999-2004   22,284 respondents, 22,284 also in 1999-2000/2001-2002/2003-2004
#   2007-2012      380 respondents,     380 also in their component cycles
#   2017-2020   15,560 respondents,       0 shared, CDC renumbers the pre-pandemic file
#
# Filing a shared-SEQN respondent under the pooled label puts them in a stratum
# nothing else can reach. Bit positions are assigned per stratum, so no AND can
# cross that boundary, and a real design reads as dead. Measured: SSALB x RIAGENDR
# reported a joint n of 539 against a true 21,837, exiting 0. That is a 40x
# understatement in the pessimistic direction, which is the direction this project
# calls disqualifying.
#
# So a respondent's stratum is the cycle whose own files contain that SEQN. A
# pooled file contributes its variables to whichever cycle each respondent already
# belongs to. Where no cycle claims the SEQN, as with the renumbered pre-pandemic
# file, the pooled span stays its own stratum, correctly, because those identifiers
# genuinely cannot be matched to anyone.
#
# This is exact rather than an approximation: the SEQNs are the same people.
#
# Shared by build_nhanes.R and audit.R. A rule like this in two places has already
# drifted twice in this project and both times the audit blamed the data.

# Given a data frame of (stratum, unit_id, variable) rows for NHANES, move rows
# whose unit_id is claimed by a real cycle out of any pooled stratum.
nhanes_rehome <- function(pres, pooled = c("1999-2004", "2007-2012", "1999-2023",
                                           "2007-2012", "1988-2020")) {
  home <- pres[!pres$stratum %in% pooled, c("unit_id", "stratum")]
  home <- home[!duplicated(home$unit_id), ]
  names(home)[2] <- "home_stratum"

  moved <- merge(pres, home, by = "unit_id", all.x = TRUE, sort = FALSE)
  is_pooled <- moved$stratum %in% pooled & !is.na(moved$home_stratum)
  n <- sum(is_pooled)
  moved$stratum[is_pooled] <- moved$home_stratum[is_pooled]
  moved$home_stratum <- NULL
  if (n) cat("  rehomed", format(n, big.mark = ","), "pooled-file rows to their own cycle\n")
  unique(moved[, c("dataset", "stratum", "unit_id", "variable")])
}

# Which real cycles a pooled span draws its respondents from. Used by audit.R,
# which otherwise looks for a pooled table's variables under the pooled label and
# reports a correct index as missing.
NHANES_POOLED_PARTS <- list(
  "1999-2004" = c("1999-2000", "2001-2002", "2003-2004"),
  "2007-2012" = c("2007-2008", "2009-2010", "2011-2012")
)
