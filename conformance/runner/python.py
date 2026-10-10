"""Reference conformance runner — Python.

Reads every vector in ../vectors and puts each document through this
implementation's validators, then reports whether the outcome matches what the
vector requires. Any implementation in any language can be conforming by doing
the same thing: read the YAML, validate, compare accept/refuse and the refusal
code. That is the whole contract of a runner — roughly a hundred lines, which is
the point.

Usage:  python conformance/runner/python.py [--verbose]
Exit 0 when every vector matches; 1 otherwise, so CI can gate on it.
"""

from __future__ import annotations

import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from asop.errors import Refusal
from asop.gates import gate_satisfied, validate_attestation, validate_gate
from asop.refusals import CODES as REFUSAL_CODES
from asop.revision import may_flip
from asop.chain import plan_handoffs, requires_satisfied, version_in
from asop.sop import SopContractError, validate_asop

VECTORS = pathlib.Path(__file__).resolve().parents[1] / "vectors"
SUBMITTER = "conformance-runner"


def _handoff_decisions(vector: dict, library: dict) -> list[str]:
    """Render each filing decision the way handoff.yaml spells it."""
    def resolve(to):
        for version, body in (library.get(to["asop_id"]) or {}).items():
            if version_in(to, int(version)):
                return int(version), validate_asop(body, asop_id=to["asop_id"])
        return None

    filer = vector["filer"]
    (_, body), = library[filer].items()
    decisions = plan_handoffs(
        validate_asop(body, asop_id=filer), outcome=vector.get("outcome"),
        outputs=vector.get("outputs") or {}, run_inputs=vector.get("run_inputs") or {},
        requests=vector.get("requests") or [], resolve=resolve, chain=vector.get("chain", "R1"),
        existing=vector.get("existing") or {}, attempt=vector.get("attempt", 1),
    )
    out = []
    for d in decisions:
        if d["action"] == "file":
            out.append(f"file {d['to']['asop_id']}@{d['to']['version']} {d['subject']}")
        elif d["action"] == "duplicate":
            out.append(f"duplicate {d['natural_key']}")
        else:
            code = d["code"].split(":", 1)[0] + ":<rule>"
            if code not in REFUSAL_CODES:
                out.append(f"uncatalogued {d['code']}")
            else:
                out.append(f"refuse {d['code']}")
    return out


def run_one(artifact: str, vector: dict, suite: dict | None = None) -> tuple[bool, str]:
    """Return (matched, what actually happened)."""
    detail_message = ""
    try:
        if artifact == "handoff":
            # A filing question (§3.7): every input is in the vector and the
            # suite's shared library, so two engines must agree line for line.
            got = _handoff_decisions(vector, (suite or {}).get("library") or {})
            want = vector["expect"]
            if got != want:
                return False, f"expected {want}, got {got}"
            return True, f"{len(got)} decision(s)"
        elif artifact == "requires":
            # each run: [asop_id, subject, attempt, done, processed, refused_toward?]
            runs = [{"asop_id": r[0], "subject": r[1], "attempt": r[2], "done": r[3], "processed": r[4],
                     "refused_toward": r[5] if len(r) > 5 else []} for r in vector.get("runs") or []]
            got = requires_satisfied(vector["requires"], runs=runs, predecessors=vector.get("predecessors") or {},
                                     approved=vector.get("approved") or [])
            if got != vector["satisfied"]:
                return False, f"expected satisfied={vector['satisfied']}, got {got}"
            return True, f"satisfied={got}"
        elif artifact == "asop":
            # A record error is `sop_refused` (§10), whichever module found it.
            try:
                validate_asop(vector["document"], asop_id=vector.get("asop_id"))
            except SopContractError as e:
                raise Refusal("sop_refused", str(e), "fix the record") from e
        elif artifact == "gate":
            validate_gate(vector["document"])
        elif artifact == "attestation":
            gate = validate_gate(vector["gate"])
            validate_attestation(vector["document"], gate=gate, submitted_by=SUBMITTER)
        elif artifact == "satisfaction":
            # A different question: not "is this document well formed" but
            # "may the step be recorded as done". Each attestation is validated
            # first, because evidence that would be refused cannot answer
            # anything — a vector that got that wrong would be testing the
            # runner's tolerance rather than the implementation's judgement.
            gate = validate_gate(vector["gate"])
            attested = [
                validate_attestation(a, gate=gate, submitted_by=SUBMITTER)
                for a in vector["attestations"]
            ]
            got = gate_satisfied(gate, attested)
            want = vector["satisfied"]
            if got != want:
                return False, f"expected satisfied={want}, got {got}"
            return True, f"satisfied={got}"
        elif artifact == "ownership":
            # Not a document question either: given a run's owner and a store
            # asking to flip one of its beads, may it? §7.2.
            got = may_flip(vector["actor_store"], vector["run_owner"])
            want = vector["may_flip"]
            if got != want:
                return False, f"expected may_flip={want}, got {got}"
            expected_code = vector.get("refusal")
            if expected_code and expected_code not in REFUSAL_CODES:
                # The vector names a code the contract does not define. One
                # implementation would ship `not_owner` and pass anyway.
                return False, f"{expected_code!r} is not in asop.refusals"
            return True, f"may_flip={got}" + (f" ({expected_code})" if expected_code else "")
        else:
            return False, f"runner does not know artifact {artifact!r}"
        got, code = "accept", None
    except Refusal as refusal:
        got, code = "refuse", refusal.code
        detail_message = refusal.message
    except Exception as exc:  # a crash is not a refusal, and must not read as one
        return False, f"raised {type(exc).__name__}: {exc}"

    want = vector["expect"]
    if got != want:
        return False, f"expected {want}, got {got}" + (f" ({code})" if code else "")
    if want == "refuse" and vector.get("refusal") and code != vector["refusal"]:
        return False, f"refused with {code!r}, vector requires {vector['refusal']!r}"

    # `reaches` is NON-NORMATIVE and deliberately so. One refusal code covers
    # many distinct rules — gate_invalid alone guards 38 of them — so comparing
    # codes cannot tell whether a vector reached the rule it names. Two vectors
    # were found green while testing a rule other than the one their `because`
    # described, and nothing in the suite could have caught it.
    #
    # A conforming implementation is NOT required to match these strings: its
    # wording is its own. The reference runner checks them because it is the
    # implementation whose messages they quote, and because a vector that
    # silently drifts onto a different rule is worse than a missing one.
    if reaches := vector.get("reaches"):
        message = detail_message or ""
        if reaches.lower() not in message.lower():
            return False, (
                f"refused correctly with {code!r}, but reached the wrong rule: "
                f"expected a message containing {reaches!r}, got {message[:70]!r}"
            )
    return True, got if not code else f"{got} ({code})"


def main() -> int:
    verbose = "--verbose" in sys.argv
    total = failed = 0
    for path in sorted(VECTORS.glob("*.yaml")):
        suite = yaml.safe_load(path.read_text())
        artifact = suite["artifact"]
        print(f"\n{path.name} — {len(suite['vectors'])} vectors ({artifact})")
        for vector in suite["vectors"]:
            total += 1
            matched, detail = run_one(artifact, vector, suite)
            if not matched:
                failed += 1
                print(f"  FAIL  {vector['id']:<34} {detail}")
                print(f"        because: {vector.get('because', '')}")
            elif verbose:
                print(f"  ok    {vector['id']:<34} {detail}")

    print(f"\n{total - failed}/{total} vectors matched")
    if failed:
        print(f"{failed} did NOT — a vector and an implementation disagree, and one of them is wrong")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
