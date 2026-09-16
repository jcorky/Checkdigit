# samples/ — driver & acceptance fixtures

Input files the `run_pass*.py` drivers and `run_acceptance.py` read. They were
originally read from an absolute sandbox path that did not ship with the package
(the cause of the `--verify` `FileNotFoundError`); they are bundled here so the
suite is self-contained.

## User-supplied generic fixtures

Anonymized real terminal messages (generic ports/lines/containers). The XML one
had its namespace neutralized to a vendor-neutral URI; the parser matches the
format structurally, so it still parses any container XML.

| File | Type | Notes |
| --- | --- | --- |
| `COPRAR_Discharge.xml` | SNX container XML | COPRAR discharge, 30 units in `line-discharge-list/@unit-id` |
| `Bigship_BAPLIE.edi` | EDIFACT BAPLIE | full vessel bayplan |
| `Booking_no1.txt` | EDIFACT COPARN | booking (original) |
| `Booking_no2_amend.txt` | EDIFACT COPARN | booking amendment |

## Assertion-bound fixtures (rebuilt from the bundle's own ground truth)

These carry the exact counts the `pass8`/`pass9`/`acceptance` assertions check.

| File | Source of truth | Method |
| --- | --- | --- |
| `4Containers_snx_Example.xml` | `*.CORRECTED.xml` + `sample_reports.json` | byte-exact (3 eqids reverted); namespace neutralized |
| `txtcontainers.txt` | `identified_containers.csv` | byte-exact printed forms (41 distinct, 3 valid / 38 flagged) |
| `Example_1_X12.edi` | bundle root | copied verbatim |
| `USER01_baplie_edi.txt` | `*.CORRECTED.txt` | functional: 10 EQD checks re-broken (→ 10 corrected strict) |
| `L02LOADLIST.txt` | `*.CORRECTED_lenient.txt` | functional: 90 of 93 re-broken / 3 valid |

"Functional" = the tool's own byte-splice run backwards: only the check-digit
byte changes; the genuine message structure and container numbers are untouched.

## Notes

- The XML format is treated as a generic terminal container XML; "SNX" is an
  opaque format code only, with no vendor namespace or branding in the package.
- `run_file_tests.py` is format-aware: it routes XML through the SNX path and
  EDIFACT through the EDIFACT locator. It is not part of `--verify`.
