"""
equipment_checkdigit.py
=======================
Correction kernel for shipping / terminal equipment identifiers.

SCOPE (pass 2 of the build): pure check-digit LOGIC only.

This module:
  * computes ISO 6346 (and EN 13044 / ILU) mod-11 check digits,
  * computes UIC vehicle-number mod-10 (Luhn) check digits,
  * classifies a candidate token by identifier type, and
  * decides -- FAIL-LOUD -- whether to CORRECT, accept as VALID, FLAG, or treat
    as NOT_A_TARGET, given the SEMANTIC CONTEXT of the field the token came from.

It deliberately does NOT:
  * read or parse any file (EDIFACT / X12 / XML / SNX / .txt) -- that is the
    parser/transport layer's job; it locates identifiers and hands strings here,
  * mutate files or perform substitution -- the caller applies this kernel's
    verdict via surgical byte-level substitution,
  * touch a database, the network, or a UI.

Separation of concerns is intentional and load-bearing: parsing correctness,
correction logic, persistence, and visualization stay architecturally distinct.

Verified facts encoded here (project research passes 1-2):
  * ISO 6346 letter values: A=10..Z=38, skipping multiples of 11 (11, 22, 33);
    weights 2**0..2**9 = 1..512 over the 10 leading chars; remainder 10 -> 0.
    Worked example CSQU305438 -> check 3.
  * EN 13044 (ILU) reuses the ISO 6346 4+6+1 structure and -- per current public
    sources -- the SAME mod-11 calculation, distinguished by category letter
    A/B/D/E and the K compatibility case rather than U/J/Z.
    >>> FLAG: not yet confirmed against EN 13044-1 normative text. The shared
    >>> math means correction is identical; only the *label* differs. <<<
  * UIC 12-digit vehicle/wagon numbers: 12th digit = Luhn (mod 10) over digits
    1..11. Worked example 21 81 2471 217 -> check 3.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Set, Tuple


# --------------------------------------------------------------------------- #
# 1. Check-digit algorithms (pure functions: no classification, no I/O)
# --------------------------------------------------------------------------- #

# ISO 6346 letter -> numeric value. A=10, then +1 per letter, skipping any
# multiple of 11 (11, 22, 33). Digits keep their face value.
_ISO6346_LETTER_VALUES = {
    ch: val
    for ch, val in zip(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        # A   B   C   D   E   F   G   H   I   J   K   L   M
        [10, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 23, 24,
         # N   O   P   Q   R   S   T   U   V   W   X   Y   Z
         25, 26, 27, 28, 29, 30, 31, 32, 34, 35, 36, 37, 38],
    )
}


def iso6346_value(ch: str) -> int:
    """Numeric value of one ISO 6346 character (letter via table, digit as-is)."""
    if ch.isdigit():
        return int(ch)
    try:
        return _ISO6346_LETTER_VALUES[ch]
    except KeyError as exc:  # fail loud rather than silently coerce
        raise ValueError(f"Not a valid ISO 6346 character: {ch!r}") from exc


def iso6346_check_digit(body10: str) -> int:
    """
    ISO 6346 check digit from the 10 leading characters
    (3-letter owner + 1-letter category + 6-digit serial).

    Weighted sum of value * 2**position (position 0..9, left to right), mod 11;
    a remainder of 10 maps to a check digit of 0.

    >>> iso6346_check_digit("CSQU305438")
    3
    >>> iso6346_check_digit("APLU100000")   # remainder 10 -> 0
    0
    """
    if len(body10) != 10:
        raise ValueError(f"ISO 6346 body must be 10 chars, got {len(body10)}: {body10!r}")
    total = 0
    for i, ch in enumerate(body10):
        total += iso6346_value(ch) * (1 << i)   # 1<<i == 2**i: 1,2,4,...,512
    remainder = total % 11
    return 0 if remainder == 10 else remainder


def uic_check_digit(body11: str) -> int:
    """
    UIC vehicle/wagon check digit (12th digit) from the first 11 digits, via the
    Luhn (mod 10) algorithm.

    Right-to-left, the rightmost of the 11 is weighted x2, alternating x1;
    products >= 10 are digit-summed; check = (10 - (sum % 10)) % 10.

    >>> uic_check_digit("21812471217")
    3
    """
    if len(body11) != 11 or not body11.isdigit():
        raise ValueError(f"UIC body must be 11 digits, got {body11!r}")
    total = 0
    for i, ch in enumerate(reversed(body11)):
        product = int(ch) * (2 if i % 2 == 0 else 1)
        if product >= 10:
            product = product // 10 + product % 10   # digit-sum of the product
        total += product
    return (10 - (total % 10)) % 10


# --------------------------------------------------------------------------- #
# 2. Identifier taxonomy, field context, and result type
# --------------------------------------------------------------------------- #

class IdentifierType(str, Enum):
    ISO6346 = "iso6346"        # maritime container, category U/J/Z
    ILU = "ilu"                # EN 13044 intermodal loading unit, category A/B/D/E/K
    UIC = "uic"                # 12-digit rail vehicle / wagon number
    SIZE_TYPE = "size_type"    # ISO 6346 size/type code (descriptive)
    UNKNOWN = "unknown"


class FieldContext(str, Enum):
    """
    Semantic role of the field the token came from. This is what lets the kernel
    be fail-loud: a check-digit mismatch is a confident CORRECT only when the
    field is *declared* to carry an equipment number. In free text the same
    mismatch is FLAGGED, because the token may not be an identifier at all.

    Parser-layer mapping (pass 3+):
      EQUIPMENT_ID : EDIFACT EQD/C237/8260 ; X12 N7-01+N7-02 ; SNX eqid/id/unique-key
      SIZE_TYPE    : EDIFACT EQD/C224/8155 ; X12 N7-22       ; SNX container/@type
      RAIL_VEHICLE : a field known to carry UIC vehicle numbers
      FREE_TEXT    : a plain .txt regex hit -- low trust
    """
    EQUIPMENT_ID = "equipment_id"
    RAIL_VEHICLE = "rail_vehicle"
    SIZE_TYPE = "size_type"
    FREE_TEXT = "free_text"
    UNKNOWN = "unknown"


class Status(str, Enum):
    VALID = "valid"                          # recognized type, printed check already correct
    CORRECTED = "corrected"                  # printed check wrong (or absent); corrected value provided
    FLAGGED = "flagged"                      # ambiguous -- surfaced for review, NOT auto-applied
    NOT_A_TARGET = "not_a_target"            # size/type code etc. -- never a check-digit target
    INVALID_STRUCTURE = "invalid_structure"  # matches no known identifier shape


@dataclass
class CorrectionResult:
    raw: str                              # token exactly as received
    normalized: str                       # upper-cased, presentational spaces/dashes removed
    id_type: IdentifierType
    status: Status
    confidence: str                       # "high" | "low"
    reason: str                           # fail-loud, human-readable rationale
    printed_check: Optional[str] = None   # check digit found in the input (None if absent)
    computed_check: Optional[str] = None  # correct check digit (also a *suggestion* when FLAGGED)
    corrected: Optional[str] = None       # full corrected identifier; None unless CORRECTED

    @property
    def changed(self) -> bool:
        return self.status is Status.CORRECTED


# Structural patterns. NB: these match SHAPE only; classification still depends
# on the category letter and the field context.
_RE_BIC_LIKE = re.compile(r"^[A-Z]{3}([A-Z])\d{6}(\d)$")   # 4 letters + 7 digits (standard)
_RE_CONTAINER_SHAPED = re.compile(r"^[A-Z0-9]{4}\d{7}$")   # 4 alnum + 7 digits (incl. pseudo-prefix)
_RE_UIC = re.compile(r"^\d{12}$")
_ISO6346_CATEGORIES = frozenset("UJZ")
_ILU_CATEGORIES = frozenset("ABDEK")       # EN 13044 category letters incl. K (ISO-compat)
_OWNER_POLICIES = frozenset({"strict", "lenient"})   # accepted owner_policy values


# --------------------------------------------------------------------------- #
# 3. Classification + correction orchestration (the fail-loud router)
# --------------------------------------------------------------------------- #

def normalize(token: str) -> str:
    """
    Upper-case and remove purely presentational whitespace/hyphens, e.g.
    'APLU 919281 2' -> 'APLU9192812' and '21 81 2471 217-3' -> '218124712173'.
    Internal structure is otherwise untouched.
    """
    return re.sub(r"[\s\-]", "", token).upper()


def _evaluate_bic(body10: str) -> Tuple[IdentifierType, str, bool]:
    """
    Evaluate a 10-char BIC/ILU body. Returns (id_type, computed_check, nonstandard).
    `nonstandard` is True when the category letter is neither ISO 6346 nor ILU.
    """
    category = body10[3]
    computed = str(iso6346_check_digit(body10))   # shared math for ISO 6346 and ILU
    if category in _ISO6346_CATEGORIES:
        return IdentifierType.ISO6346, computed, False
    if category in _ILU_CATEGORIES:
        return IdentifierType.ILU, computed, False
    return IdentifierType.UNKNOWN, computed, True


def correct_identifier(
    token: str,
    context: FieldContext = FieldContext.UNKNOWN,
    *,
    known_owner_prefixes: Optional[Set[str]] = None,
    owner_policy: str = "strict",
    policy=None,
) -> CorrectionResult:
    """
    Classify a candidate identifier and decide what to do with it.

    `context` is the semantic role of the source field (see FieldContext).
    `known_owner_prefixes`, if supplied by the enrichment layer (e.g. the BIC
    owner-code register), lets free-text matches be corroborated; the kernel
    itself ships no registry.
    `owner_policy` governs container-shaped tokens whose owner code is NOT three
    letters (e.g. carrier pseudo-prefixes like "L01U..." in synthetic / SOC data):
      "strict"  -> FLAG them (fail-loud; never auto-correct a non-conformant owner)
      "lenient" -> correct them using the standard mod-11 (caller asserts the
                   pseudo-prefix scheme uses ISO 6346 check-digit math)
    `policy`, if given (a policy.Policy), is the OPERATOR's per-prefix rule layer.
    It is resolved for this token's owner prefix and mapped onto the primitives
    above: an allow-listed prefix is added to the corroborated set, a per-prefix
    override replaces owner_policy, and a deny-listed prefix forces a FLAG result
    (a human must eyeball it) regardless of its check digit. Policy is a thin
    layer over these existing hooks; it never bypasses the math.
    """
    if policy is not None:
        prefix = normalize(token)[:3]
        t = policy.resolve(prefix)
        owner_policy = t.owner_policy
        if t.corroborated:
            known_owner_prefixes = (known_owner_prefixes or set()) | {prefix}
        if t.force_flag:
            # Run the normal evaluation to get the computed check for the
            # operator's review, then downgrade any disposition to FLAGGED.
            base = correct_identifier(token, context,
                                      known_owner_prefixes=known_owner_prefixes,
                                      owner_policy=owner_policy)
            if base.status in (Status.VALID, Status.CORRECTED, Status.FLAGGED):
                return CorrectionResult(
                    base.raw, base.normalized, base.id_type, Status.FLAGGED,
                    confidence=base.confidence,
                    reason=(f"Owner prefix {prefix!r} is on the operator deny list "
                            f"(review required, never auto-corrected). "
                            + (base.reason or "")),
                    printed_check=base.printed_check, computed_check=base.computed_check)
            return base                                # INVALID_STRUCTURE / NOT_A_TARGET unchanged

    if owner_policy not in _OWNER_POLICIES:
        raise ValueError(
            f"owner_policy must be one of {sorted(_OWNER_POLICIES)}, got {owner_policy!r}")
    raw = token
    norm = normalize(token)

    # --- size/type fields are never a correction target -------------------- #
    if context is FieldContext.SIZE_TYPE:
        return CorrectionResult(
            raw, norm, IdentifierType.SIZE_TYPE, Status.NOT_A_TARGET,
            confidence="high",
            reason="Field is an ISO 6346 size/type code (descriptive); not a check-digit target.",
        )

    # --- 4 letters + 7 digits: ISO 6346 container or EN 13044 ILU ---------- #
    m = _RE_BIC_LIKE.match(norm)
    if m:
        category, printed = m.group(1), m.group(2)
        body10 = norm[:10]
        id_type, computed, nonstandard = _evaluate_bic(body10)
        corrected_full = body10 + computed

        if nonstandard:
            # Right shape, non-standard category letter. Could be a data error
            # or a non-container token. Fail loud: never auto-correct.
            return CorrectionResult(
                raw, norm, IdentifierType.UNKNOWN, Status.FLAGGED, confidence="low",
                reason=(f"Shape matches a container number but category letter {category!r} "
                        f"is neither ISO 6346 (U/J/Z) nor ILU (A/B/D/E/K). "
                        f"Suggested check digit {computed}; not auto-applied."),
                printed_check=printed, computed_check=computed,
            )

        return _decide_bic(raw, norm, id_type, context, printed, computed,
                           corrected_full, known_owner_prefixes)

    # --- 4 alnum + 7 digits: container-shaped but owner is NOT 3 letters ---- #
    # e.g. carrier pseudo-prefixes "L01U1150107" in synthetic / SOC datasets.
    if _RE_CONTAINER_SHAPED.match(norm):
        body10, printed = norm[:10], norm[10]
        owner = norm[:3]
        computed = str(iso6346_check_digit(body10))   # math runs on digits-as-values
        nonconformant = (
            f"Container-shaped but owner code {owner!r} is not three letters "
            f"(ISO 6346 requires a 3-letter owner) -- likely a synthetic / SOC / "
            f"carrier pseudo-prefix."
        )
        if owner_policy == "lenient" and context in (FieldContext.EQUIPMENT_ID, FieldContext.UNKNOWN):
            if printed == computed:
                return CorrectionResult(
                    raw, norm, IdentifierType.ISO6346, Status.VALID, confidence="low",
                    reason=nonconformant + f" Check digit {printed} is consistent under standard mod-11 (lenient policy).",
                    printed_check=printed, computed_check=computed,
                )
            return CorrectionResult(
                raw, norm, IdentifierType.ISO6346, Status.CORRECTED, confidence="low",
                reason=nonconformant + f" Corrected {printed} -> {computed} under standard mod-11 (lenient policy).",
                printed_check=printed, computed_check=computed, corrected=body10 + computed,
            )
        return CorrectionResult(
            raw, norm, IdentifierType.ISO6346, Status.FLAGGED, confidence="low",
            reason=nonconformant + f" Suggested check digit {computed} (standard mod-11); "
                                   f"not auto-applied -- confirm prefix policy.",
            printed_check=printed, computed_check=computed,
        )

    # --- 12 digits: possible UIC vehicle number ---------------------------- #
    if _RE_UIC.match(norm):
        body11, printed = norm[:11], norm[11]
        computed = str(uic_check_digit(body11))
        return _decide_uic(raw, norm, context, printed, computed, body11 + computed)

    # --- nothing recognized ------------------------------------------------ #
    return CorrectionResult(
        raw, norm, IdentifierType.UNKNOWN, Status.INVALID_STRUCTURE, confidence="high",
        reason="Does not match any known equipment-identifier structure.",
    )


def _decide_bic(raw, norm, id_type, context, printed, computed, corrected_full,
                known_owner_prefixes) -> CorrectionResult:
    label = id_type.value.upper()
    owner = norm[:3]

    if printed == computed:
        return CorrectionResult(
            raw, norm, id_type, Status.VALID, confidence="high",
            reason=f"{label} check digit is correct.",
            printed_check=printed, computed_check=computed,
        )

    # printed != computed -> correction candidate. Trust depends on context.
    # A 4-alpha + valid-category + 7-digit token is highly specific (low false-
    # positive rate), so EQUIPMENT_ID and UNKNOWN are correctable; only FREE_TEXT
    # demands extra corroboration (it could be a coincidental string).
    if context in (FieldContext.EQUIPMENT_ID, FieldContext.UNKNOWN):
        return CorrectionResult(
            raw, norm, id_type, Status.CORRECTED, confidence="high",
            reason=f"{label} check digit {printed} -> {computed}.",
            printed_check=printed, computed_check=computed, corrected=corrected_full,
        )

    if context is FieldContext.FREE_TEXT:
        if known_owner_prefixes is not None and owner in known_owner_prefixes:
            return CorrectionResult(
                raw, norm, id_type, Status.CORRECTED, confidence="high",
                reason=(f"{label} check digit {printed} -> {computed}; "
                        f"owner prefix {owner!r} corroborated against registry."),
                printed_check=printed, computed_check=computed, corrected=corrected_full,
            )
        return CorrectionResult(
            raw, norm, id_type, Status.FLAGGED, confidence="low",
            reason=(f"Free-text token has the {label} shape but a failing check digit "
                    f"({printed}; expected {computed}). Not auto-corrected -- may not be a "
                    f"container number. Supply an owner registry to corroborate."),
            printed_check=printed, computed_check=computed,
        )

    # RAIL_VEHICLE context but BIC shape -> expectation/structure mismatch.
    return CorrectionResult(
        raw, norm, id_type, Status.FLAGGED, confidence="low",
        reason=(f"Token has {label} shape but the field was declared RAIL_VEHICLE. "
                f"Context/structure mismatch; not auto-corrected."),
        printed_check=printed, computed_check=computed,
    )


def _decide_uic(raw, norm, context, printed, computed, corrected_full) -> CorrectionResult:
    if context is not FieldContext.RAIL_VEHICLE:
        # A bare 12-digit run is NOT self-identifying as UIC. Refuse to auto-
        # correct outside a field declared to carry rail vehicle numbers.
        verdict = "matches" if printed == computed else f"fails (printed {printed}, expected {computed})"
        return CorrectionResult(
            raw, norm, IdentifierType.UIC, Status.FLAGGED, confidence="low",
            reason=(f"12-digit UIC candidate, but field context is not RAIL_VEHICLE. "
                    f"Luhn check {verdict}; not auto-corrected -- a 12-digit run is not "
                    f"self-identifying."),
            printed_check=printed, computed_check=computed,
        )

    if printed == computed:
        return CorrectionResult(
            raw, norm, IdentifierType.UIC, Status.VALID, confidence="high",
            reason="UIC Luhn check digit is correct.",
            printed_check=printed, computed_check=computed,
        )
    return CorrectionResult(
        raw, norm, IdentifierType.UIC, Status.CORRECTED, confidence="high",
        reason=f"UIC Luhn check digit {printed} -> {computed}.",
        printed_check=printed, computed_check=computed, corrected=corrected_full,
    )


# --------------------------------------------------------------------------- #
# 4. Adapter for split identifiers (X12 N7-01 initial + N7-02 number)
# --------------------------------------------------------------------------- #

def correct_x12_equipment(
    initial: str,
    number: str,
    printed_check: Optional[str] = None,
    *,
    known_owner_prefixes: Optional[Set[str]] = None,
    policy=None,
) -> CorrectionResult:
    """
    Assemble an ISO 6346 number from X12 N7-01 (Equipment Initial, 4 alpha) and
    N7-02 (Equipment Number, serial), with the optional N7-18 (element 761) check
    digit, then evaluate under EQUIPMENT_ID trust.

    The caller writes the resulting `computed_check` back to N7-18. If N7-18 was
    not transmitted (empty), the result is CORRECTED with printed_check=None,
    signalling "populate the missing check digit".
    """
    init = normalize(initial)
    num = re.sub(r"\D", "", number)

    if policy is not None:
        t = policy.resolve(init[:3])
        if t.corroborated:
            known_owner_prefixes = (known_owner_prefixes or set()) | {init[:3]}
        if t.force_flag:
            base = correct_x12_equipment(initial, number, printed_check,
                                         known_owner_prefixes=known_owner_prefixes)
            if base.status in (Status.VALID, Status.CORRECTED, Status.FLAGGED):
                return CorrectionResult(
                    base.raw, base.normalized, base.id_type, Status.FLAGGED,
                    confidence=base.confidence,
                    reason=(f"Owner prefix {init[:3]!r} is on the operator deny list "
                            f"(review required, never auto-corrected). " + (base.reason or "")),
                    printed_check=base.printed_check, computed_check=base.computed_check)
            return base

    if len(init) != 4 or not init.isalpha():
        return CorrectionResult(
            f"{initial}/{number}", init + num, IdentifierType.UNKNOWN,
            Status.INVALID_STRUCTURE, confidence="high",
            reason=f"X12 N7-01 expected 4 alpha (got {initial!r}); cannot form ISO 6346 number.",
        )
    if len(num) > 6:
        return CorrectionResult(
            f"{initial}/{number}", init + num, IdentifierType.UNKNOWN,
            Status.FLAGGED, confidence="low",
            reason=f"X12 N7-02 has {len(num)} digits; not a standard 6-digit ISO 6346 serial.",
        )

    body10 = init + num.zfill(6)
    id_type, computed, nonstandard = _evaluate_bic(body10)

    if nonstandard:
        return CorrectionResult(
            f"{initial}/{number}", body10, IdentifierType.UNKNOWN, Status.FLAGGED,
            confidence="low",
            reason=(f"X12 equipment category {init[3]!r} is neither ISO 6346 nor ILU; "
                    f"suggested check digit {computed}; not auto-applied."),
            computed_check=computed,
        )

    label = id_type.value.upper()
    if printed_check in (None, ""):
        return CorrectionResult(
            f"{initial}/{number}", body10, id_type, Status.CORRECTED, confidence="high",
            reason=f"X12 N7-18 (check digit) not transmitted; computed {computed}. Write it to N7-18.",
            printed_check=None, computed_check=computed, corrected=body10 + computed,
        )
    if printed_check == computed:
        return CorrectionResult(
            f"{initial}/{number}", body10 + printed_check, id_type, Status.VALID,
            confidence="high", reason=f"{label} check digit (N7-18) is correct.",
            printed_check=printed_check, computed_check=computed,
        )
    return CorrectionResult(
        f"{initial}/{number}", body10 + printed_check, id_type, Status.CORRECTED,
        confidence="high", reason=f"{label} check digit (N7-18) {printed_check} -> {computed}.",
        printed_check=printed_check, computed_check=computed, corrected=body10 + computed,
    )


# --------------------------------------------------------------------------- #
# 5. Smoke demo (not the app; just a quick self-check when run directly)
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    print("ISO 6346 / ILU / UIC correction kernel -- smoke demo\n")
    samples = [
        ("APLU9192812", FieldContext.EQUIPMENT_ID),   # SNX fixture: bad -> APLU9192819
        ("CBHU2426018", FieldContext.EQUIPMENT_ID),   # SNX fixture: valid
        ("CSQU3054383", FieldContext.EQUIPMENT_ID),   # canonical ISO 6346 example
        ("APLU9192812", FieldContext.FREE_TEXT),      # same token, low-trust source -> flagged
        ("218124712173", FieldContext.RAIL_VEHICLE),  # valid UIC
        ("218124712170", FieldContext.RAIL_VEHICLE),  # bad UIC -> ...173
        ("218124712170", FieldContext.UNKNOWN),       # UIC shape, no rail context -> flagged
        ("L5G1", FieldContext.SIZE_TYPE),             # size/type -> not a target
    ]
    for tok, ctx in samples:
        r = correct_identifier(tok, ctx)
        arrow = f" -> {r.corrected}" if r.corrected else ""
        print(f"{tok:<14} [{ctx.value:<12}] {r.id_type.value:<9} {r.status.value:<17}{arrow}")
        print(f"               {r.reason}")


# ─────────────────────────── worked-math explainer ─────────────────────────
def explain(token: str) -> dict:
    """
    Single-number calculator: classify the token's shape, run the matching
    check-digit algorithm, and return every intermediate step so a UI (or a
    trainee) can see the work. Pure read-only companion to correct_identifier;
    mirrors iso6346_check_digit / uic_check_digit exactly.

    Shapes:
      4 letters + 6 digits  -> ISO 6346 / ILU, compute the check digit
      4 letters + 7 digits  -> ISO 6346 / ILU, verify the 11th char
      11 digits             -> UIC wagon, compute the 12th digit
      12 digits             -> UIC wagon, verify the 12th digit
    Anything else           -> {"ok": False, "error": ...}  (no exception)
    """
    norm = normalize(token or "")
    if not norm:
        return {"ok": False, "error": "Empty input."}

    if norm.isdigit() and len(norm) in (11, 12):
        body, printed = norm[:11], (norm[11] if len(norm) == 12 else None)
        steps, total = [], 0
        for i, ch in enumerate(reversed(body)):          # mirrors uic_check_digit
            product = int(ch) * (2 if i % 2 == 0 else 1)
            if product >= 10:
                product = product // 10 + product % 10
            steps.append({"char": ch, "weight": 2 if i % 2 == 0 else 1,
                          "product": product})
            total += product
        computed = (10 - (total % 10)) % 10
        verdict = ("computed" if printed is None
                   else "valid" if printed == str(computed) else "mismatch")
        return {"ok": True, "kind": "uic", "normalized": norm, "body": body,
                "printed": printed, "steps": steps, "sum": total,
                "computed": computed, "verdict": verdict,
                "full": body + str(computed)}

    if re.fullmatch(r"[A-Z]{4}\d{6,7}", norm):
        body, printed = norm[:10], (norm[10] if len(norm) == 11 else None)
        chars, total = [], 0
        for i, ch in enumerate(body):                    # mirrors iso6346_check_digit
            v = iso6346_value(ch)
            w = 2 ** i
            chars.append({"char": ch, "value": v, "weight": w, "product": v * w})
            total += v * w
        mod = total % 11
        computed = 0 if mod == 10 else mod
        cat = body[3]
        category_set = ("iso6346" if cat in "UJZ"
                        else "ilu" if cat in "ABDEK" else "unknown")
        verdict = ("computed" if printed is None
                   else "valid" if printed == str(computed) else "mismatch")
        return {"ok": True, "kind": "iso6346_ilu", "normalized": norm,
                "body": body, "printed": printed, "chars": chars, "sum": total,
                "mod": mod, "remainder_ten": mod == 10, "computed": computed,
                "category": cat, "category_set": category_set,
                "verdict": verdict, "full": body + str(computed)}

    return {"ok": False,
            "error": "Enter 4 letters + 6-7 digits (ISO 6346 / ILU) "
                     "or 11-12 digits (UIC wagon)."}
