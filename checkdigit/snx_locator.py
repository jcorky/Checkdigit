"""
snx_locator.py
==============
Pass 5, format #2: locate container identifiers in SNX container XML.

In SNX the container number is denormalized across several attributes that
MUST stay in sync:
    container/@eqid , equipment/@eqid , unit/@id , unit/@unique-key
It is NOT in container/@type or equipment/@type (ISO 6346 size/type codes), nor
in @owner/@operator/@line, nor in carrier/@id (a vessel/service code).

Approach (per the project's XML guidance -- parse to validate, regex to locate,
never re-serialize):
  1. HARDEN + parse with the stdlib to confirm well-formedness and to enumerate,
     per container number, how many identifier-bearing attributes carry it.
  2. Locate exact byte offsets of each identifier-as-attribute-value via a scoped
     scan, so substitution is surgical and keeps every occurrence in sync.
  3. Cross-check located vs parsed occurrence counts and fail loud on divergence.

XXE / entity hardening (no third-party dependency required):
  * Reject any input containing a DOCTYPE or ENTITY declaration outright. Legit
    SNX is XSD-schema-based and never carries a DTD, so this single rule blocks
    XXE, external-DTD retrieval, and billion-laughs entity expansion.
  * Parse with the stdlib (expat does not fetch external resources by default).
  * If defusedxml is installed it is used as an additional layer; it is OPTIONAL
    because the protection above does not depend on it.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from equipment_checkdigit import (
    correct_identifier, CorrectionResult, FieldContext, _OWNER_POLICIES,
)


class XmlSecurityError(ValueError):
    """Raised when untrusted XML contains a construct we refuse to parse."""


class IntegrityError(ValueError):
    """Raised when the parsed structure and the located byte spans disagree."""


_DOCTYPE_RE = re.compile(r"<!DOCTYPE", re.IGNORECASE)
_ENTITY_RE = re.compile(r"<!ENTITY", re.IGNORECASE)

# (element localname, attribute localname) pairs that carry the container number.
_EQID_ATTRS: Set[Tuple[str, str]] = {
    ("container", "eqid"),
    ("equipment", "eqid"),
    ("unit", "id"),
    ("unit", "unique-key"),
    ("line-discharge-list", "unit-id"),   # COPRAR discharge XML (one eqid per element)
}
# size/type lives here -- never a correction target (collected for the report only).
_SIZE_TYPE_ATTRS: Set[Tuple[str, str]] = {("container", "type"), ("equipment", "type")}

# attribute names whose value we will scan for, by name (value match does the rest)
_SCAN_ATTR_NAMES = sorted({a for _, a in _EQID_ATTRS})   # ['eqid','id','unique-key']


def harden_and_parse(text: str) -> ET.Element:
    """Reject DTD/entities, then parse with a hardened stdlib (or defusedxml) parser."""
    if _DOCTYPE_RE.search(text) or _ENTITY_RE.search(text):
        raise XmlSecurityError(
            "Refusing to parse XML containing a DOCTYPE/ENTITY declaration "
            "(XXE / external-DTD / entity-expansion hardening). Legitimate SNX has none.")
    try:
        import defusedxml.ElementTree as DET     # optional extra layer
        return DET.fromstring(text)
    except ImportError:
        return ET.fromstring(text)               # expat: no external entity fetch by default


def _localname(name: str) -> str:
    """Strip any '{namespace}' (elements) or 'prefix:' (attrs) qualifier."""
    return name.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _element_of(text: str, attr_match_start: int) -> str:
    """Localname of the element that owns the attribute at the given match start."""
    lt = text.rfind("<", 0, attr_match_start)
    if lt == -1:
        return "?"
    m = re.match(r"[A-Za-z0-9_:.-]+", text[lt + 1: lt + 60])
    return _localname(m.group(0)) if m else "?"


@dataclass
class SnxRef:
    eqid: str
    occurrences: List[Tuple[int, str]]   # (byte offset of value, label "element/@attr")


def _parsed_counts(root: ET.Element) -> Tuple[Dict[str, int], Set[str]]:
    """Return ({eqid: count of id-bearing attributes}, {size/type codes seen})."""
    counts: Dict[str, int] = {}
    size_types: Set[str] = set()
    for el in root.iter():
        ln = _localname(el.tag)
        for attr, val in el.attrib.items():
            an = _localname(attr)
            if not val:
                continue
            if (ln, an) in _EQID_ATTRS:
                counts[val] = counts.get(val, 0) + 1
            elif (ln, an) in _SIZE_TYPE_ATTRS:
                size_types.add(val)
    return counts, size_types


def _locate_occurrences(text: str, eqid: str) -> List[Tuple[int, str]]:
    """Every (value_offset, 'element/@attr') where `eqid` is an id-bearing attr value."""
    pat = re.compile(
        r'\b(?P<attr>' + "|".join(_SCAN_ATTR_NAMES) + r')="(?P<val>' + re.escape(eqid) + r')"')
    out: List[Tuple[int, str]] = []
    for m in pat.finditer(text):
        element = _element_of(text, m.start())
        out.append((m.start("val"), f"{element}/@{m.group('attr')}"))
    return out


def locate_equipment(text: str) -> List[SnxRef]:
    """Validate (hardened), enumerate container numbers, and locate every synced occurrence."""
    root = harden_and_parse(text)
    counts, _ = _parsed_counts(root)
    refs: List[SnxRef] = []
    for eqid, parsed_n in counts.items():
        occ = _locate_occurrences(text, eqid)
        if len(occ) != parsed_n:
            raise IntegrityError(
                f"occurrence mismatch for {eqid!r}: parser saw {parsed_n} id-bearing "
                f"attribute(s) but the byte scan located {len(occ)}. Refusing to edit.")
        refs.append(SnxRef(eqid=eqid, occurrences=occ))
    return refs


def size_type_codes(text: str) -> List[str]:
    """ISO 6346 size/type codes present (left untouched). For reporting/context."""
    _, st = _parsed_counts(harden_and_parse(text))
    return sorted(st)


def evaluate_text(text: str, *, owner_policy: str = "strict", policy=None
                  ) -> List[Tuple[SnxRef, CorrectionResult]]:
    """Locate container numbers and evaluate each via the kernel (EQUIPMENT_ID context)."""
    if owner_policy not in _OWNER_POLICIES:
        raise ValueError(
            f"owner_policy must be one of {sorted(_OWNER_POLICIES)}, got {owner_policy!r}")
    out: List[Tuple[SnxRef, CorrectionResult]] = []
    for ref in locate_equipment(text):
        out.append((ref, correct_identifier(
            ref.eqid, FieldContext.EQUIPMENT_ID, owner_policy=owner_policy, policy=policy)))
    return out
