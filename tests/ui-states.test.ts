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

describe("check page announces results and keeps a persistent live region", () => {
  const htmlText = read("check.html");
  const ts = read("pages/check.ts");
  it("has a persistent aria-live region", () => {
    expect(htmlText).toMatch(/id="live"[^>]*aria-live="polite"/);
  });
  it("leads a mismatch with the entered number, not the proposed number", () => {
    // the summary rows name Entered and Proposed as separate fields
    expect(ts).toContain('resultRow("Entered"');
    expect(ts).toContain('resultRow("Proposed number"');
    expect(ts).toContain("Check digit mismatch");
    expect(ts).toContain("Calculated check digit");
    expect(ts).toContain("Check digit matches");
    // the full validation table is disclosed, not shown by default
    expect(ts).toMatch(/<details class="explain">/);
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

describe("reference page names the task and offers a compact selector", () => {
  const htmlText = read("reference.html");
  it("uses a descriptive heading and a topic selector", () => {
    expect(htmlText).toContain("<h1>Guides and reference</h1>");
    expect(htmlText).toContain('id="ref-jump-select"');
  });
});

describe("homepage names the task and starts empty", () => {
  const htmlText = read("index.html");
  const ts = read("pages/index.ts");
  it("leads with a task-naming headline and task cards", () => {
    expect(htmlText).toContain("Check container and equipment numbers.");
    expect(htmlText).toContain("Check a number");
    expect(htmlText).toContain("Check a file");
    expect(htmlText).toContain("Compare lists");
  });
  it("starts empty and offers an explicit example action", () => {
    expect(ts).toContain('cellsFrom("")');
    expect(ts).toContain('const EXAMPLE = "CSQU3054383"');
    expect(htmlText).toContain('id="try-example"');
  });
  it("preserves the entered number when opening the detailed checker", () => {
    expect(ts).toMatch(/cta\.href = vm\.token \? `\/check#\$\{vm\.token\}` : "\/check"/);
  });
});
