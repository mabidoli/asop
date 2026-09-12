# Contributing

ASOP is a specification. The most useful contributions are usually not prose.

## What helps most

**Conformance vectors.** The spec carries 109 normative assertions and ten are
covered by a vector — the README says so, and the suite admits a
pattern-matcher could still pass. A vector for an uncovered rule is worth more
than a paragraph explaining the rule better. `conformance/README.md` has the
format.

**A second implementation's disagreement.** If you implemented this and the
spec was ambiguous enough that you had to guess, that guess is a finding. Say
where you guessed and what you chose. Two implementations that agree because
one author wrote both is not evidence of a clear contract.

**Errata.** A rule whose prose and whose record contract disagree is a defect
even when both readings are reasonable.

## The one rule enforced mechanically

Nothing here names a real person, company, or deployment. A standard that
requires you to set a variable named after somebody's employer is not a
standard.

## Changing normative text

Normative changes land with a version bump and a CHANGELOG entry saying what an
existing implementation must now do differently. "Clarified" is not an answer
to that question — either behaviour changed or it did not.

Counts in documentation go stale: `conformance/README.md` carries the commands
that recount them. Run those rather than trusting the numbers.
