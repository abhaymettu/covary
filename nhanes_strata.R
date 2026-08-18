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
# reported a joint n of 539 against a true 21,846, exiting 0. That is a 40x
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
# NHANES_POOLED_PARTS below is the whole rule, and it is genuinely shared:
# build_nhanes.R rehomes with it and audit.R sums across the same cycles.
#
# An earlier version of this file also defined a nhanes_rehome() helper and the
# header said the rule was shared. Nothing ever called it, build_nhanes.R had its
# own inline copy, and the two had already drifted: the dead function listed
# "2007-2012" twice and two spans the live rule did not know about. A file that
# describes code nobody runs is worse than no file, because it reads as the
# authority. Deleted rather than wired up, since the live version is four lines.


NHANES_POOLED_PARTS <- list(
  "1999-2004" = c("1999-2000", "2001-2002", "2003-2004"),
  "2007-2012" = c("2007-2008", "2009-2010", "2011-2012")
)
