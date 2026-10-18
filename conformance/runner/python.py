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
from asop.gates import gate_outcome, gate_satisfied, validate_attestation, validate_gate
from asop.refusals import CODES as REFUSAL_CODES
from asop.revision import may_flip
from asop.chain import (ChainContractError, fanout_startable, join_state, plan_entry, plan_handoffs,
                        plan_join, plan_rework, requires_satisfied, version_in)
from asop.journal import chain_outcome, charged_failures, resume_point, stale_runs
from asop.chain import validate_approval
from asop.manifest import (adoption_impact, adoption_target, body_digest, check_pinned, manifest_resolver,
                           pin_manifest, require_schema, validate_adoption, verify_manifest)
from asop.sop import SopContractError, validate_asop

VECTORS = pathlib.Path(__file__).resolve().parents[1] / "vectors"
SUBMITTER = "conformance-runner"


def _resolver(library: dict):
    """The library lists each procedure's ACTIVE version: asop_id -> {version: body}."""
    def resolve(to):
        for version, body in (library.get(to["asop_id"]) or {}).items():
            if version_in(to, int(version)):
                return int(version), validate_asop(body, asop_id=to["asop_id"])
        return None
    return resolve


def _handoff_decisions(vector: dict, library: dict) -> list[str]:
    """Render each filing decision the way handoff.yaml spells it."""
    resolve = _resolver(library)
    if "pinned" in vector:
        # v3.6: the chain resolves from its manifest, not from what is active now
        pins = {"schema": "asop.chain-manifest/1", "chain": vector.get("chain", "R1"),
                "start": {"asop_id": vector["filer"], "version": next(iter(library[vector["filer"]]))},
                "members": [{"asop_id": a, "version": ver, "digest": "sha256:" + "0" * 64}
                            for a, ver in vector["pinned"].items()]}
        bodies = vector.get("store") or {}
        resolve = manifest_resolver(pins, lambda a, ver: (validate_asop(bodies[a][ver], asop_id=a)
                                                          if ver in (bodies.get(a) or {}) else None))

    filer = vector["filer"]
    (_, body), = library[filer].items()
    common = dict(outcome=vector.get("outcome"), outputs=vector.get("outputs") or {},
                  run_inputs=vector.get("run_inputs") or {}, resolve=resolve, chain=vector.get("chain", "R1"),
                  existing=vector.get("existing") or {}, attempt=vector.get("attempt", 1))
    body = validate_asop(body, asop_id=filer)
    if "join" in vector:
        # the join's filing, once join.yaml's question says it is ready
        decisions = plan_join(body, vector["join"], **common)
    else:
        decisions = plan_handoffs(body, requests=vector.get("requests") or [], approvals=vector.get("approvals"),
                                  output_digests=vector.get("output_digests"), **common)
    out = []
    for d in decisions:
        if d["action"] == "wait":
            out.append(f"wait {d['handoff']}")
        elif d["action"] == "file":
            line = f"file {d['to']['asop_id']}@{d['to']['version']} {d['subject']}"
            if d.get("depends_on"):
                line += " after " + ",".join(d["depends_on"])
            out.append(line)
        elif d["action"] == "duplicate":
            out.append(f"duplicate {d['natural_key']}")
        else:
            code = d["code"].split(":", 1)[0] + ":<rule>"
            if code not in REFUSAL_CODES:
                out.append(f"uncatalogued {d['code']}")
            else:
                out.append(f"refuse {d['code']}" + (f" -> {','.join(d['toward'])}" if d.get("toward") else ""))
    return out


# --- v3.6 questions -----------------------------------------------------------
# Each takes (vector, suite) and returns (matched, what happened). Every input is
# in the vector or the suite's shared `library`, as for hand-offs.

def _compare(got, want, what: str) -> tuple[bool, str]:
    return (True, f"{what}={got}") if got == want else (False, f"expected {what}={want}, got {got}")


def _entry(vector: dict, suite: dict) -> tuple[bool, str]:
    gates = validate_asop(suite["library"][vector["consumer"]], asop_id=vector["consumer"])["entry_gates"]
    got = plan_entry(gates, outcomes=vector.get("outcomes") or {}, producers=vector.get("producers") or {})
    rendered = [got["action"]] + [f"refile {r['natural_key']}" + (" charged" if r["charged"] else "")
                                  for r in got.get("refile", [])] \
        + [f"hold {h['gate']} {h['reason']}" for h in got.get("hold", [])]
    return _compare(rendered, vector["expect"], "decision")


def _rework(vector: dict, suite: dict) -> tuple[bool, str]:
    library = suite["library"]
    (_, body), = library[vector["filer"]].items()
    got = plan_rework(validate_asop(body, asop_id=vector["filer"]), outcome=vector.get("outcome"),
                      failed_step=vector.get("failed_step"), rounds_used=vector.get("rounds_used"),
                      outputs=vector.get("outputs"), owner=vector.get("owner"), resolve=_resolver(library),
                      chain=vector.get("chain", "R1"))
    if got is None:
        rendered = "none"
    elif got["action"] == "refuse":
        rendered = f"refuse {got['code']}"
    elif got["action"] == "escalate":
        rendered = "escalate rounds_exhausted"
    elif "from_step" in got:
        rendered = f"rework steps {got['from_step']}..{got['through_step']} round {got['round']}"
    else:
        rendered = (f"rework {got['natural_key']} round {got['round']}"
                    + (f" findings {got['prior_findings']}" if "prior_findings" in got else ""))
    return _compare(rendered, vector["expect"], "decision")


def _fanout(vector: dict, suite: dict) -> tuple[bool, str]:
    handoff = {"id": "each", "subject_from": {"concurrency": vector["concurrency"]} if "concurrency" in vector else {}}
    decisions = [{"action": "file", "handoff": "each", "natural_key": k, "depends_on": deps}
                 for k, deps in vector["filed"]]
    got = fanout_startable(handoff, decisions, done=vector.get("done") or [], started=vector.get("started") or [])
    return _compare(got, vector["startable"], "startable")


def _manifest(vector: dict, suite: dict) -> tuple[bool, str]:
    if "old" in vector:
        got = [f"{r['kind']} {r['name']} {r['effect']}" for r in adoption_impact(vector["old"], vector["new"])]
        return _compare(got, vector["impact"], "impact")
    library = vector.get("library") or suite.get("library") or {}
    resolve = _resolver(library)
    start = vector["start"]
    (_, body), = library[start].items()
    try:
        m = pin_manifest(start, int(next(iter(library[start]))), validate_asop(body, asop_id=start), resolve,
                         chain=vector.get("chain", "R1"))
    except ChainContractError as e:
        return _compare("refuse", vector["expect"], "pin") if vector["expect"] == "refuse" else \
            (False, f"refused: {e}")
    got = [f"{x['asop_id']}@{x['version']}" for x in m["members"]]
    return _compare(got, vector["expect"], "members")


def _journal(vector: dict, suite: dict) -> tuple[bool, str]:
    body = validate_asop(suite["procedure"])
    entries = [{"step": s, "event": ev, **({"fault_class": fc} if fc else {})}
               for s, ev, *rest in vector["entries"] for fc in [rest[0] if rest else None]]
    if "charged" in vector:
        return _compare(charged_failures(entries, step=vector["step"]), vector["charged"], "charged")
    r = resume_point(body, entries)
    got = {k: r[k] for k in vector["expect"]}
    return _compare(got, vector["expect"], "resume")


def _staleness(vector: dict, suite: dict) -> tuple[bool, str]:
    return _compare(stale_runs(vector["lineage"], vector.get("changed") or {}), vector["stale"], "stale")


def _chain_outcome(vector: dict, suite: dict) -> tuple[bool, str]:
    runs = [{"asop_id": r[0], "version": r[1], "subject": r[2], "attempt": r[3], "status": r[4]}
            for r in vector["runs"]]
    r = chain_outcome(runs, chain=vector.get("chain", "R1"), chain_gate=vector.get("chain_gate"))
    return _compare([r["verdict"], r["mixed_versions"]], [vector["verdict"], vector.get("mixed_versions", [])],
                    "verdict")


def _digest(vector: dict, suite: dict) -> tuple[bool, str]:
    import json
    doc = json.loads(vector["json"]) if "json" in vector else vector["document"]
    try:
        got = body_digest(doc, asop_id=vector.get("asop_id"))
    except (ChainContractError, SopContractError) as e:
        if vector.get("expect") == "refuse":
            return True, f"refuse ({vector.get('refusal', 'sop_refused')})"
        return False, f"refused: {e}"
    if vector.get("expect") == "refuse":
        return False, f"expected a refusal, got {got}"
    if "digest" in vector:
        return _compare(got, vector["digest"], "digest")
    return (False, f"digest must differ from {got}") if got == vector["not_digest"] else (True, "differs")


def _chain_store(vector: dict, suite: dict) -> tuple[bool, str]:
    """v3.6.1: what a store refuses when it records chain records (§10)."""
    store = suite.get("store") or {}
    q = vector["question"]
    try:
        try:
            if q == "manifest":
                def fetch(a, ver):
                    hit = (store.get(a) or {}).get(ver)
                    return None if hit is None else (hit["status"], hit["record"])
                verify_manifest(vector["document"], fetch)
            elif q == "target":
                adoption_target(vector["chain"], vector["to"], current=vector["current"], manifests=vector["manifests"])
            elif q == "adoption":
                try:
                    validate_adoption(vector["document"], old=vector["old"], new=vector["new"])
                except ChainContractError as e:
                    raise Refusal("adoption_invalid", str(e), "correct the record") from e
            elif q == "approval":
                try:
                    validate_approval(vector["document"])
                except ChainContractError as e:
                    raise Refusal("approval_invalid", str(e), "correct the record") from e
            elif q == "pinned":
                check_pinned(vector["manifest"], vector["asop_id"], vector.get("version"))
            elif q == "schema":
                require_schema(vector["document"])
            else:
                return False, f"unknown question {q!r}"
        except Refusal:
            raise
    except Refusal as r:
        if r.code not in REFUSAL_CODES:
            return False, f"{r.code!r} is not in asop.refusals"
        if vector["expect"] != "refuse":
            return False, f"refused {r.code}: {r.message}"
        return _compare(r.code, vector["refusal"], "refusal")
    return _compare("accept", vector["expect"], "outcome")


V36_QUESTIONS = {"digest": _digest, "chain-store": _chain_store, "entry": _entry, "rework": _rework, "fanout": _fanout, "manifest": _manifest,
                 "journal": _journal, "staleness": _staleness, "chain-outcome": _chain_outcome}


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
        elif artifact == "join":
            # May a join be filed yet? Only the parent run's own decisions and
            # the status of the runs they name are inputs (§3.7).
            got = join_state(vector["after"], decisions=vector.get("decisions") or [],
                             done=vector.get("done") or [], failed=vector.get("failed") or [])
            if got != vector["state"]:
                return False, f"expected state={vector['state']}, got {got}"
            return True, f"state={got}"
        elif artifact == "requires":
            # each run: [asop_id, subject, attempt, done]
            runs = [{"asop_id": r[0], "subject": r[1], "attempt": r[2], "done": r[3]}
                    for r in vector.get("runs") or []]
            reqs = validate_asop({"title": "t", "roles": {"w": {"kind": "agent"}},
                                  "steps": [{"name": "s", "role": "w", "purpose": "p",
                                             "gate": {"kind": "deterministic", "check": "c"}}],
                                  "requires": vector["requires"]})["requires"]
            got = requires_satisfied(reqs, chain=vector.get("chain", "R1"), runs=runs,
                                     approved=vector.get("approved") or [], approvals=vector.get("approvals"),
                                     digests=vector.get("digests"), chain_gates=vector.get("chain_gates"))
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
            if "outcome" in vector:   # v3.6: which of the outcomes, not only whether it passed
                outcome = gate_outcome(gate, attested)
                if outcome != vector["outcome"]:
                    return False, f"expected outcome={vector['outcome']}, got {outcome}"
                return True, f"satisfied={got} outcome={outcome}"
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
        elif artifact in V36_QUESTIONS:
            return V36_QUESTIONS[artifact](vector, suite or {})
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
