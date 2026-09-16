/*
 * Port of checkdigit/iso6346_sizetype.py: decode the ISO 6346 four-character
 * size/type code into length, height, width, group and labels. Decode and
 * annotate only; a size/type code is never a correction target.
 *
 * Tables reproduce the Python module verbatim, including its two VERIFY notes
 * (height code 6 exact height; pallet-wide width per letter) and the deliberate
 * omission of length code 5 (ISO marks it unassigned; 45 ft is code L).
 */

// iso6346_sizetype.py:33-46
const LENGTH_MM: Record<string, number> = {
  "1": 2991, "2": 6068, "3": 9125, "4": 12192, B: 7315, C: 7430, G: 12500, H: 13106, L: 13716, M: 14630, N: 14935, P: 16154,
};
const LENGTH_LABEL: Record<string, string> = {
  "1": "10ft", "2": "20ft", "3": "30ft", "4": "40ft", B: "24ft", C: "24ft6in", G: "41ft", H: "43ft", L: "45ft", M: "48ft", N: "49ft", P: "53ft",
};

// iso6346_sizetype.py:57-73
const HEIGHT_MM: Record<string, number> = { "0": 2438, "2": 2591, "4": 2743, "5": 2896, "6": 2926, "8": 1295, "9": 1219 };
const HEIGHT_LABEL: Record<string, string> = { "0": "8ft", "2": "8ft6in", "4": "9ft", "5": "9ft6in (HC)", "6": ">9ft6in", "8": "4ft3in", "9": "<=4ft" };
const HEIGHT_LETTER_MM: Record<string, number> = { C: 2591, D: 2896, E: 2591, F: 2896, L: 2591, M: 2896, N: 2896, P: 2896 };
const STD_WIDTH_MM = 2438;

// iso6346_sizetype.py:78-94
const GROUP_BY_TYPE_LETTER: Record<string, string> = {
  G: "dry", V: "dry", W: "dry", R: "reefer", H: "reefer", U: "open_top", P: "flat_rack", K: "tank", N: "tank", T: "tank", B: "bulk", S: "named", A: "dry",
};
const TYPE_LETTER_LABEL: Record<string, string> = {
  G: "General purpose", V: "Ventilated", W: "Foldable GP", R: "Refrigerated", H: "Thermal/insulated", U: "Open top",
  P: "Platform / flat rack", K: "Tank", N: "Bulk (tank/hopper)", B: "Dry bulk", S: "Named cargo", A: "Air/surface", T: "Tank container",
};

export const VERIFY_NOTES = [
  "Height code 6: ISO says the height is above 2896 mm; the exact value used for drawing (2926 mm) is unverified.",
  "Pallet-wide letter codes: the width used (2500 mm) is a nominal value; the exact width per letter is unverified.",
];

export interface SizeType {
  code: string;
  defined: boolean;
  length_mm: number;
  width_mm: number;
  height_mm: number;
  high_cube: boolean;
  over_width: boolean;
  group: string;
  teu: number;
  length_label: string;
  height_label: string;
  type_label: string;
  notes: string[];
}

const FALLBACK = { length_mm: 6068, width_mm: 2438, height_mm: 2591, teu: 1.0 };

const pyRepr = (s: string): string => `'${s}'`;

// iso6346_sizetype.py:129-191
export function decodeSizeType(code: string | null | undefined): SizeType {
  const raw = (code ?? "").trim().toUpperCase();
  const notes: string[] = [];
  if (raw.length !== 4) {
    notes.push(`size/type code ${pyRepr(raw)} is not 4 characters; treated as undefined`);
    return {
      code: raw, defined: false, length_mm: FALLBACK.length_mm, width_mm: FALLBACK.width_mm, height_mm: FALLBACK.height_mm,
      high_cube: false, over_width: false, group: "undefined", teu: FALLBACK.teu, length_label: "?", height_label: "?", type_label: "?", notes,
    };
  }
  const [cLen, cHgt, cT1, cT2] = [raw[0] as string, raw[1] as string, raw[2] as string, raw[3] as string];
  let defined = true;

  let lengthMm = LENGTH_MM[cLen];
  let lengthLabel: string;
  if (lengthMm === undefined) {
    defined = false;
    notes.push(`length code ${pyRepr(cLen)} unknown/unassigned (ISO marks '5' unassigned; 45ft is 'L')`);
    lengthMm = FALLBACK.length_mm;
    lengthLabel = "?";
  } else {
    lengthLabel = LENGTH_LABEL[cLen] ?? "?";
  }
  const teu = lengthMm >= 12000 ? 2.0 : 1.0;

  let overWidth = false;
  let highCube = false;
  let heightMm: number;
  let widthMm: number;
  let heightLabel: string;
  if (cHgt in HEIGHT_MM) {
    heightMm = HEIGHT_MM[cHgt] as number;
    highCube = cHgt === "5" || cHgt === "6";
    widthMm = STD_WIDTH_MM;
    heightLabel = HEIGHT_LABEL[cHgt] ?? "?";
  } else if (cHgt in HEIGHT_LETTER_MM) {
    heightMm = HEIGHT_LETTER_MM[cHgt] as number;
    overWidth = true;
    highCube = heightMm >= 2896;
    widthMm = 2500;
    heightLabel = (highCube ? "9ft6in (HC)" : "8ft6in") + ", pallet-wide";
  } else {
    defined = false;
    heightMm = FALLBACK.height_mm;
    widthMm = STD_WIDTH_MM;
    heightLabel = "?";
    notes.push(`height/width code ${pyRepr(cHgt)} unknown`);
  }

  let group = GROUP_BY_TYPE_LETTER[cT1];
  let typeLabel: string;
  if (group === undefined) {
    defined = false;
    group = "undefined";
    typeLabel = `type ${cT1}${cT2} (unrecognised)`;
    notes.push(`type code ${pyRepr(cT1)} unrecognised`);
  } else {
    typeLabel = `${TYPE_LETTER_LABEL[cT1] ?? "?"} (${cT1}${cT2})`;
  }

  if (!defined && group !== "undefined") notes.push("partially decoded; raw code shown on the unit");

  return {
    code: raw, defined, length_mm: lengthMm, width_mm: widthMm, height_mm: heightMm, high_cube: highCube, over_width: overWidth,
    group, teu, length_label: lengthLabel, height_label: heightLabel, type_label: typeLabel, notes,
  };
}

// iso6346_sizetype.py:195-204
export const GROUP_COLOR: Record<string, string> = {
  dry: "#5b8def", reefer: "#22a3b8", open_top: "#e0a13a", flat_rack: "#9b6dd6", tank: "#c65d5d", bulk: "#7a8a5a", named: "#d98ab2", undefined: "#9aa0a6",
};

export const LENGTH_CODES = Object.keys(LENGTH_MM);
export const HEIGHT_CODES = [...Object.keys(HEIGHT_MM), ...Object.keys(HEIGHT_LETTER_MM)];
export const TYPE_LETTERS = Object.keys(GROUP_BY_TYPE_LETTER);
