import type { PolicyLike } from "./checkdigit";
import type { CorrectionReport } from "./report";
import { correctCsv, CsvError, type ColumnSel } from "./formats/csv";
import { correctEdifact } from "./formats/edifact";
import { correctFixedWidth, FixedWidthError, type ColRange } from "./formats/fixedwidth";
import { correctSnx } from "./formats/snx";
import { correctTxt } from "./formats/txt";
import { correctX12 } from "./formats/x12";

/*
 * Port of checkdigit/dispatcher.py: content-based format detection, the binary
 * front door, and routing to the format correctors. Plain text is the
 * catch-all; CSV and fixed-width are never auto-detected (the caller opts in
 * with a hint and a column spec).
 */

export type Format = "edifact" | "snx" | "x12" | "txt" | "csv" | "fixed" | "xlsx" | "unsupported";

export interface Detection {
  fmt: Format;
  reason: string;
}

// dispatcher.py:33-56
export function detectFormat(text: string): Detection {
  const s = text.replace(/^[﻿ \t\r\n]+/, "");
  const head = s.slice(0, 1000);
  if (s.startsWith("<?xml") || s.startsWith("<")) {
    if (
      /<(?:[A-Za-z][\w.-]*:)?snx[\s/>]/.test(head) ||
      head.includes("<line-discharge-list") ||
      (s.includes("<container") && s.includes("eqid=")) ||
      (s.includes("<unit") && s.includes("unique-key="))
    ) {
      return { fmt: "snx", reason: "" };
    }
    return { fmt: "unsupported", reason: "XML detected but not a recognized container XML schema" };
  }
  if (s.startsWith("UNA") || s.startsWith("UNB") || head.includes("UNB+") || head.includes("UNH+")) {
    return { fmt: "edifact", reason: "" };
  }
  if (s.includes("~") && (s.startsWith("ISA") || head.includes("ISA*") || head.includes("GS*") || head.includes("ST*"))) {
    return { fmt: "x12", reason: "" };
  }
  return { fmt: "txt", reason: "" };
}

const ZIP_MAGIC = [0x50, 0x4b, 0x03, 0x04];

/** Member names from a ZIP central directory (no decompression). Throws on a corrupt archive. */
export function zipMemberNames(data: Uint8Array): string[] {
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const minEocd = 22;
  if (data.length < minEocd) throw new Error("archive too small");
  let eocd = -1;
  for (let i = data.length - minEocd; i >= Math.max(0, data.length - minEocd - 65535); i--) {
    if (view.getUint32(i, true) === 0x06054b50) {
      eocd = i;
      break;
    }
  }
  if (eocd === -1) throw new Error("end of central directory not found");
  const entries = view.getUint16(eocd + 10, true);
  const cdSize = view.getUint32(eocd + 12, true);
  const cdOffset = view.getUint32(eocd + 16, true);
  if (cdOffset + cdSize > data.length) throw new Error("central directory outside the file");
  const names: string[] = [];
  let p = cdOffset;
  const decoder = new TextDecoder("utf-8");
  for (let k = 0; k < entries; k++) {
    if (p + 46 > data.length || view.getUint32(p, true) !== 0x02014b50) throw new Error("bad central directory entry");
    const nameLen = view.getUint16(p + 28, true);
    const extraLen = view.getUint16(p + 30, true);
    const commentLen = view.getUint16(p + 32, true);
    names.push(decoder.decode(data.subarray(p + 46, p + 46 + nameLen)));
    p += 46 + nameLen + extraLen + commentLen;
  }
  return names;
}

// dispatcher.py:134-161
export function detectBytes(data: Uint8Array): Detection | null {
  if (data.length >= 4 && ZIP_MAGIC.every((b, i) => data[i] === b)) {
    let names: string[];
    try {
      names = zipMemberNames(data);
    } catch {
      return { fmt: "unsupported", reason: "corrupt ZIP archive" };
    }
    if (names.includes("xl/workbook.xml")) return { fmt: "xlsx", reason: "" };
    if (names.includes("word/document.xml")) return { fmt: "unsupported", reason: "Word document (.docx) is not a supported format" };
    if (names.includes("ppt/presentation.xml")) return { fmt: "unsupported", reason: "PowerPoint (.pptx) is not a supported format" };
    return { fmt: "unsupported", reason: "ZIP archive that is not an Excel workbook" };
  }
  if (data.length >= 5 && data[0] === 0x25 && data[1] === 0x50 && data[2] === 0x44 && data[3] === 0x46 && data[4] === 0x2d) {
    return { fmt: "unsupported", reason: "PDF is not a supported format" };
  }
  return null;
}

export interface CorrectOptions {
  owner_policy?: string;
  trust?: boolean;
  known_owner_prefixes?: ReadonlySet<string> | null;
  policy?: PolicyLike | null;
}

// dispatcher.py:59-73
export function correct(text: string, options: CorrectOptions = {}): [Detection, CorrectionReport | null] {
  const det = detectFormat(text);
  const ownerPolicy = options.owner_policy ?? "strict";
  const policy = options.policy ?? null;
  if (det.fmt === "edifact") return [det, correctEdifact(text, { owner_policy: ownerPolicy, policy })];
  if (det.fmt === "snx") return [det, correctSnx(text, { owner_policy: ownerPolicy, policy })];
  if (det.fmt === "x12") return [det, correctX12(text, { owner_policy: ownerPolicy, policy })];
  if (det.fmt === "txt") {
    return [
      det,
      correctTxt(text, {
        trust: options.trust ?? false,
        owner_policy: ownerPolicy,
        known_owner_prefixes: options.known_owner_prefixes ?? null,
        policy,
      }),
    ];
  }
  return [det, null];
}

export interface ParseOptions {
  columns?: ColumnSel[];
  delimiter?: string | null;
  has_header?: boolean;
  whole_cell?: boolean;
  ranges?: ColRange[];
  header_lines?: number;
  whole_field?: boolean;
}

// dispatcher.py:81-127
export function correctWithHint(
  text: string,
  formatHint: string,
  parseOptions: ParseOptions = {},
  options: CorrectOptions = {},
): [Detection, CorrectionReport | null] {
  const ownerPolicy = options.owner_policy ?? "strict";
  const trust = options.trust ?? true;
  const shared = { trust, owner_policy: ownerPolicy, known_owner_prefixes: options.known_owner_prefixes ?? null, policy: options.policy ?? null };
  if (formatHint === "csv") {
    const cols = parseOptions.columns;
    if (!cols || cols.length === 0) {
      return [{ fmt: "unsupported", reason: "CSV hint requires parse_options.columns (indices or header names)" }, null];
    }
    try {
      const rep = correctCsv(text, cols, {
        ...shared,
        delimiter: parseOptions.delimiter ?? null,
        has_header: parseOptions.has_header ?? true,
        whole_cell: parseOptions.whole_cell ?? true,
      });
      return [{ fmt: "csv", reason: "" }, rep];
    } catch (err) {
      if (err instanceof CsvError) return [{ fmt: "unsupported", reason: `CSV column spec error: ${err.message}` }, null];
      throw err;
    }
  }
  if (formatHint === "fixed" || formatHint === "fixedwidth" || formatHint === "fixed_width") {
    const ranges = parseOptions.ranges;
    if (!ranges || ranges.length === 0) {
      return [{ fmt: "unsupported", reason: "fixed-width hint requires parse_options.ranges [[start,end],...]" }, null];
    }
    try {
      const rep = correctFixedWidth(text, ranges, {
        ...shared,
        header_lines: parseOptions.header_lines ?? 0,
        whole_field: parseOptions.whole_field ?? true,
      });
      return [{ fmt: "fixed", reason: "" }, rep];
    } catch (err) {
      if (err instanceof FixedWidthError) return [{ fmt: "unsupported", reason: `fixed-width spec error: ${err.message}` }, null];
      throw err;
    }
  }
  return correct(text, { ...options, trust });
}
