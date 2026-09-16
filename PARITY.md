# Parity map

Every behaviour the site exposes is a port of the Python tree in `checkdigit/`. This
table records, per Python symbol, the TypeScript symbol that mirrors it, the test that
proves it, and the status:

- **PARITY** — the test runs and passes against the Python behaviour or its golden output.
- **INTENTIONAL-DIFF** — the port deliberately differs; the difference is explained in place.
- **OPEN** — not ported yet, or ported without a passing proof.

Status is updated only when the named test is green. Nothing here is marked PARITY
by inspection.

## Kernel (`checkdigit/equipment_checkdigit.py`)

| Python symbol | Lines | TypeScript symbol | Proven by | Status |
|---|---|---|---|---|
| `_ISO6346_LETTER_VALUES` | 51-60 | `src/lib/checkdigit.ts` letter table | `tests/kernel.parity.test.ts` | PARITY |
| `iso6346_value` | 63-70 | `iso6346Value` | `tests/kernel.parity.test.ts` | PARITY (ASCII digits only; Python `str.isdigit` also accepts non-ASCII digits) |
| `iso6346_check_digit` | 73-92 | `iso6346CheckDigit` | `tests/kernel.parity.test.ts` | PARITY |
| `uic_check_digit` | 95-114 | `uicCheckDigit` | `tests/kernel.parity.test.ts` | PARITY |
| `IdentifierType` | 121-126 | `IdentifierType` (`ISO6346`, `ILU`, `UIC`, `SIZE_TYPE`, `UNKNOWN`) | `tests/kernel.parity.test.ts` | PARITY |
| `FieldContext` | 129-146 | `FieldContext` | `tests/kernel.parity.test.ts` | PARITY |
| `Status` | 149-154 | `Status` (`VALID`, `CORRECTED`, `FLAGGED`, `NOT_A_TARGET`, `INVALID_STRUCTURE`) | `tests/kernel.parity.test.ts` | PARITY |
| `CorrectionResult` | 157-171 | `CorrectionResult` | `tests/kernel.parity.test.ts` | PARITY |
| `_RE_BIC_LIKE` | 176 | `RE_BIC_LIKE` | `tests/kernel.parity.test.ts` | INTENTIONAL-DIFF: `[0-9]` instead of Python `\d`, which also matches non-ASCII decimal digits; see OPEN_ITEMS.md |
| `_RE_CONTAINER_SHAPED` | 177 | `RE_CONTAINER_SHAPED` | `tests/kernel.parity.test.ts` | INTENTIONAL-DIFF: ASCII digits only (as above) |
| `_RE_UIC` | 178 | `RE_UIC` | `tests/kernel.parity.test.ts` | INTENTIONAL-DIFF: ASCII digits only (as above) |
| `_ISO6346_CATEGORIES` (`UJZ`) | 179 | `ISO6346_CATEGORIES` | `tests/kernel.parity.test.ts` | PARITY |
| `_ILU_CATEGORIES` (`ABDEK`) | 180 | `ILU_CATEGORIES` | `tests/kernel.parity.test.ts` | PARITY |
| `_OWNER_POLICIES` | 181 | `OWNER_POLICIES` | `tests/kernel.parity.test.ts` | PARITY |
| `normalize` | 188-194 | `normalize` | `tests/kernel.parity.test.ts` | PARITY |
| `_evaluate_bic` | 197-208 | `evaluateBic` | `tests/kernel.parity.test.ts` | PARITY |
| `correct_identifier` decision table | 211-336 | `correctIdentifier` | `tests/kernel.parity.test.ts` | PARITY |
| `correct_identifier` policy layer (deny / allow / per-prefix) | 238-258 | `correctIdentifier` with `policy` option (`PolicyLike` contract) | `tests/kernel.parity.test.ts` (decision vectors with 6 operator policies) | PARITY |
| `_decide_bic` | 339-384 | `decideBic` | `tests/kernel.parity.test.ts` | PARITY |
| `_decide_uic` | 387-410 | `decideUic` | `tests/kernel.parity.test.ts` | PARITY |
| `correct_x12_equipment` | 417-495 | `correctX12Equipment` | `tests/kernel.parity.test.ts` | PARITY |
| `explain` | 522-581 | `explain` | `tests/kernel.parity.test.ts` (calc vectors) | PARITY |

Result objects keep the Python field names (`printed_check`, `computed_check`,
`id_type`, `corrected`, `changed`) so vectors and the Phase 2 report compare directly.
Enum member names and string values are the Python ones (`Status.CORRECTED === "corrected"`).

## Kernel test cases (`checkdigit/test_equipment_checkdigit.py`, 22 tests)

| Python test | TypeScript test | Status |
|---|---|---|
| `test_iso6346_canonical_example` (`CSQU305438` gives 3) | `tests/kernel.parity.test.ts` | PARITY |
| `test_iso6346_remainder_10_maps_to_zero` (`APLU100000` gives 0) | `tests/kernel.parity.test.ts` | PARITY |
| `test_uic_worked_example` (`21812471217` gives 3) | `tests/kernel.parity.test.ts` | PARITY |
| `test_iso6346_value_rejects_bad_char` | `tests/kernel.parity.test.ts` | PARITY |
| `test_snx_fixture` | `tests/kernel.parity.test.ts` | PARITY |
| `test_snx_valid_one_is_not_changed` | `tests/kernel.parity.test.ts` | PARITY |
| `test_freetext_checkfail_is_flagged_not_corrected` | `tests/kernel.parity.test.ts` | PARITY |
| `test_freetext_corroborated_by_owner_registry_is_corrected` | `tests/kernel.parity.test.ts` | PARITY |
| `test_unusual_category_is_flagged` | `tests/kernel.parity.test.ts` | PARITY |
| `test_ilu_category_routes_and_shares_math` | `tests/kernel.parity.test.ts` | PARITY |
| `test_size_type_passthrough` | `tests/kernel.parity.test.ts` | PARITY |
| `test_uic_requires_rail_context_to_correct` | `tests/kernel.parity.test.ts` | PARITY |
| `test_uic_correction_in_rail_context` | `tests/kernel.parity.test.ts` | PARITY |
| `test_garbage_is_invalid_structure` | `tests/kernel.parity.test.ts` | PARITY |
| `test_painted_form_is_normalized` | `tests/kernel.parity.test.ts` | PARITY |
| `test_x12_populates_absent_check_digit` | `tests/kernel.parity.test.ts` | PARITY |
| `test_x12_validates_correct_check_digit` | `tests/kernel.parity.test.ts` | PARITY |
| `test_x12_corrects_wrong_check_digit` | `tests/kernel.parity.test.ts` | PARITY |
| `test_x12_rejects_bad_initial` | `tests/kernel.parity.test.ts` | PARITY |
| `test_pseudo_prefix_strict_is_flagged` | `tests/kernel.parity.test.ts` | PARITY |
| `test_pseudo_prefix_lenient_is_corrected` | `tests/kernel.parity.test.ts` | PARITY |
| `test_standard_owner_unaffected_by_policy` | `tests/kernel.parity.test.ts` | PARITY |

## Kernel vectors and explain assertions (`checkdigit/run_pass11.py`)

| Python source | TypeScript test | Status |
|---|---|---|
| `write_js_vectors()`: 11 vectors in `tests/vectors/calc_vectors.json` | `tests/kernel.parity.test.ts` | PARITY |
| `test_explain_iso` per-character assertions (`chars[0]`, `chars[3]`, `remainder_ten`, `mod`) | `tests/kernel.parity.test.ts` | PARITY |
| `test_explain_uic_and_errors` (`steps` sum equals `sum`; malformed shapes return `ok: false`) | `tests/kernel.parity.test.ts` | PARITY |
| Required extra cases: `MSKU1234567` mismatch with computed 5; lowercase and space-separated token; unknown category gives FLAGGED; 12-digit token outside rail context gives FLAGGED even when Luhn passes | `tests/kernel.parity.test.ts` | PARITY |
| `scripts/gen_decision_vectors.py` (new): `correct_identifier` over 23 tokens x 5 contexts x strict/lenient x corroboration x 6 operator policies, `correct_x12_equipment` over 15 split inputs x 7 policies, plus 7 `ValueError` messages; every field of every result, reason text included | `tests/kernel.parity.test.ts` (657 cases) | PARITY |

## Existing JavaScript mirrors (to be consolidated into `src/lib/checkdigit.ts`)

| Source block | Replacement | Proven by | Status |
|---|---|---|---|
| `checkdigit/checkdigit_app.jsx:69-119` `calcExplain` | `explain` | `tests/kernel.parity.test.ts`; `tests/site.test.ts` runs both vector sets against the built kernel chunk | PARITY |
| `checkdigit/site/index.html:362-372` `checkOf` | `iso6346CheckDigit` via `src/pages/index.ts` | `tests/site.test.ts` (single CALC-PURE block; letter table shipped once) | PARITY |
| `checkdigit/site/check-digit.html:225-247` `checkOf`, `breakdown` | `iso6346CheckDigit`, `explain` via `src/pages/check.ts` | `tests/site.test.ts` | PARITY (the `/how-it-works` page itself is still to be built) |

## Phase 1 supporting modules

| Python symbol | TypeScript symbol | Proven by | Status |
|---|---|---|---|
| `iso6346_sizetype._LENGTH_MM`, `_LENGTH_LABEL` | `src/lib/sizetype.ts` | `tests/sizetype.test.ts` | OPEN |
| `iso6346_sizetype._HEIGHT_MM`, `_HEIGHT_LETTER_MM`, `_STD_WIDTH_MM` | `src/lib/sizetype.ts` | `tests/sizetype.test.ts` | OPEN |
| `iso6346_sizetype._GROUP_BY_TYPE_LETTER`, `_TYPE_LETTER_LABEL` | `src/lib/sizetype.ts` | `tests/sizetype.test.ts` | OPEN |
| `iso6346_sizetype.decode` (with the `defined=False` fallback, no length code `5`, both `VERIFY` notes) | `decodeSizeType` | `tests/sizetype.test.ts` | OPEN |
| `txt_locator._RE_BIC` (`[A-Z]{4}[0-9]{7}`, global, non-overlapping) | `src/lib/bulk.ts` token extraction | `tests/bulk.test.ts` | OPEN. Planned INTENTIONAL-DIFF: `/bulk` normalizes lowercase and hyphen/space-separated tokens before matching; the Phase 2 file corrector keeps the Python behaviour unchanged |
| SPA `exportCsv` columns (UTF-8 BOM, CRLF) | `src/lib/export.ts` | `tests/bulk.test.ts` | OPEN |

## Phase 2: writer, detection, locators, correctors

| Python symbol | TypeScript symbol | Proven by | Status |
|---|---|---|---|
| `substitution.Edit`, `apply_edits`, `SubstitutionError`, `changed_indices` (7 tests in `test_substitution.py`) | `src/lib/substitution.ts` | `tests/substitution.test.ts` | OPEN |
| `dispatcher.detect_format` | `detectFormat` | `tests/detect.test.ts` | OPEN |
| `dispatcher.detect_bytes` (ZIP, PDF, docx, pptx, corrupt ZIP reasons) | `detectBytes` | `tests/detect.test.ts` | OPEN |
| `dispatcher.correct`, `correct_with_hint` | `correct`, `correctWithHint` | `tests/splice.parity.test.ts` | OPEN |
| `txt_locator.locate_equipment`, `evaluate_text`; `txt_corrector.correct_txt` | `src/lib/formats/txt.ts` | `tests/splice.parity.test.ts` (`samples/txtcontainers.txt`: 41 distinct, 3 valid, 38 flagged) | OPEN |
| `edifact_locator.detect_separators`, `_split_segments`, `_split_on`, `parse_segment`, `iter_segments`, `locate_equipment`, `evaluate_text`; `edifact_corrector.correct_edifact` | `src/lib/formats/edifact.ts` | `tests/splice.parity.test.ts` (`USER01_baplie_edi.txt` to `USER01_baplie_edi.CORRECTED.txt`; `L02LOADLIST.txt` to `L02LOADLIST.CORRECTED_lenient.txt` under lenient) | OPEN |
| `x12_locator.detect_delims`, `iter_segments`, `locate_equipment`, `evaluate_text`; `x12_corrector._body10`, `correct_x12` | `src/lib/formats/x12.ts` | `tests/splice.parity.test.ts` (`run_pass6.SYNTH` to `X12_synthetic.CORRECTED.edi`) | OPEN |
| `snx_locator.harden_and_parse`, `XmlSecurityError`, `IntegrityError`, `_parsed_counts`, `_locate_occurrences`, `locate_equipment`, `size_type_codes`, `evaluate_text`; `snx_corrector.correct_snx` | `src/lib/formats/snx.ts` | `tests/splice.parity.test.ts` (`4Containers_snx_Example.xml` to `4Containers_snx_Example.CORRECTED.xml`; `COPRAR_Discharge.xml` 30 units) | OPEN |
| `csv_locator.sniff_delimiter`, `parse_records`, `resolve_columns`, `locate_csv`, `evaluate_csv`, `CsvError`; `csv_corrector.correct_csv` | `src/lib/formats/csv.ts` | `tests/splice.parity.test.ts` | OPEN |
| `fixedwidth_locator._validate_ranges`, `_line_spans`, `locate_fixedwidth`, `evaluate_fixedwidth`, `FixedWidthError`; `fixedwidth_corrector.correct_fixedwidth` | `src/lib/formats/fixedwidth.ts` | `tests/splice.parity.test.ts` | OPEN |
| `xlsx_locator.load_workbook`, `_t_spans`, `_shared_cell_refs`, `_locate_shared`, `_locate_sheets`, `locate`, `XlsxError`; `xlsx_corrector.correct_workbook` | `src/lib/formats/xlsx.ts` | `tests/splice.parity.test.ts` | OPEN |
| `correction_report.Occurrence`, `Change`, `Flag`, `ContainerRecord`, `CorrectionReport`, `inventory_from_results` | `src/lib/report.ts` | `tests/splice.parity.test.ts` | OPEN |
| `nearmiss.osa_distance`, `suggest` | `src/lib/nearmiss.ts` | `tests/nearmiss.test.ts` | OPEN |
| `policy.Policy`, `ResolvedTreatment`, `plan_for_tokens` (per-session only, never persisted) | `src/lib/policy.ts` | `tests/policy.test.ts` | OPEN |
| `batch.process_batch` output layout (`corrected/<name>`, `report.csv`, `manifest.json` with `totals` and `members`) | `src/lib/batch.ts` | `tests/batch.test.ts` | OPEN |

## Idempotence and non-corruption (every golden pair)

| Property | Proven by | Status |
|---|---|---|
| Output length equals input length | `tests/splice.parity.test.ts` | OPEN |
| Output bytes equal the `*.CORRECTED*` golden file | `tests/splice.parity.test.ts` | OPEN |
| Running the corrector on its own output changes nothing | `tests/splice.parity.test.ts` | OPEN |

## Site-level checks

| Property | Proven by | Status |
|---|---|---|
| Zero external `http(s)://` script, link, img, or font references in the build | `tests/site.test.ts` | PARITY |
| Zero `fetch(`, `XMLHttpRequest`, `WebSocket`, `sendBeacon` outside the service worker's own asset caching | `tests/site.test.ts` | PARITY (no service worker yet) |
| Every CALC-PURE block in the build equals the shared module | `tests/site.test.ts`: one block in `src/`, letter table in one built chunk, built chunk passes all 668 vectors | PARITY |
| Every route returns 200 from `wrangler dev` | `tests/site.test.ts` (routes derived from the Vite page list; unknown path gets the 404 page) | PARITY for `/`, `/check`; other routes are added as they are built |
| `wrangler.jsonc` declares no bindings and no `main` | `tests/cost.test.ts` | PARITY |
| First-load JavaScript for `/` at or under 60 kB gzipped | `tests/site.test.ts` | PARITY (about 5.6 kB) |
| Text contrast at or above 4.5:1 on both palettes; verdict tokens unchanged in the stylesheet | `tests/site.test.ts` | PARITY |
