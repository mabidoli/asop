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

VECTORS = pathlib.Path(__file__).resolve().parents[1] / "vectors"
SUBMITTER = "conformance-runner"


def run_one(artifact: str, vector: dict) -> tuple[bool, str]:
    """Return (matched, what actually happened)."""
    try:
        if artifact == "gate":
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
        else:
            return False, f"runner does not know artifact {artifact!r}"
        got, code = "accept", None
    except Refusal as refusal:
        got, code = "refuse", refusal.code
    except Exception as exc:  # a crash is not a refusal, and must not read as one
        return False, f"raised {type(exc).__name__}: {exc}"

    want = vector["expect"]
    if got != want:
        return False, f"expected {want}, got {got}" + (f" ({code})" if code else "")
    if want == "refuse" and vector.get("refusal") and code != vector["refusal"]:
        return False, f"refused with {code!r}, vector requires {vector['refusal']!r}"
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
            matched, detail = run_one(artifact, vector)
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
