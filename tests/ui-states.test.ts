import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

/*
 * Structural regression guards for the interface-state and labelling defects:
 * hidden controls that were overridden by display styling, download controls
 * shown before their files exist, unlabelled inputs, workspace-only options
 * crowding a simple local check, and a headline that did not name the task.
 * These assert the authored markup and tokens rather than a running DOM.
 */

const src = resolve(import.meta.dirname, "..", "src");
const read = (rel: string) => readFileSync(resolve(src, rel), "utf8");

describe("hidden state is enforced globally", () => {
  const css = read("styles/site.css");
  it("hides any [hidden] element even when a display class would show it", () => {
    expect(css).toMatch(/\[hidden\]\s*\{\s*display:\s*none\s*!important;\s*\}/);
  });
});

describe("the headline uses a readable colour, not the low-contrast lime", () => {
  const css = read("styles/site.css");
  it("no heading colours its text with the lime brand token", () => {
    // the light-mode lime headline measured ~1.16:1 on the off-white background
    expect(css).not.toMatch(/\bh1[^{}]*\{[^}]*color:\s*var\(--lime\)/);
    expect(css).not.toContain(".hero h1 em");
  });
});

describe("check page announces results and keeps a persistent live region", () => {
  const htmlText = read("check.html");
  const ts = read("pages/check.ts");
  const view = read("lib/result-view.ts");
  it("has a persistent aria-live region", () => {
    expect(htmlText).toMatch(/id="live"[^>]*aria-live="polite"/);
  });
  it("builds its result from the shared result view and discloses the working", () => {
    expect(ts).toContain("buildResult");
    // the full validation table and arithmetic are disclosed, not shown by default
    expect(ts).toMatch(/<details class="explain">/);
  });
  it("leads a mismatch with the entered number, not the suggestion", () => {
    // the shared view names Entered, Entered digit and Expected digit separately
    expect(view).toContain('rrow("Entered"');
    expect(view).toContain('rrow("Entered digit"');
    expect(view).toContain('rrow("Expected digit"');
    expect(view).toContain("Check digit mismatch");
    expect(view).toContain("Check digit calculated.");
    expect(view).toContain("Check digit matches.");
    // the suggestion is never presented as a confirmed correction
    expect(view).toContain("not a confirmed correction");
  });
});

describe("compare page uses descriptive list names and labelled controls", () => {
  const htmlText = read("compare.html");
  it("defaults to First list and Second list", () => {
    expect(htmlText).toContain('value="First list"');
    expect(htmlText).toContain('value="Second list"');
    expect(htmlText).not.toContain(">Side A<");
    expect(htmlText).not.toContain(">Side B<");
  });
  it("associates a label with the matching and filter selects", () => {
    expect(htmlText).toMatch(/<label for="key">/);
    expect(htmlText).toMatch(/<label for="filter">/);
    expect(htmlText).toMatch(/<label[^>]*for="a-label">/);
    expect(htmlText).toMatch(/<label[^>]*for="b-label">/);
  });
});

describe("files page: idempotent export, source-scoped state, keyboard", () => {
  const ts = read("pages/files.ts");
  it("does not mutate approved decisions when building, so rebuilds are identical", () => {
    expect(ts).not.toContain('state: "applied_to_draft"');
    expect(ts).toContain("invalidateExport");
  });
  it("re-derives the mapping and clears review state for a new source", () => {
    const setSource = ts.slice(ts.indexOf("async function setSource"), ts.indexOf("el.file.addEventListener"));
    expect(setSource).toContain("state.mapping = null");
    expect(setSource).toContain("state.approvals = []");
  });
  it("lets nested action buttons handle their own keys", () => {
    expect(ts).toContain("if (ev.target !== tr) return;");
    expect(ts).toContain("restoreRowFocus");
  });
  it("uses consistent decision labels and no red FAILED for kept originals", () => {
    expect(ts).toContain("Approve change");
    expect(ts).toContain("Review later");
    expect(ts).toContain('rejected: ["neutral", "Kept original"]');
  });
});

describe("files page keeps a simple local check simple", () => {
  const htmlText = read("files.html");
  const advancedIndex = htmlText.indexOf('id="advanced"');
  it("labels the paste area", () => {
    expect(htmlText).toMatch(/<textarea id="paste"[^>]*aria-label="Text to inspect"/);
  });
  it("moves import mode, coverage scope and owner policy into Advanced settings", () => {
    expect(advancedIndex).toBeGreaterThan(0);
    for (const id of ['id="mode"', 'id="scope-kind"', 'id="owner-policy"']) {
      expect(htmlText.indexOf(id), id).toBeGreaterThan(advancedIndex);
    }
  });
  it("does not auto-enable treat-as-declared and gives plain progress feedback", () => {
    expect(htmlText).not.toMatch(/id="trust"[^>]*checked/);
    expect(htmlText).not.toContain("Runs in a Web Worker");
    expect(htmlText).toContain("Checked in this browser");
  });
  it("makes the reviewed file the primary download and groups the reports", () => {
    expect(htmlText).toMatch(/id="dl-corrected"[^>]*class="btn btn-primary"|class="btn btn-primary"[^>]*id="dl-corrected"/);
    expect(htmlText).toContain('id="dl-extras"');
  });
});

describe("compare and check page correctness guards", () => {
  const compareTs = read("pages/compare.ts");
  const checkTs = read("pages/check.ts");
  it("compare refuses when a side was read only partially", () => {
    expect(compareTs).toContain("a.refused");
    expect(compareTs).toContain("b.refused");
  });
  it("compare export names its scope", () => {
    expect(compareTs).toContain("Download all");
    expect(compareTs).toContain("filtered result");
  });
  it("check share fragment carries the selected scheme", () => {
    expect(checkTs).toContain("function fragmentFor");
    expect(checkTs).toMatch(/scheme !== "auto"/);
  });
  it("an unknown category never reads as a valid match", () => {
    const view = read("lib/result-view.ts");
    expect(view).toContain("Unsupported category");
    // the unknown-category branch is reached before the "matches" branch
    expect(view.indexOf('e.category_set === "unknown"')).toBeGreaterThan(0);
    expect(view.indexOf('e.category_set === "unknown"')).toBeLessThan(view.indexOf('e.verdict === "valid"'));
  });
});

describe("homepage does not leave a stale result and links to the full working", () => {
  const indexTs = read("pages/index.ts");
  it("marks a shown result as outdated when the number changes", () => {
    expect(indexTs).toContain("markOutdated");
    expect(indexTs).toContain('resultEl.dataset["outdated"]');
  });
  it("opens the detailed checker with the entered number", () => {
    expect(indexTs).toMatch(/fullWorking\.href = view\.normalized \? `\/check#\$\{view\.normalized\}`/);
  });
});

describe("reference page names the task and offers a compact selector", () => {
  const htmlText = read("reference.html");
  it("uses a descriptive heading and a topic selector", () => {
    expect(htmlText).toContain("<h1>Guides and reference</h1>");
    expect(htmlText).toContain('id="ref-jump-select"');
  });
});

describe("homepage leads with the calculator and starts empty", () => {
  const htmlText = read("index.html");
  const ts = read("pages/index.ts");
  it("names the task in the headline and offers single and bulk modes", () => {
    expect(htmlText).toContain("Container Check Digit Calculator");
    expect(htmlText).toContain("Calculate a missing digit or check a complete container number.");
    expect(htmlText).toMatch(/role="tab"[^>]*>Single number/);
    expect(htmlText).toMatch(/role="tab"[^>]*>Bulk check/);
  });
  it("leads with one labelled field, not eleven character boxes", () => {
    expect(htmlText).toMatch(/<label class="calc-label" for="calc-input">Container number<\/label>/);
    expect(htmlText).not.toContain('id="cells"');
    expect(ts).not.toContain("cellsFrom");
  });
  it("exposes the other tools in the Tools menu", () => {
    expect(htmlText).toMatch(/<details class="menu">/);
    for (const href of ["/bulk", "/files", "/compare"]) {
      expect(htmlText, href).toMatch(new RegExp(`menu-panel[\\s\\S]*href="${href}"`));
    }
  });
  it("starts empty with a deliberate example action and a display-only diagram", () => {
    expect(ts).toContain('const EXAMPLE = "CSQU3054383"');
    expect(htmlText).toContain('id="example-btn"');
    expect(htmlText).toContain('id="diagram"');
    // the empty result state is painted from the shared view, not a prefilled success
    expect(ts).toContain('buildResult("", "auto")');
  });
  it("offers the two required explainer disclosures", () => {
    expect(htmlText).toContain("How this was calculated");
    expect(htmlText).toContain("What this check covers");
    expect(htmlText).toContain("Other identifier types");
  });
  it("preserves the entered number when opening the detailed checker", () => {
    expect(ts).toMatch(/fullWorking\.href = view\.normalized \? `\/check#\$\{view\.normalized\}`/);
  });
});
