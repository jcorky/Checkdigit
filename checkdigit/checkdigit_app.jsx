import { useState, useRef, useEffect, Fragment } from "react";
import {
  Upload, Download, ShieldCheck, FlagTriangleRight, AlertCircle,
  ScanLine, FileText, Boxes, BookOpen, ArrowRight, Layers, Database,
  GitBranch, Lock, Ship, Train, Truck,
} from "lucide-react";

/* ---------------------------------------------------------------------------
   Live demo fixtures: REAL CorrectionReport.to_dict() output from the Python
   service (SNX + a synthetic X12), so the UI renders against the true contract.
   corrected_text is null here (omitted from demo data); against the live API it
   is present and the download button produces the corrected file.
--------------------------------------------------------------------------- */
const FIXTURES = {"snx":{"owner_policy":"strict","total_containers":4,"corrected":[{"old":"APLU9192812","new":"APLU9192819","printed_check":"2","computed_check":"9","id_type":"iso6346","occurrences":[{"offset":215,"label":"container/@eqid","before":"eqid=\"APLU9192812\"","after":"eqid=\"APLU9192819\""},{"offset":3195,"label":"unit/@id","before":"id=\"APLU9192812\"","after":"id=\"APLU9192819\""},{"offset":3292,"label":"unit/@unique-key","before":"unique-key=\"APLU9192812\"","after":"unique-key=\"APLU9192819\""},{"offset":3384,"label":"equipment/@eqid","before":"eqid=\"APLU9192812\"","after":"eqid=\"APLU9192819\""}],"reason":"ISO6346 check digit 2 -> 9."},{"old":"APLU7979534","new":"APLU7979533","printed_check":"4","computed_check":"3","id_type":"iso6346","occurrences":[{"offset":907,"label":"container/@eqid","before":"eqid=\"APLU7979534\"","after":"eqid=\"APLU7979533\""},{"offset":7105,"label":"unit/@id","before":"id=\"APLU7979534\"","after":"id=\"APLU7979533\""},{"offset":7202,"label":"unit/@unique-key","before":"unique-key=\"APLU7979534\"","after":"unique-key=\"APLU7979533\""},{"offset":7294,"label":"equipment/@eqid","before":"eqid=\"APLU7979534\"","after":"eqid=\"APLU7979533\""}],"reason":"ISO6346 check digit 4 -> 3."},{"old":"APLU2127329","new":"APLU2127323","printed_check":"9","computed_check":"3","id_type":"iso6346","occurrences":[{"offset":2437,"label":"container/@eqid","before":"eqid=\"APLU2127329\"","after":"eqid=\"APLU2127323\""},{"offset":4846,"label":"unit/@id","before":"id=\"APLU2127329\"","after":"id=\"APLU2127323\""},{"offset":4943,"label":"unit/@unique-key","before":"unique-key=\"APLU2127329\"","after":"unique-key=\"APLU2127323\""},{"offset":5035,"label":"equipment/@eqid","before":"eqid=\"APLU2127329\"","after":"eqid=\"APLU2127323\""}],"reason":"ISO6346 check digit 9 -> 3."}],"flagged":[],"valid":1,"empty_id":0,"invalid":0,"corrected_text":null,"containers":[{"as_found":"APLU9192812","canonical":"APLU9192819","owner":"APL","category":"U","id_type":"iso6346","status":"corrected","printed_check":"2","computed_check":"9","occurrences":1},{"as_found":"APLU7979534","canonical":"APLU7979533","owner":"APL","category":"U","id_type":"iso6346","status":"corrected","printed_check":"4","computed_check":"3","occurrences":1},{"as_found":"CBHU2426018","canonical":"CBHU2426018","owner":"CBH","category":"U","id_type":"iso6346","status":"valid","printed_check":"8","computed_check":"8","occurrences":1},{"as_found":"APLU2127329","canonical":"APLU2127323","owner":"APL","category":"U","id_type":"iso6346","status":"corrected","printed_check":"9","computed_check":"3","occurrences":1}],"detected_format":"snx","filename":"4Containers_snx_Example.xml"},"x12":{"owner_policy":"strict","total_containers":4,"corrected":[{"old":"MSCU1234560","new":"MSCU1234566","printed_check":"0","computed_check":"6","id_type":"iso6346","occurrences":[{"offset":43,"label":"N7/N7-18(761)","before":"N7*MSCU*123456****************0****22G1","after":"N7*MSCU*123456****************6****22G1"}],"reason":"ISO6346 check digit (N7-18) 0 -> 6."},{"old":"TCLU456789_","new":"TCLU4567897","printed_check":"","computed_check":"7","id_type":"iso6346","occurrences":[{"offset":84,"label":"N7/N7-18(761)","before":"N7*TCLU*456789********************45G1","after":"N7*TCLU*456789****************7****45G1"}],"reason":"X12 N7-18 (check digit) not transmitted; computed 7. Write it to N7-18."},{"old":"MSCU1234560","new":"MSCU1234566","printed_check":"0","computed_check":"6","id_type":"iso6346","occurrences":[{"offset":116,"label":"N9*EQ","before":"N9*EQ*MSCU1234560","after":"N9*EQ*MSCU1234566"}],"reason":"ISO6346 check digit 0 -> 6."}],"flagged":[{"offset":94,"eqid":"HLBU112233","suggested_check":"2","reason":"N7-18 (element 761) is absent; populating it would require inserting elements. Suggested check 2; not auto-applied.","occurrences":1}],"valid":0,"empty_id":0,"invalid":0,"corrected_text":null,"containers":[{"as_found":"MSCU1234560","canonical":"MSCU1234566","owner":"MSC","category":"U","id_type":"iso6346","status":"corrected","printed_check":"0","computed_check":"6","occurrences":2},{"as_found":"TCLU456789","canonical":"TCLU4567897","owner":"TCL","category":"U","id_type":"iso6346","status":"corrected","printed_check":"","computed_check":"7","occurrences":1},{"as_found":"HLBU112233","canonical":"HLBU1122332","owner":"HLB","category":"U","id_type":"iso6346","status":"flagged","printed_check":"","computed_check":"2","occurrences":1}],"detected_format":"x12","filename":"terminal_322.edi"},"baplie":{"report":{"owner_policy":"strict","total_containers":4,"corrected":[{"old":"MSKU7351773","new":"MSKU7351770","printed_check":"3","computed_check":"0","id_type":"iso6346","occurrences":[{"offset":145,"label":"EQD/C237/8260","before":"EQD+CN+MSKU7351773+22G1+++5","after":"EQD+CN+MSKU7351770+22G1+++5"}],"reason":"ISO6346 check digit 3 -> 0."},{"old":"CBHU6818013","new":"CBHU6818017","printed_check":"3","computed_check":"7","id_type":"iso6346","occurrences":[{"offset":192,"label":"EQD/C237/8260","before":"EQD+CN+CBHU6818013+45R1+++4","after":"EQD+CN+CBHU6818017+45R1+++4"}],"reason":"ISO6346 check digit 3 -> 7."},{"old":"MEDU2345674","new":"MEDU2345679","printed_check":"4","computed_check":"9","id_type":"iso6346","occurrences":[{"offset":325,"label":"EQD/C237/8260","before":"EQD+CN+MEDU2345674+22G1+++5","after":"EQD+CN+MEDU2345679+22G1+++5"}],"reason":"ISO6346 check digit 4 -> 9."}],"flagged":[],"valid":1,"empty_id":0,"invalid":0,"corrected_text":"UNB+UNOA:2+SENDER+RECV+260611:1200+1'UNH+1+BAPLIE:D:95B:UN:SMDG20'BGM+45'TDT+20++1++MAERSK++++9V:146:11:MAERSK ALABAMA'LOC+147+0030184::5'EQD+CN+MSKU7351770+22G1+++5'LOC+147+0050186::5'EQD+CN+CBHU6818017+45R1+++4'RFF+ABA:1'TMP+2+-18:CEL'LOC+147+0070188::5'EQD+CN+HLXU1234561+22G1+++5'DGS+IMD+3:1203'LOC+147+0090180::5'EQD+CN+MEDU2345679+22G1+++5'UNT+12+1'UNZ+1+1'","corrected_b64":null,"containers":[{"as_found":"MSKU7351773","canonical":"MSKU7351770","owner":"MSK","category":"U","id_type":"iso6346","status":"corrected","printed_check":"3","computed_check":"0","occurrences":1,"near_misses":[]},{"as_found":"CBHU6818013","canonical":"CBHU6818017","owner":"CBH","category":"U","id_type":"iso6346","status":"corrected","printed_check":"3","computed_check":"7","occurrences":1,"near_misses":[]},{"as_found":"HLXU1234561","canonical":"HLXU1234561","owner":"HLX","category":"U","id_type":"iso6346","status":"valid","printed_check":"1","computed_check":"1","occurrences":1,"near_misses":[]},{"as_found":"MEDU2345674","canonical":"MEDU2345679","owner":"MED","category":"U","id_type":"iso6346","status":"corrected","printed_check":"4","computed_check":"9","occurrences":1,"near_misses":[]}],"filename":"MAERSK_ALABAMA.baplie","detected_format":"edifact"},"visualization":{"transport":"vessel","vessel_name":"MAERSK ALABAMA","counts":{"total":4,"placed":4,"unplaced":0,"reefer":1,"hazmat":1,"oversize":0,"undefined":0,"empty":1,"high_cube":1},"bays":[3,5,7,9],"rendered_bays":[3,5,7,9],"omitted_bays":[],"svg_by_bay":{"3":"<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"226\" height=\"212\" font-family=\"sans-serif\" viewBox=\"0 0 226 212\"><defs><marker id=\"ovh\" markerWidth=\"6\" markerHeight=\"6\" refX=\"5\" refY=\"3\" orient=\"auto\"><path d=\"M0,0 L6,3 L0,6 z\" fill=\"#b00\"/></marker></defs><rect x=\"0\" y=\"0\" width=\"226\" height=\"212\" fill=\"#fbfbfc\"/><text x=\"60\" y=\"26\" font-size=\"15\" font-weight=\"bold\" fill=\"#111\">Bay 03 \u2014 20ft slot  \u00b7 MAERSK ALABAMA</text><text x=\"60\" y=\"40\" font-size=\"10\" fill=\"#666\">looking forward \u2014 port (even rows) left \u00b7 starboard (odd rows) right</text><text x=\"50\" y=\"110.0\" text-anchor=\"end\" font-size=\"9\" fill=\"#555\">84</text><g><title>MSKU7351770  22G1\n20ft General purpose (G1)\nslot 0030184  bay 3 row 1 tier 84 (starboard, on deck)\nfull</title><polygon points=\"114,92 130,76 130,106 114,122\" fill=\"#4165ac\" stroke=\"#2d4677\" stroke-width=\"1\"/><polygon points=\"60,92 76,76 130,76 114,92\" fill=\"#6ba6ff\" stroke=\"#2d4677\" stroke-width=\"1\"/><rect x=\"60\" y=\"92\" width=\"54\" height=\"30\" fill=\"#5b8def\" stroke=\"#2d4677\" stroke-width=\"1.2\"/></g><text x=\"87.0\" y=\"134\" text-anchor=\"middle\" font-size=\"9\" fill=\"#555\">01<tspan fill=\"#999\">S</tspan></text><g transform=\"translate(60,196)\"><g font-family=\"sans-serif\" font-size=\"10\"><rect x=\"0\" y=\"0\" width=\"12\" height=\"12\" fill=\"#5b8def\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"16\" y=\"10\" fill=\"#222\">Dry</text><rect x=\"57\" y=\"0\" width=\"12\" height=\"12\" fill=\"#22a3b8\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"73\" y=\"10\" fill=\"#222\">Reefer</text><rect x=\"141\" y=\"0\" width=\"12\" height=\"12\" fill=\"#e0a13a\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"157\" y=\"10\" fill=\"#222\">Open-top</text><rect x=\"243\" y=\"0\" width=\"12\" height=\"12\" fill=\"#9b6dd6\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"259\" y=\"10\" fill=\"#222\">Flat rack</text><rect x=\"354\" y=\"0\" width=\"12\" height=\"12\" fill=\"#c65d5d\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"370\" y=\"10\" fill=\"#222\">Tank</text><rect x=\"420\" y=\"0\" width=\"12\" height=\"12\" fill=\"#7a8a5a\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"436\" y=\"10\" fill=\"#222\">Bulk</text><rect x=\"486\" y=\"0\" width=\"12\" height=\"12\" fill=\"#9aa0a6\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"502\" y=\"10\" fill=\"#222\">Undefined</text><text x=\"597\" y=\"10\" fill=\"#0a6\">\u2744 reefer</text><text x=\"667\" y=\"10\" fill=\"#d33\">\u25c6 hazmat</text></g></g></svg>","5":"<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"226\" height=\"212\" font-family=\"sans-serif\" viewBox=\"0 0 226 212\"><defs><marker id=\"ovh\" markerWidth=\"6\" markerHeight=\"6\" refX=\"5\" refY=\"3\" orient=\"auto\"><path d=\"M0,0 L6,3 L0,6 z\" fill=\"#b00\"/></marker></defs><rect x=\"0\" y=\"0\" width=\"226\" height=\"212\" fill=\"#fbfbfc\"/><text x=\"60\" y=\"26\" font-size=\"15\" font-weight=\"bold\" fill=\"#111\">Bay 05 \u2014 20ft slot  \u00b7 MAERSK ALABAMA</text><text x=\"60\" y=\"40\" font-size=\"10\" fill=\"#666\">looking forward \u2014 port (even rows) left \u00b7 starboard (odd rows) right</text><text x=\"50\" y=\"110.0\" text-anchor=\"end\" font-size=\"9\" fill=\"#555\">86</text><g><title>CBHU6818017  45R1\n40ft Refrigerated (R1) HC\nslot 0050186  bay 5 row 1 tier 86 (starboard, on deck)\nEMPTY\nreefer -18CEL</title><polygon points=\"114,92 130,76 130,106 114,122\" fill=\"#187584\" stroke=\"#11515c\" stroke-width=\"1\"/><polygon points=\"60,92 76,76 130,76 114,92\" fill=\"#23abc1\" stroke=\"#11515c\" stroke-width=\"1\"/><rect x=\"60\" y=\"92\" width=\"54\" height=\"30\" fill=\"#eef0f2\" stroke=\"#11515c\" stroke-width=\"1.2\" stroke-dasharray=\"3 2\"/><rect x=\"60\" y=\"92\" width=\"54\" height=\"4\" fill=\"#1a1a1a\" opacity=\"0.65\"/><text x=\"63\" y=\"102\" font-size=\"11\" fill=\"#0a6\">\u2744</text></g><text x=\"87.0\" y=\"134\" text-anchor=\"middle\" font-size=\"9\" fill=\"#555\">01<tspan fill=\"#999\">S</tspan></text><g transform=\"translate(60,196)\"><g font-family=\"sans-serif\" font-size=\"10\"><rect x=\"0\" y=\"0\" width=\"12\" height=\"12\" fill=\"#5b8def\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"16\" y=\"10\" fill=\"#222\">Dry</text><rect x=\"57\" y=\"0\" width=\"12\" height=\"12\" fill=\"#22a3b8\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"73\" y=\"10\" fill=\"#222\">Reefer</text><rect x=\"141\" y=\"0\" width=\"12\" height=\"12\" fill=\"#e0a13a\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"157\" y=\"10\" fill=\"#222\">Open-top</text><rect x=\"243\" y=\"0\" width=\"12\" height=\"12\" fill=\"#9b6dd6\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"259\" y=\"10\" fill=\"#222\">Flat rack</text><rect x=\"354\" y=\"0\" width=\"12\" height=\"12\" fill=\"#c65d5d\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"370\" y=\"10\" fill=\"#222\">Tank</text><rect x=\"420\" y=\"0\" width=\"12\" height=\"12\" fill=\"#7a8a5a\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"436\" y=\"10\" fill=\"#222\">Bulk</text><rect x=\"486\" y=\"0\" width=\"12\" height=\"12\" fill=\"#9aa0a6\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"502\" y=\"10\" fill=\"#222\">Undefined</text><text x=\"597\" y=\"10\" fill=\"#0a6\">\u2744 reefer</text><text x=\"667\" y=\"10\" fill=\"#d33\">\u25c6 hazmat</text></g></g></svg>","7":"<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"226\" height=\"212\" font-family=\"sans-serif\" viewBox=\"0 0 226 212\"><defs><marker id=\"ovh\" markerWidth=\"6\" markerHeight=\"6\" refX=\"5\" refY=\"3\" orient=\"auto\"><path d=\"M0,0 L6,3 L0,6 z\" fill=\"#b00\"/></marker></defs><rect x=\"0\" y=\"0\" width=\"226\" height=\"212\" fill=\"#fbfbfc\"/><text x=\"60\" y=\"26\" font-size=\"15\" font-weight=\"bold\" fill=\"#111\">Bay 07 \u2014 20ft slot  \u00b7 MAERSK ALABAMA</text><text x=\"60\" y=\"40\" font-size=\"10\" fill=\"#666\">looking forward \u2014 port (even rows) left \u00b7 starboard (odd rows) right</text><text x=\"50\" y=\"110.0\" text-anchor=\"end\" font-size=\"9\" fill=\"#555\">88</text><g><title>HLXU1234561  22G1\n20ft General purpose (G1)\nslot 0070188  bay 7 row 1 tier 88 (starboard, on deck)\nfull\nIMDG 3 (IMD)</title><polygon points=\"114,92 130,76 130,106 114,122\" fill=\"#4165ac\" stroke=\"#2d4677\" stroke-width=\"1\"/><polygon points=\"60,92 76,76 130,76 114,92\" fill=\"#6ba6ff\" stroke=\"#2d4677\" stroke-width=\"1\"/><rect x=\"60\" y=\"92\" width=\"54\" height=\"30\" fill=\"#5b8def\" stroke=\"#2d4677\" stroke-width=\"1.2\"/><polygon points=\"67,93 72,98 67,103 62,98\" fill=\"#d33\" stroke=\"#7a0000\" stroke-width=\"0.7\"/><text x=\"67\" y=\"100\" text-anchor=\"middle\" font-size=\"5.5\" fill=\"#fff\" font-weight=\"bold\">3</text></g><text x=\"87.0\" y=\"134\" text-anchor=\"middle\" font-size=\"9\" fill=\"#555\">01<tspan fill=\"#999\">S</tspan></text><g transform=\"translate(60,196)\"><g font-family=\"sans-serif\" font-size=\"10\"><rect x=\"0\" y=\"0\" width=\"12\" height=\"12\" fill=\"#5b8def\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"16\" y=\"10\" fill=\"#222\">Dry</text><rect x=\"57\" y=\"0\" width=\"12\" height=\"12\" fill=\"#22a3b8\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"73\" y=\"10\" fill=\"#222\">Reefer</text><rect x=\"141\" y=\"0\" width=\"12\" height=\"12\" fill=\"#e0a13a\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"157\" y=\"10\" fill=\"#222\">Open-top</text><rect x=\"243\" y=\"0\" width=\"12\" height=\"12\" fill=\"#9b6dd6\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"259\" y=\"10\" fill=\"#222\">Flat rack</text><rect x=\"354\" y=\"0\" width=\"12\" height=\"12\" fill=\"#c65d5d\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"370\" y=\"10\" fill=\"#222\">Tank</text><rect x=\"420\" y=\"0\" width=\"12\" height=\"12\" fill=\"#7a8a5a\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"436\" y=\"10\" fill=\"#222\">Bulk</text><rect x=\"486\" y=\"0\" width=\"12\" height=\"12\" fill=\"#9aa0a6\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"502\" y=\"10\" fill=\"#222\">Undefined</text><text x=\"597\" y=\"10\" fill=\"#0a6\">\u2744 reefer</text><text x=\"667\" y=\"10\" fill=\"#d33\">\u25c6 hazmat</text></g></g></svg>","9":"<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"226\" height=\"212\" font-family=\"sans-serif\" viewBox=\"0 0 226 212\"><defs><marker id=\"ovh\" markerWidth=\"6\" markerHeight=\"6\" refX=\"5\" refY=\"3\" orient=\"auto\"><path d=\"M0,0 L6,3 L0,6 z\" fill=\"#b00\"/></marker></defs><rect x=\"0\" y=\"0\" width=\"226\" height=\"212\" fill=\"#fbfbfc\"/><text x=\"60\" y=\"26\" font-size=\"15\" font-weight=\"bold\" fill=\"#111\">Bay 09 \u2014 20ft slot  \u00b7 MAERSK ALABAMA</text><text x=\"60\" y=\"40\" font-size=\"10\" fill=\"#666\">looking forward \u2014 port (even rows) left \u00b7 starboard (odd rows) right</text><text x=\"50\" y=\"110.0\" text-anchor=\"end\" font-size=\"9\" fill=\"#555\">80</text><g><title>MEDU2345679  22G1\n20ft General purpose (G1)\nslot 0090180  bay 9 row 1 tier 80 (starboard, on deck)\nfull</title><polygon points=\"114,92 130,76 130,106 114,122\" fill=\"#4165ac\" stroke=\"#2d4677\" stroke-width=\"1\"/><polygon points=\"60,92 76,76 130,76 114,92\" fill=\"#6ba6ff\" stroke=\"#2d4677\" stroke-width=\"1\"/><rect x=\"60\" y=\"92\" width=\"54\" height=\"30\" fill=\"#5b8def\" stroke=\"#2d4677\" stroke-width=\"1.2\"/></g><text x=\"87.0\" y=\"134\" text-anchor=\"middle\" font-size=\"9\" fill=\"#555\">01<tspan fill=\"#999\">S</tspan></text><g transform=\"translate(60,196)\"><g font-family=\"sans-serif\" font-size=\"10\"><rect x=\"0\" y=\"0\" width=\"12\" height=\"12\" fill=\"#5b8def\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"16\" y=\"10\" fill=\"#222\">Dry</text><rect x=\"57\" y=\"0\" width=\"12\" height=\"12\" fill=\"#22a3b8\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"73\" y=\"10\" fill=\"#222\">Reefer</text><rect x=\"141\" y=\"0\" width=\"12\" height=\"12\" fill=\"#e0a13a\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"157\" y=\"10\" fill=\"#222\">Open-top</text><rect x=\"243\" y=\"0\" width=\"12\" height=\"12\" fill=\"#9b6dd6\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"259\" y=\"10\" fill=\"#222\">Flat rack</text><rect x=\"354\" y=\"0\" width=\"12\" height=\"12\" fill=\"#c65d5d\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"370\" y=\"10\" fill=\"#222\">Tank</text><rect x=\"420\" y=\"0\" width=\"12\" height=\"12\" fill=\"#7a8a5a\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"436\" y=\"10\" fill=\"#222\">Bulk</text><rect x=\"486\" y=\"0\" width=\"12\" height=\"12\" fill=\"#9aa0a6\" stroke=\"#333\" stroke-width=\"0.6\"/><text x=\"502\" y=\"10\" fill=\"#222\">Undefined</text><text x=\"597\" y=\"10\" fill=\"#0a6\">\u2744 reefer</text><text x=\"667\" y=\"10\" fill=\"#d33\">\u25c6 hazmat</text></g></g></svg>"},"unplaced":[]}}};

const FORMAT_LABEL = { edifact: "EDIFACT", snx: "SNX XML", x12: "ANSI X12", txt: "Plain text", xlsx: "Excel", unsupported: "Unsupported" };
const STATUS_LABEL = { corrected: "Corrected", flagged: "Flagged", valid: "Valid", invalid_structure: "Invalid" };

/* minimal diff: trim common prefix/suffix, return the differing middle of each */
function diffParts(a, b) {
  a = a || ""; b = b || "";
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i++;
  let j = 0;
  while (j < a.length - i && j < b.length - i && a[a.length - 1 - j] === b[b.length - 1 - j]) j++;
  return { pre: a.slice(0, i), aMid: a.slice(i, a.length - j), bMid: b.slice(i, b.length - j), post: a.slice(a.length - j) };
}

function CheckSlot({ body, printed, computed, status }) {
  return (
    <span className="cd">
      <span className="cd-body">{body}</span>
      <span className={`cd-slot ${status}`}>
        {status === "valid" && <span className="cd-ok">{printed}</span>}
        {status === "corrected" && (<>
          {printed ? <s className="cd-old">{printed}</s> : <span className="cd-empty">∅</span>}
          <span className="cd-arrow">→</span><b className="cd-new">{computed}</b>
        </>)}
        {status === "flagged" && (<>
          <span className="cd-warn">{printed || "·"}</span>
          <span className="cd-arrow">→</span><i className="cd-sugg">{computed}</i>
        </>)}
        {status === "invalid_structure" && <span className="cd-bad">{printed || "?"}</span>}
      </span>
    </span>
  );
}

function DiffLine({ before, after }) {
  const d = diffParts(before, after);
  return (
    <span className="snip">
      <span className="snip-pre">{d.pre}</span>
      {d.aMid && <s className="snip-old">{d.aMid}</s>}
      {d.bMid && <b className="snip-new">{d.bMid}</b>}
      <span className="snip-pre">{d.post}</span>
    </span>
  );
}

const STATS = [
  { key: "containers", label: "Containers", tone: "neutral" },
  { key: "corrected", label: "Corrected", tone: "corrected" },
  { key: "flagged", label: "Flagged", tone: "flagged" },
  { key: "valid", label: "Valid", tone: "valid" },
  { key: "invalid", label: "Invalid", tone: "invalid" },
];

/* CALC-PURE-BEGIN — mirrors equipment_checkdigit.py exactly; cross-validated under
   node against kernel-generated truth vectors (run_pass11.py). Do not "improve"
   one side without the other. */
const ISO_LETTER_VALUES = (() => {
  const vals = [10, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 23, 24,
                25, 26, 27, 28, 29, 30, 31, 32, 34, 35, 36, 37, 38];
  const m = {};
  "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("").forEach((ch, i) => { m[ch] = vals[i]; });
  return m;
})();

function calcExplain(raw) {
  const norm = (raw || "").replace(/[\s\-]/g, "").toUpperCase();
  if (!norm) return null;
  if (/^\d{11,12}$/.test(norm)) {
    const body = norm.slice(0, 11), printed = norm.length === 12 ? norm[11] : null;
    let total = 0; const steps = [];
    for (let i = 0; i < 11; i++) {                     // right-to-left, like the kernel
      const ch = body[10 - i];
      let p = Number(ch) * (i % 2 === 0 ? 2 : 1);
      if (p >= 10) p = Math.floor(p / 10) + (p % 10);
      steps.push({ char: ch, weight: i % 2 === 0 ? 2 : 1, product: p });
      total += p;
    }
    const computed = (10 - (total % 10)) % 10;
    const verdict = printed == null ? "computed" : (printed === String(computed) ? "valid" : "mismatch");
    return { ok: true, kind: "uic", normalized: norm, body, printed, steps,
             sum: total, computed, verdict, full: body + String(computed) };
  }
  if (/^[A-Z]{4}\d{6,7}$/.test(norm)) {
    const body = norm.slice(0, 10), printed = norm.length === 11 ? norm[10] : null;
    let total = 0; const chars = [];
    for (let i = 0; i < 10; i++) {
      const ch = body[i];
      const v = /\d/.test(ch) ? Number(ch) : ISO_LETTER_VALUES[ch];
      const w = 2 ** i, p = v * w;
      chars.push({ char: ch, value: v, weight: w, product: p });
      total += p;
    }
    const mod = total % 11;
    const computed = mod === 10 ? 0 : mod;
    const cat = body[3];
    const categorySet = "UJZ".includes(cat) ? "iso6346" : ("ABDEK".includes(cat) ? "ilu" : "unknown");
    const verdict = printed == null ? "computed" : (printed === String(computed) ? "valid" : "mismatch");
    return { ok: true, kind: "iso6346_ilu", normalized: norm, body, printed, chars,
             sum: total, mod, remainderTen: mod === 10, computed, category: cat,
             categorySet, verdict, full: body + String(computed) };
  }
  return { ok: false, error: "Enter 4 letters + 6–7 digits (ISO 6346 / ILU) or 11–12 digits (UIC wagon)." };
}
/* CALC-PURE-END */

/* ===========================================================================
   Pass 22 — Analytics Dashboard. Dependency-free inline SVG charts over the
   /insights warehouse payload. No chart library (keeps the no-deps ethos);
   small, legible, prints clean. All panels degrade to an empty-state when the
   relevant warehouse table has no rows yet (e.g. lanes/vessels before any
   carrier enrichment events exist).
=========================================================================== */
function fmtPct(x) { return x == null ? "\u2014" : (Math.round(x * 1000) / 10).toFixed(1) + "%"; }
function fmtInt(x) { return (x == null ? 0 : x).toLocaleString(); }

function Kpi({ label, value, sub, tone }) {
  return (
    <div className="kpi">
      <div className="kpi-v" style={tone ? { color: tone } : null}>{value}</div>
      <div className="kpi-l">{label}</div>
      {sub != null && <div className="kpi-s">{sub}</div>}
    </div>
  );
}

/* Grouped time-series: corrected vs flagged per day, as an overlaid line chart. */
function TrendChart({ rows }) {
  if (!rows || !rows.length) return <Empty msg="No ingestion history in this window." />;
  const W = 720, H = 200, P = 34;
  const xs = rows.map((_, i) => i);
  const maxY = Math.max(1, ...rows.map(r => Math.max(r.corrected || 0, r.flagged || 0, r.files || 0)));
  const xAt = i => P + (xs.length === 1 ? (W - 2 * P) / 2 : (i * (W - 2 * P)) / (xs.length - 1));
  const yAt = v => H - P - (v / maxY) * (H - 2 * P);
  const line = (key) => rows.map((r, i) => `${i === 0 ? "M" : "L"}${xAt(i).toFixed(1)},${yAt(r[key] || 0).toFixed(1)}`).join(" ");
  const series = [["files", "#7a8a9a"], ["corrected", "#46a758"], ["flagged", "#e0a13a"]];
  const ticks = 4;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="Error trend over time">
      {Array.from({ length: ticks + 1 }, (_, t) => {
        const y = P + (t * (H - 2 * P)) / ticks;
        const val = Math.round(maxY * (1 - t / ticks));
        return (<g key={t}>
          <line x1={P} y1={y} x2={W - P} y2={y} stroke="var(--line)" strokeWidth="1" />
          <text x={P - 6} y={y + 3} textAnchor="end" fontSize="9" fill="var(--muted)">{val}</text>
        </g>);
      })}
      {series.map(([k, c]) => <path key={k} d={line(k)} fill="none" stroke={c} strokeWidth="2" />)}
      {series.map(([k, c]) => rows.map((r, i) => (
        <circle key={k + i} cx={xAt(i)} cy={yAt(r[k] || 0)} r="2.4" fill={c}>
          <title>{`${r.day}\n${k}: ${r[k] || 0}`}</title>
        </circle>
      )))}
      {rows.map((r, i) => (i % Math.ceil(rows.length / 8) === 0 || i === rows.length - 1) && (
        <text key={"x" + i} x={xAt(i)} y={H - P + 14} textAnchor="middle" fontSize="8.5" fill="var(--muted)">
          {r.day.slice(5)}
        </text>
      ))}
      <g transform={`translate(${P},14)`} fontSize="10">
        {series.map(([k, c], i) => (<g key={k} transform={`translate(${i * 96},0)`}>
          <rect width="10" height="10" rx="2" fill={c} /><text x="14" y="9" fill="var(--text)" style={{ textTransform: "capitalize" }}>{k}</text>
        </g>))}
      </g>
    </svg>
  );
}

/* Horizontal bars with a fix-rate label — used for owner league + by-format. */
function BarRows({ rows, labelKey, labelName, max, bars, footer }) {
  if (!rows || !rows.length) return <Empty msg={`No ${footer || "data"} yet.`} />;
  const cap = max || Math.max(1, ...rows.map(r => bars.reduce((s, b) => Math.max(s, r[b.key] || 0), 0)));
  return (
    <div className="bars">
      {rows.map((r, i) => (
        <div className="bar-row" key={i}>
          <div className="bar-lab" title={labelName ? r[labelName] : ""}>
            <b>{r[labelKey] || "\u2014"}</b>{labelName && r[labelName] ? <span className="bar-sub">{r[labelName]}</span> : null}
          </div>
          <div className="bar-track">
            {bars.map((b) => {
              const v = r[b.key] || 0;
              return <div key={b.key} className="bar-fill" title={`${b.key}: ${v}`}
                style={{ width: `${(v / cap) * 100}%`, background: b.color }} />;
            })}
          </div>
          <div className="bar-val">
            {r.fix_rate != null ? <span className="pill">{fmtPct(r.fix_rate)} fixed</span> : null}
            <span className="bar-counts">
              {bars.map((b, j) => <span key={b.key} style={{ color: b.color }}>{j > 0 ? " / " : ""}{fmtInt(r[b.key] || 0)}</span>)}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}

function SimpleTable({ rows, cols, empty }) {
  if (!rows || !rows.length) return <Empty msg={empty} />;
  return (
    <table className="tbl">
      <thead><tr>{cols.map(c => <th key={c.key} style={c.num ? { textAlign: "right" } : null}>{c.label}</th>)}</tr></thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>{cols.map(c => (
            <td key={c.key} style={c.num ? { textAlign: "right", fontVariantNumeric: "tabular-nums" } : null}>
              {c.fmt ? c.fmt(r[c.key], r) : (r[c.key] != null ? r[c.key] : "\u2014")}
            </td>
          ))}</tr>
        ))}
      </tbody>
    </table>
  );
}

function Empty({ msg }) { return <div className="empty">{msg}</div>; }

function Dashboard({ data, loading, err, onReload, apiBase }) {
  if (err) return (
    <section className="panel">
      <div className="dash-head"><h2>Analytics</h2><button className="btn ghost" onClick={onReload}>Retry</button></div>
      <div className="err-box">Could not load analytics: {err}{!apiBase && " \u2014 the dashboard reads the live audit DB; point the app at a running API (it is empty in the static demo)."}</div>
    </section>
  );
  if (loading || !data) return (
    <section className="panel"><div className="dash-head"><h2>Analytics</h2></div>
      <div className="muted-row">{loading ? "Loading analytics\u2026" : "No data."}</div></section>
  );
  const t = data.totals || {};
  return (
    <div className="dash">
      <div className="dash-head">
        <h2>Analytics <span className="dash-sub">data-quality across everything this system has ingested</span></h2>
        <button className="btn ghost" onClick={onReload}>Refresh</button>
      </div>

      <div className="kpis">
        <Kpi label="Files processed" value={fmtInt(t.processed)} sub={`${fmtInt(t.rejected)} rejected`} />
        <Kpi label="Distinct containers" value={fmtInt(t.distinct_containers)} sub={`${fmtInt(t.containers)} sightings`} />
        <Kpi label="Check digits corrected" value={fmtInt(t.corrected)} tone="#46a758" />
        <Kpi label="Flagged for review" value={fmtInt(t.flagged)} tone="#e0a13a" sub={`${fmtInt(t.invalid)} malformed`} />
        <Kpi label="Overall fix rate" value={fmtPct(t.overall_fix_rate)} sub="corrected / (corrected+flagged)" />
      </div>

      <section className="panel">
        <h3 className="dash-h3">Error &amp; volume trend <span className="dash-note">last 30 days</span></h3>
        <TrendChart rows={data.error_trend} />
      </section>

      <div className="dash-2col">
        <section className="panel">
          <h3 className="dash-h3">Dirtiest partners <span className="dash-note">by owner prefix</span></h3>
          <p className="dash-cap">Who ships the most broken numbers. Bars: corrected vs flagged sightings.</p>
          <BarRows rows={data.owner_quality} labelKey="prefix" labelName="owner_name"
            bars={[{ key: "fixes", color: "#46a758" }, { key: "flags", color: "#e0a13a" }]}
            footer="owner activity" />
        </section>
        <section className="panel">
          <h3 className="dash-h3">Fix rate by file format</h3>
          <p className="dash-cap">Which formats arrive dirtiest. Bars: corrected vs flagged.</p>
          <BarRows rows={data.by_format} labelKey="format"
            bars={[{ key: "corrected", color: "#46a758" }, { key: "flagged", color: "#e0a13a" }]}
            footer="format data" />
        </section>
      </div>

      <div className="dash-2col">
        <section className="panel">
          <h3 className="dash-h3">Busiest locations <span className="dash-note">from carrier events</span></h3>
          <SimpleTable rows={data.top_locations}
            cols={[{ key: "unlocode", label: "UN/LOCODE" }, { key: "name", label: "Name" },
                   { key: "containers", label: "Containers", num: true, fmt: fmtInt },
                   { key: "events", label: "Events", num: true, fmt: fmtInt }]}
            empty="No carrier events yet — enrichment is opt-in; this fills once container events are fetched." />
        </section>
        <section className="panel">
          <h3 className="dash-h3">Top vessels <span className="dash-note">from carrier events</span></h3>
          <SimpleTable rows={data.top_vessels}
            cols={[{ key: "name", label: "Vessel" }, { key: "imo", label: "IMO" },
                   { key: "containers", label: "Containers", num: true, fmt: fmtInt },
                   { key: "events", label: "Events", num: true, fmt: fmtInt }]}
            empty="No vessel events yet." />
        </section>
      </div>

      <section className="panel">
        <h3 className="dash-h3">Busiest lanes <span className="dash-note">origin → destination, from event sequences</span></h3>
        <SimpleTable rows={data.lanes}
          cols={[{ key: "origin", label: "Origin" }, { key: "destination", label: "Destination" },
                 { key: "containers", label: "Containers", num: true, fmt: fmtInt },
                 { key: "legs", label: "Legs", num: true, fmt: fmtInt }]}
          empty="No multi-stop container histories yet — lanes derive from consecutive located events per container." />
      </section>
    </div>
  );
}

/* ===========================================================================
   Documentation view. A structured technical reference with a sticky section
   nav. Explains what the system is, the algorithms (with worked examples), every
   format and its trust model, the correction guarantees, the data model, the
   visualizers, deployment, and privacy. Self-contained; no data fetch.
=========================================================================== */
const DOC_SECTIONS = [
  ["what", "What it is"],
  ["quickstart", "Quick start"],
  ["algorithm", "The check-digit math"],
  ["formats", "Formats & trust"],
  ["guarantees", "Correction guarantees"],
  ["identifiers", "Identifier types"],
  ["policy", "Rules & owner policy"],
  ["batch", "Batch & SFTP"],
  ["enrichment", "Carrier enrichment"],
  ["warehouse", "Data & analytics"],
  ["viz", "Stowage visualizers"],
  ["privacy", "Privacy & security"],
  ["deploy", "Self-hosting"],
  ["api", "API reference"],
];

function DocH({ id, children }) { return <h2 id={`doc-${id}`} className="doc-h">{children}</h2>; }
function K({ children }) { return <code>{children}</code>; }

function Docs() {
  const [active, setActive] = useState("what");
  function jump(id) {
    setActive(id);
    const el = document.getElementById(`doc-${id}`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  return (
    <div className="docs">
      <aside className="doc-nav" aria-label="Documentation sections">
        <div className="doc-nav-h">Documentation</div>
        {DOC_SECTIONS.map(([id, label]) => (
          <button key={id} className={`doc-nav-b ${active === id ? "on" : ""}`} onClick={() => jump(id)}>{label}</button>
        ))}
      </aside>

      <article className="doc-body">
        <DocH id="what">What CHECKDIGIT is</DocH>
        <p>
          CHECKDIGIT is a self-hosted web application that finds, validates, and corrects the
          check digits of shipping-container and intermodal equipment identifiers inside the data
          files the industry actually exchanges — and tracks what it has seen. The headline job is
          narrow and done well: a single mistyped or mis-scanned digit in a container number can
          get a box rejected by a terminal system or break manifest reconciliation. The math to fix
          it is small; doing it correctly across every file format, <em>without corrupting anything
          else in the file</em>, and leaving an audit trail, is the work.
        </p>
        <p>
          Everything runs on hardware you control. The correction engine has zero third-party
          dependencies, every algorithm is documented below, and every behavioural claim on this
          page is enforced by a test in the suite.
        </p>
        <div className="doc-callout">
          <strong>Mental model:</strong> parse the file → locate every identifier → route each to the
          correct check-digit algorithm → recompute → splice the corrected digit back into the
          original bytes → report the diff → record it. Correctness, provenance, and visualization —
          never fabrication.
        </div>

        <DocH id="quickstart">Quick start</DocH>
        <ol className="doc-ol">
          <li>Go to <strong>Correct</strong> and drop a file (or paste text). Format is detected from
            the <em>content</em>, not the extension.</li>
          <li>Pick an <strong>owner policy</strong> (strict vs lenient) and, for plain text, the
            <strong> trust</strong> level — both explained under Formats &amp; trust.</li>
          <li>Press <strong>Validate &amp; correct</strong>. You get a per-identifier verdict
            (valid / corrected / flagged / invalid), a before→after diff, and the corrected file to
            download — byte-identical except the fixed digits.</li>
          <li>Use the calculator to check a single number by hand, or open the
            <strong> Dashboard</strong>, <strong>History</strong>, and <strong>Containers</strong>
            views for the cross-file picture.</li>
        </ol>
        <p className="doc-note">No backend running yet? The Correct view ships with sample files
          (SNX XML, X12) so you can see the full result UI without an API.</p>

        <DocH id="algorithm">The check-digit math</DocH>
        <p>The primary scheme is <strong>ISO 6346</strong>, the standard for container numbers. A
          number is four letters (owner + category) then six serial digits then one check digit, e.g.
          <K>MSKU 7351770</K> — the trailing <K>0</K> is the check digit.</p>
        <p className="doc-step"><strong>Step 1 — letters to values.</strong> Map each of the first 10
          characters. Letters use A=10, B=12, C=13, … skipping every multiple of 11 (so values 11,
          22, 33 are never used); digits are themselves.</p>
        <p className="doc-step"><strong>Step 2 — weight.</strong> Multiply position <em>i</em>
          (0-indexed) by 2<sup><em>i</em></sup>: weights 1, 2, 4, 8, 16, 32, 64, 128, 256, 512.</p>
        <p className="doc-step"><strong>Step 3 — sum, mod 11.</strong> Add the weighted values; take
          the sum modulo 11.</p>
        <p className="doc-step"><strong>Step 4 — the remainder-10 rule.</strong> If the remainder is
          10, the check digit is <strong>0</strong>. Otherwise it is the remainder.</p>
        <div className="doc-worked">
          <div className="dw-h">Worked example — <span className="mono">MSKU 735177?</span></div>
          <table className="dw-t">
            <thead><tr><th>char</th><th>value</th><th>×weight</th><th>=</th></tr></thead>
            <tbody>
              {[["M",24,1],["S",30,2],["K",21,4],["U",32,8],["7",7,16],["3",3,32],["5",5,64],["1",1,128],["7",7,256],["7",7,512]].map(([c,v,w],i)=>(
                <tr key={i}><td className="mono">{c}</td><td>{v}</td><td>{w}</td><td>{v*w}</td></tr>
              ))}
            </tbody>
          </table>
          <div className="dw-calc">sum = 11321 → 11321 mod 11 = 0 → check digit <strong>0</strong> →
            <span className="mono"> MSKU7351770</span> ✓</div>
        </div>
        <p>Other identifier types use their own schemes and CHECKDIGIT never crosses them: <strong>ILU
          </strong> codes (EN 13044) use the same mod-11; <strong>UIC</strong> rail wagon numbers use
          the <strong>Luhn</strong> (mod-10) algorithm. Size/type codes like <K>22G1</K> are
          descriptive, not check-bearing, so they are decoded and annotated, never "corrected".</p>

        <DocH id="formats">Formats &amp; the trust model</DocH>
        <p>Format is detected from content. Each format has a <em>trust level</em> that decides
          whether a failing check digit is <strong>corrected</strong> or only <strong>flagged</strong>:</p>
        <table className="doc-table">
          <thead><tr><th>Format</th><th>How identifiers are found</th><th>Default trust</th></tr></thead>
          <tbody>
            <tr><td>EDIFACT</td><td>EQD segments (BAPLIE, COPRAR, COPINO, CODECO, …)</td><td>High — structured field, corrected</td></tr>
            <tr><td>ANSI X12</td><td>N7 / W2 equipment, N9 references</td><td>High — corrected</td></tr>
            <tr><td>SNX XML</td><td>The four synchronized container attributes</td><td>High — corrected</td></tr>
            <tr><td>Excel (.xlsx)</td><td>Shared strings + inline cells</td><td>Cell-scoped; corrected</td></tr>
            <tr><td>CSV / TSV</td><td>A column you declare as the container column</td><td>Opt-in: declared column corrected, others untouched</td></tr>
            <tr><td>Fixed-width</td><td>A character range you declare</td><td>Opt-in: declared range corrected</td></tr>
            <tr><td>Plain text</td><td>Free-text scan for the identifier pattern</td><td>Low by default — flagged, not silently changed</td></tr>
          </tbody>
        </table>
        <div className="doc-callout">
          <strong>Why low-trust text matters.</strong> A bare list of container numbers in a .txt has
          no schema to vouch for it, so by default CHECKDIGIT <em>flags</em> failures rather than
          rewriting numbers it can't corroborate. Flip the <strong>Plain text</strong> toggle to
          "trusted list" when you know the file is a container list and want failures corrected. CSV
          and fixed-width are never auto-trusted — you point at the container column explicitly,
          because a CSV of numbers is still valid plain text.
        </div>
        <p><strong>Owner policy (strict vs lenient).</strong> Some numbers carry a non-standard owner
          prefix (e.g. carrier pseudo-prefixes like <K>L01U</K> in SOC/synthetic data). <strong>Strict
          </strong> flags those for a human; <strong>lenient</strong> applies the standard mod-11 to
          them. Strict is the safe default.</p>

        <DocH id="guarantees">Correction guarantees</DocH>
        <ul className="doc-list">
          <li><strong>Byte-splice, never re-serialize.</strong> CHECKDIGIT parses to <em>locate</em>
            the identifier's exact byte offset, then replaces only the wrong digit's bytes. It never
            rebuilds the file from a parsed model — so segment terminators, release characters,
            quoting, line endings, encoding, and every untouched field come back exactly as sent. A
            latin-1 EDIFACT returns latin-1; an Excel workbook keeps every chart and macro byte-identical.</li>
          <li><strong>One identifier, one algorithm.</strong> Each identifier is classified and routed
            to its own scheme. The ISO 6346 mod-11 is never applied to a UIC wagon or an ILU code.</li>
          <li><strong>Advisory, not silent.</strong> Near-miss "did you mean…" suggestions are shown,
            never auto-applied. When the body (not just the digit) looks wrong, that's surfaced for a
            human — the tool won't invent a container.</li>
          <li><strong>Fail loud.</strong> Missing dependencies or config surface immediately rather
            than degrading to a silent wrong answer.</li>
          <li><strong>Undefined stays undefined.</strong> An unrecognized size/type code is drawn grey
            with its raw value shown — never guessed into something plausible.</li>
        </ul>

        <DocH id="identifiers">Identifier types &amp; routing</DocH>
        <table className="doc-table">
          <thead><tr><th>Type</th><th>Standard</th><th>Check scheme</th><th>CHECKDIGIT action</th></tr></thead>
          <tbody>
            <tr><td>Container number</td><td>ISO 6346</td><td>mod-11, remainder-10→0</td><td>Validate &amp; correct</td></tr>
            <tr><td>ILU code</td><td>EN 13044</td><td>mod-11 (same as ISO 6346)</td><td>Validate &amp; correct</td></tr>
            <tr><td>UIC wagon</td><td>UIC</td><td>Luhn (mod-10)</td><td>Validate &amp; correct</td></tr>
            <tr><td>Size/type code</td><td>ISO 6346 Table</td><td>none (descriptive)</td><td>Decode &amp; annotate</td></tr>
          </tbody>
        </table>
        <p>When a type is ambiguous, or a niche local override might apply, CHECKDIGIT flags it for
          review rather than silently "correcting" it.</p>

        <DocH id="policy">Rules &amp; owner policy engine</DocH>
        <p>Beyond the per-run strict/lenient choice, an operator can define a persistent rule layer —
          because a terminal knows its own quirks. A policy resolves per 3-letter owner prefix in the
          order <strong>deny → allow → per-prefix → default</strong>:</p>
        <ul className="doc-list">
          <li><strong>deny</strong> — always flag for human review, even if the check digit is valid.
            The strongest guarantee; for prefixes you never want auto-touched.</li>
          <li><strong>allow</strong> — treat as a corroborated owner, so failures are corrected even at
            low trust (for legitimate lessor/pseudo prefixes your registry doesn't list).</li>
          <li><strong>per-prefix</strong> — override strict/lenient for specific owners.</li>
          <li><strong>default</strong> — applied to everything else.</li>
        </ul>
        <p>Policies are JSON, settable live via the API (<K>PUT /policy</K>) or a mounted file, and
          wildcards are supported (e.g. <K>TES*</K>).</p>

        <DocH id="batch">Batch &amp; SFTP automation</DocH>
        <p><strong>Batch.</strong> Submit many files at once — or a single <K>.zip</K> of them — and
          get back a zip containing each corrected file plus a consolidated report and manifest. A
          rejected or malformed member is recorded and skipped; one bad file never sinks the batch,
          and every member is audited.</p>
        <p><strong>SFTP watch-folder.</strong> A headless worker watches an inbox directory and runs
          the identical pipeline the moment a partner drops a file — guarded against half-written
          uploads — writing the corrected file plus a verdict CSV to an outbox. It shares the same
          audit database as the web app, so drops appear in History alongside uploads.</p>

        <DocH id="enrichment">Carrier enrichment</DocH>
        <p>CHECKDIGIT can attach known information to each container — owner identity (from the BIC
          owner-code register, instant and offline) and, optionally, live status from carrier APIs.
          The rules are strict and deliberate:</p>
        <ul className="doc-list">
          <li><strong>Opt-in only.</strong> Every external call is gated behind an environment
            variable you set with your own free API key. Nothing reaches the network by default.</li>
          <li><strong>Self-service APIs only.</strong> Maersk, CMA CGM, BIC BoxTech, Hapag-Lloyd and
            similar published endpoints — verified against their documentation.</li>
          <li><strong>No scraping.</strong> Public web tracking pages forbid automation; CHECKDIGIT
            respects that and never scrapes them.</li>
          <li><strong>Never blocks correction.</strong> Enrichment runs out of band; a slow or
            rate-limited API can't delay a correction.</li>
        </ul>

        <DocH id="warehouse">Data model &amp; analytics</DocH>
        <p>Every run writes an audit event (filename, format, counts, outcome, timestamp, user-agent —
          deliberately <em>not</em> the client IP). Underneath sits a relational warehouse that
          deduplicates owners, vessels, locations, and equipment types across containers, plus
          per-container counters (times seen, times corrected, times flagged) and stored carrier
          events.</p>
        <p>The <strong>Dashboard</strong> turns that into headline KPIs, an error/volume trend, a
          fix-rate league table by owner ("who ships the most broken numbers"), fix rate by file
          format, busiest locations and vessels, and origin→destination lanes derived from
          consecutive container events. It's all SQL over tables you already populated by using the
          tool — no extra setup.</p>

        <DocH id="viz">Stowage visualizers</DocH>
        <p>When a file describes a physical move, CHECKDIGIT can draw it as an isometric diagram built
          from the corrected data. <strong>Where you see it:</strong> correct a BAPLIE, rail, or truck
          file in the <strong>Correct</strong> view and the drawing appears automatically in a
          <em> Stowage</em> panel right below the results — vessel files get a tab per bay; rail and
          truck get a single diagram. The same render is available headless at <K>POST /visualize</K>
          (returns the SVG) for embedding elsewhere.</p>
        <ul className="doc-list">
          <li><Ship size={14} className="doc-ic" /> <strong>Vessel bay plan</strong> (BAPLIE) — each
            container in its real stowage slot (bay / row / tier, ISO 9711), looking forward, with a
            hatch line splitting on-deck from in-hold, and callouts for reefer, hazmat (IMDG class +
            UN), oversize (overhang in cm), high-cube, and empties.</li>
          <li><Train size={14} className="doc-ic" /> <strong>Rail</strong> — a doublestack well-car
            cross-section and a train-consist sequence (X12 404 / 418, EDIFACT).</li>
          <li><Truck size={14} className="doc-ic" /> <strong>Truck</strong> — container(s) on a chassis,
            front/rear on a tandem (COPINO / CODECO).</li>
        </ul>
        <div className="doc-callout">
          <strong>Honesty in the drawing.</strong> Rail well/tier and truck front/rear positions are
          <em> not</em> carried in standard EDI — they're inferred from equipment type, container
          count/size, and loading rules. Anything inferred is drawn with a dashed outline and an
          "inferred" marker, and a banner says so. The tool never presents a derived position as if it
          were read from the file.
        </div>

        <DocH id="privacy">Privacy &amp; security</DocH>
        <ul className="doc-list">
          <li><strong>No client IP, ever.</strong> The audit log records the user-agent and request
            metadata but deliberately never reads or stores the client IP address.</li>
          <li><strong>Untrusted-upload hardening.</strong> XML parsing is guarded against XXE; uploads
            are size-capped (and rejections audited); a reverse-proxy body-size limit is the real DoS
            guard under load.</li>
          <li><strong>Your data stays yours.</strong> Self-hosted end to end; external enrichment is
            opt-in per source; nothing leaves the box unless you turn it on.</li>
          <li><strong>Concurrency-safe.</strong> WAL journaling, a busy-timeout so concurrent writers
            wait instead of failing, atomic policy swaps, and race-safe startup — load-tested with
            parallel uploads and multiple processes sharing one database.</li>
        </ul>

        <DocH id="deploy">Self-hosting</DocH>
        <p>The app <em>is</em> the web server — a Python/FastAPI process serves both the interface and
          the API on one origin. Designed for a homelab on residential internet:</p>
        <ul className="doc-list">
          <li><strong>Exposure.</strong> Tunnel-first (Cloudflare Tunnel / Tailscale) to work behind
            CGNAT and a dynamic IP without forwarding ports; Caddy in front for automatic HTTPS.</li>
          <li><strong>Run it.</strong> <K>docker compose up</K> for the full stack (app + SFTP worker +
            replication), or a single <K>uvicorn</K> process. One worker is plenty for a homelab; the
            multi-worker path is documented.</li>
          <li><strong>One machine.</strong> SQLite is single-host by design — simple, fast, and right
            for this scale. Don't put the database on a network share.</li>
          <li><strong>Backups.</strong> Continuous WAL replication (Litestream) is wired in.</li>
        </ul>
        <p className="doc-note">Full runbooks ship in the bundle: <K>RUN_ON_LAPTOP.md</K>,
          <K> DEPLOY.md</K>, and <K>DEPLOY_PUBLIC.md</K>.</p>

        <DocH id="api">API reference</DocH>
        <p>Every interface action is an HTTP endpoint; the app is API-first.</p>
        <table className="doc-table api">
          <thead><tr><th>Method</th><th>Path</th><th>Purpose</th></tr></thead>
          <tbody>
            {[
              ["POST","/correct","Validate & correct one file or pasted text"],
              ["POST","/correct/batch","Many files or a zip → corrected zip + report"],
              ["POST","/visualize","Render a BAPLIE / rail / truck file as isometric SVG"],
              ["GET","/check/{token}","Check one identifier + near-miss suggestions"],
              ["GET","/insights","Dashboard analytics (totals, trend, league tables, lanes)"],
              ["GET","/events","The ingestion / audit log"],
              ["GET","/containers","The container registry"],
              ["GET","/containers/{eqid}","One container + its event history"],
              ["GET / PUT","/policy","Read or replace the operator rule layer"],
              ["GET","/sources","Enrichment sources and their access tiers"],
              ["GET","/portwatch/{iso3}","IMF PortWatch port activity by country (open data)"],
              ["GET","/health","Liveness"],
            ].map(([m,p,d],i)=>(
              <tr key={i}><td className="mono api-m">{m}</td><td className="mono">{p}</td><td>{d}</td></tr>
            ))}
          </tbody>
        </table>
        <div className="doc-foot">
          CHECKDIGIT · self-hosted container check-digit correction · ISO 6346 / ILU / UIC ·
          EDIFACT · X12 · SNX · XLSX · CSV · fixed-width · text
        </div>
      </article>
    </div>
  );
}

/* ===========================================================================
   StowageView — the physical representation of an uploaded file. When a BAPLIE,
   rail (X12 404/418/322), or truck (COPINO/CODECO) file is corrected, the
   service returns a `visualization` block built from the CORRECTED data. This
   renders it inline beneath the results: the isometric SVG(s), a transport
   badge, counts, the inference-honesty banner (rail/truck), and any
   unplaced/omitted notices (vessel). The SVG is our own server-generated markup.
=========================================================================== */
function TransportBadge({ transport, view }) {
  const map = {
    vessel: ["Vessel bay plan", Ship],
    rail: [view === "consist" ? "Rail consist" : "Rail — doublestack", Train],
    truck: ["Truck — chassis", Truck],
  };
  const [label, Icon] = map[transport] || ["Stowage", Boxes];
  return <span className="stow-badge"><Icon size={14} strokeWidth={1.8} /> {label}</span>;
}

function CountChips({ counts }) {
  if (!counts) return null;
  const order = [
    ["total", "total"], ["containers", "containers"], ["cars", "cars"],
    ["loaded", "loaded"], ["empty", "empty"], ["reefer", "reefer"],
    ["hazmat", "hazmat"], ["oversize", "oversize"], ["undefined", "undefined"],
    ["chassis", "chassis"],
  ];
  const shown = order.filter(([k]) => counts[k] != null && counts[k] !== 0);
  if (!shown.length) return null;
  return (
    <div className="stow-counts">
      {shown.map(([k, label]) => (
        <span key={k} className={`stow-chip sc-${k}`}>{counts[k]} {label}</span>
      ))}
    </div>
  );
}

function StowageView({ viz }) {
  // two payload shapes: vessel has svg_by_bay (+ unplaced/omitted); rail/truck
  // has a single svg (+ inference_note).
  const isVessel = viz.transport === "vessel" || !!viz.svg_by_bay;
  const bays = isVessel ? (viz.svg_by_bay || {}) : null;
  const bayKeys = bays ? Object.keys(bays) : [];
  const [activeBay, setActiveBay] = useState(bayKeys[0] || null);
  const bayToShow = activeBay && bays[activeBay] ? activeBay : bayKeys[0];

  return (
    <section className="panel stow">
      <div className="stow-head">
        <div className="stow-title">
          <TransportBadge transport={viz.transport} view={viz.view} />
          {viz.vessel_name && <span className="stow-vessel">{viz.vessel_name}</span>}
          <span className="stow-from">built from the corrected file</span>
        </div>
        <CountChips counts={viz.counts} />
      </div>

      {/* rail/truck honesty banner: derived geometry is not in the EDI */}
      {viz.inference_note && (
        <div className="stow-infer">
          <span className="si-mark">i</span>
          <span>{viz.inference_note} Inferred geometry is drawn dashed and marked
            <em> inferred</em>.</span>
        </div>
      )}

      {/* vessel: a tab per bay; rail/truck: one drawing */}
      {isVessel ? (
        <>
          {bayKeys.length > 1 && (
            <div className="stow-bays" role="tablist" aria-label="Bays">
              {bayKeys.map((b) => (
                <button key={b} role="tab" aria-selected={b === bayToShow}
                  className={`stow-bay-b ${b === bayToShow ? "on" : ""}`}
                  onClick={() => setActiveBay(b)}>Bay {b}</button>
              ))}
            </div>
          )}
          <div className="stow-canvas" dangerouslySetInnerHTML={{ __html: bays[bayToShow] || "" }} />
          {viz.omitted_bays && viz.omitted_bays.length > 0 && (
            <div className="stow-note">Showing {bayKeys.length} of {bayKeys.length + viz.omitted_bays.length} bays;
              bays {viz.omitted_bays.join(", ")} omitted for size. Use <code>/visualize</code> for the full set.</div>
          )}
          {viz.unplaced && viz.unplaced.length > 0 && (
            <div className="stow-unplaced">
              <strong>{viz.unplaced.length} container{viz.unplaced.length > 1 ? "s" : ""} without a stowage position</strong>
              {" "}(present in the file but no parseable bay/row/tier):{" "}
              <span className="mono">{viz.unplaced.map((u) => u.container_id).join(", ")}</span>
            </div>
          )}
        </>
      ) : (
        <div className="stow-canvas" dangerouslySetInnerHTML={{ __html: viz.svg || "" }} />
      )}

      <div className="stow-foot">
        Drawn from the corrected container numbers. Hover any unit for its detail.
        {" "}Reefer, hazmat, oversize, high-cube and empty are flagged; an unrecognized
        size/type is shown grey rather than guessed.
      </div>
    </section>
  );
}

// Shown in place of an admin view (dashboard/history/registry) when a backend is
// reachable but no admin session exists. Uploading/correcting stays public; only
// the warehouse views are gated. The button does a top-level navigation to the
// Google sign-in route (OAuth requires full-page redirects, not fetch).
function SignInGate({ kind, onSignIn }) {
  const LABEL = { dashboard: "the dashboard", history: "the history log",
                  containers: "the container registry" };
  return (
    <section className="panel gate">
      <Lock size={22} strokeWidth={1.5} className="gate-ic" />
      <div className="gate-t">Sign in to view {LABEL[kind] || kind}</div>
      <div className="gate-d">
        These views read the audit warehouse and are restricted to the administrator.
        Uploading and correcting files stays open to everyone — only the analytics,
        history, and registry require sign-in.
      </div>
      <button className="gate-btn" onClick={onSignIn}>
        <Lock size={14} strokeWidth={2} /> Sign in with Google
      </button>
    </section>
  );
}

export default function App() {
  const [report, setReport] = useState(null);
  const [viz, setViz] = useState(null);                // stowage visualization (vessel/rail/truck)
  const [filename, setFilename] = useState("");
  const [file, setFile] = useState(null);
  const [pasted, setPasted] = useState("");
  const [calcIn, setCalcIn] = useState("");
  const [nm, setNm] = useState(null);                  // calculator near-miss search
  const [view, setView] = useState("work");            // work | dashboard | history | containers
  const [admin, setAdmin] = useState(null);            // null=checking · "offline"=no backend · false=signed-out · {email}=signed-in
  const [insights, setInsights] = useState(null);      // /insights analytics payload
  const [insErr, setInsErr] = useState("");
  const [insLoading, setInsLoading] = useState(false);
  const [events, setEvents] = useState(null);          // ingestion history rows
  const [evTotal, setEvTotal] = useState(null);
  const [evErr, setEvErr] = useState("");
  const [evLoading, setEvLoading] = useState(false);
  const [regs, setRegs] = useState(null);              // container registry rows
  const [regErr, setRegErr] = useState("");
  const [regLoading, setRegLoading] = useState(false);
  const [regQ, setRegQ] = useState("");
  const [dossier, setDossier] = useState(null);        // {container, enrichment, history}
  const [dosErr, setDosErr] = useState("");
  const [dosLoading, setDosLoading] = useState(false);
  const [policy, setPolicy] = useState("strict");
  const [trust, setTrust] = useState(false);
  const [apiBase, setApiBase] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all");
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef(null);

  // Derived admin-auth flags driving nav + gate decisions.
  const signedIn = admin !== null && typeof admin === "object";
  const backendSignedOut = admin === false;

  const summary = report
    ? { containers: report.total_containers, corrected: report.corrected.length, flagged: report.flagged.length, valid: report.valid, invalid: report.invalid }
    : null;

  function loadSample(which) {
    setError(""); setFile(null); setPasted("");
    const raw = JSON.parse(JSON.stringify(FIXTURES[which]));
    // Some fixtures bundle a stowage visualization: {report, visualization}.
    // The plain ones are just the report object.
    const r = raw.report || raw;
    const v = raw.visualization || null;
    setFilename(r.filename);
    setReport(r); setFilter("all");
    setViz(v);
  }

  function pick(f) {
    if (!f) return;
    setFile(f); setFilename(f.name); setPasted(""); setError(""); setReport(null); setViz(null);
  }

  // Typing pasted text deselects any chosen file (and vice versa in pick()), so the
  // submit path always has exactly one input — mirroring the API's validation.
  function paste(v) {
    setPasted(v); setError("");
    if (v.trim()) { setFile(null); setFilename(""); }
  }

  // Turn a non-OK response into a specific, actionable message. FastAPI sends
  // {detail: "..."}; a proxy (Caddy's body cap, Cloudflare Access) may send HTML
  // or an empty body, so fall back to a status-based message.
  async function describeError(res) {
    let detail = "";
    try {
      if ((res.headers.get("content-type") || "").includes("application/json")) {
        const body = await res.json();
        if (typeof body?.detail === "string") detail = body.detail;
        else if (Array.isArray(body?.detail))
          detail = body.detail.map((d) => d?.msg).filter(Boolean).join("; ");
      }
    } catch (_e) { /* non-JSON or empty body */ }
    const byStatus = {
      413: "File is too large — the service caps uploads at 5 MB.",
      415: "That file type isn't supported. Upload EDIFACT, X12, SNX XML, or plain text.",
      422: "Invalid request parameter.",
      401: "Not authorized — your access session may have expired. Reload to sign in again.",
      403: "Not authorized — your access session may have expired. Reload to sign in again.",
      502: "The service is unreachable upstream (tunnel or backend may be down).",
      503: "The service is temporarily unavailable.",
    };
    return detail || byStatus[res.status] || `The service rejected the request (HTTP ${res.status}).`;
  }

  async function correct() {
    const usingText = pasted.trim().length > 0;
    if (!file && !usingText) { setError("Choose a file or paste text — or load a sample below."); return; }
    setLoading(true); setError("");
    let res;
    try {
      const fd = new FormData();
      if (usingText) fd.append("text", pasted);
      else fd.append("file", file);
      const qs = new URLSearchParams({ owner_policy: policy, trust: String(trust) });
      res = await fetch(`${apiBase}/correct?${qs}`, { method: "POST", body: fd });
    } catch (_e) {
      // fetch itself failed: backend down, DNS, CORS, or tunnel offline.
      setError(`Couldn't reach the correction service${apiBase ? ` at ${apiBase}` : ""}. Start the backend, or load a sample below to explore the interface.`);
      setLoading(false);
      return;
    }
    try {
      if (!res.ok) { setError(await describeError(res)); return; }
      const data = await res.json();
      const r = data.report || data;
      r.detected_format = data.detected_format || r.detected_format;
      r.filename = data.filename || (usingText ? "pasted.txt" : file.name);
      setReport(r); setFilter("all");
      setViz(data.visualization || null);             // stowage scene + SVG, when present
    } catch (_e) {
      setError("The service responded, but the result couldn't be read. Check the backend logs.");
    } finally { setLoading(false); }
  }

  function download() {
    if (!report) return;
    const base = (report.filename || "file").replace(/(\.[^.]+)$/, "");
    if (report.corrected_b64) {
      // Binary format (xlsx): decode base64 to bytes and save with the real MIME.
      const bin = atob(report.corrected_b64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      const blob = new Blob([bytes], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `${base}.corrected.xlsx`;
      a.click(); URL.revokeObjectURL(url);
      return;
    }
    if (report.corrected_text == null) return;
    const blob = new Blob([report.corrected_text], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const ext = (report.filename || "").match(/(\.[^.]+)$/);
    a.href = url; a.download = `${base}.corrected${ext ? ext[1] : ".txt"}`;
    a.click(); URL.revokeObjectURL(url);
  }

  function csvEscape(v) {
    const s = v == null ? "" : String(v);
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }

  // Per-container verdict table as CSV — the handoff artifact for a counterparty.
  // Client-side from the current report; past runs: GET /events/{id}/containers.csv
  function exportCsv() {
    if (!report) return;
    const reasons = {};
    (report.flagged || []).forEach(f => {
      if (!f.eqid) return;
      reasons[f.eqid] = reasons[f.eqid] ? `${reasons[f.eqid]}; ${f.reason}` : f.reason;
    });
    const cols = ["container", "as_found", "status", "owner", "category", "id_type",
                  "printed_check", "computed_check", "occurrences", "reason"];
    const lines = [cols.join(",")];
    (report.containers || []).forEach(c => {
      lines.push([c.canonical, c.as_found, c.status, c.owner, c.category, c.id_type,
                  c.printed_check, c.computed_check, c.occurrences,
                  reasons[c.canonical] || reasons[c.as_found] || ""].map(csvEscape).join(","));
    });
    const blob = new Blob(["\uFEFF" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const base = (report.filename || "report").replace(/(\.[^.]+)$/, "");
    a.href = url; a.download = `${base}.report.csv`;
    a.click(); URL.revokeObjectURL(url);
  }

  const containers = report ? report.containers.filter(c => filter === "all" || c.status === filter || (filter === "invalid" && c.status === "invalid_structure")) : [];

  const fmtTs = (ts) => (ts ? ts.slice(0, 16).replace("T", " ") : "—");

  // History / dossier views are live-only (they read the audit DB) — in sample
  // mode they show an honest "start the backend" state, never fabricated data.
  async function apiGet(path) {
    let res;
    try { res = await fetch(`${apiBase}${path}`); }
    catch (_e) {
      throw new Error(`Couldn't reach the correction service${apiBase ? ` at ${apiBase}` : ""}. Start the backend to browse live data.`);
    }
    if (!res.ok) {
      // Session expired mid-session: drop to signed-out and return to the public
      // view, so the nav reflects reality (admin tabs hide, sign-in reappears).
      if (res.status === 401 || res.status === 403) { setAdmin(false); setView("work"); }
      throw new Error(await describeError(res));
    }
    try { return await res.json(); }
    catch (_e) { throw new Error("The service responded, but the result couldn't be read."); }
  }

  async function loadEvents(reset = true) {
    setEvLoading(true); setEvErr("");
    try {
      const offset = reset ? 0 : (events ? events.length : 0);
      const data = await apiGet(`/events?limit=200&offset=${offset}`);
      setEvTotal(data.total ?? null);
      setEvents(reset ? (data.events || []) : [...(events || []), ...(data.events || [])]);
    } catch (e) { setEvErr(e.message); if (reset) setEvents(null); }
    finally { setEvLoading(false); }
  }

  async function loadRegs() {
    setRegLoading(true); setRegErr("");
    try {
      const data = await apiGet(`/containers?limit=1000`);
      setRegs(data.containers || []);
    } catch (e) { setRegErr(e.message); setRegs(null); }
    finally { setRegLoading(false); }
  }

  async function openDossier(eqid) {
    setView("containers"); setDossier(null); setDosErr(""); setDosLoading(true);
    if (regs == null && !regLoading) loadRegs();         // so "back" has a list
    try { setDossier(await apiGet(`/containers/${encodeURIComponent(eqid)}`)); }
    catch (e) { setDosErr(e.message); }
    finally { setDosLoading(false); }
  }

  function switchView(v) {
    setView(v);
    // Signed out but a backend exists: show the sign-in gate instead of firing a
    // fetch that would 401. Offline/sample mode still loads (honest empty state).
    if (backendSignedOut && (v === "dashboard" || v === "history" || v === "containers")) return;
    if (v === "history") loadEvents(true);               // always refetch: data
    if (v === "containers") { setDossier(null); setDosErr(""); loadRegs(); }  // changes per run
    if (v === "dashboard") loadInsights();
  }

  async function loadInsights() {
    setInsLoading(true); setInsErr("");
    try {
      const data = await apiGet(`/insights?limit=15&trend_days=30`);
      setInsights(data);
    } catch (e) { setInsErr(e.message); }
    finally { setInsLoading(false); }
  }

  // --- Admin session (Google sign-in) ------------------------------------- //
  function signIn() {
    // Top-level navigation, NOT fetch: OAuth requires full-page redirects to Google.
    window.location.href = `${apiBase}/admin/auth/login`;
  }
  async function signOut() {
    try { await fetch(`${apiBase}/admin/logout`, { method: "POST" }); } catch (_e) { /* ignore */ }
    setAdmin(false);
    if (view === "dashboard" || view === "history" || view === "containers") setView("work");
  }

  // On load (and if the API base changes) ask the server who we are. 200 -> signed
  // in; 401 -> a backend exists but we are signed out; network error -> no backend
  // (standalone/sample mode), which preserves the existing offline behavior.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch(`${apiBase}/admin/whoami`);
        if (!alive) return;
        if (res.ok) {
          const d = await res.json().catch(() => null);
          setAdmin(d && d.admin ? { email: d.email } : false);
        } else {
          setAdmin(false);
        }
      } catch (_e) {
        if (alive) setAdmin("offline");
      }
    })();
    return () => { alive = false; };
  }, [apiBase]);

  // If a sign-in resolves while already sitting on an admin view, hydrate it
  // (covers the race where the view was opened before whoami returned).
  useEffect(() => {
    if (!signedIn) return;
    if (view === "dashboard" && insights == null && !insLoading) loadInsights();
    else if (view === "history" && events == null && !evLoading) loadEvents(true);
    else if (view === "containers" && regs == null && !regLoading) loadRegs();
  }, [admin]); // eslint-disable-line react-hooks/exhaustive-deps

  const regQU = regQ.trim().toUpperCase();
  const regFiltered = (regs || []).filter(r => !regQU || r.eqid.includes(regQU) || (r.owner || "").includes(regQU));

  // Calculator near-miss lookup: the math is client-side, but "which containers
  // has this system seen" lives in the DB -- so this one is an explicit, opt-in
  // round trip to /check rather than a per-keystroke fetch.
  async function searchNearMiss(token) {
    setNm({ loading: true });
    try {
      const data = await apiGet(`/check/${encodeURIComponent(token)}`);
      setNm({ items: data.near_misses || [] });
    } catch (e) { setNm({ err: e.message }); }
  }

  return (
    <div className="root">
      <style>{CSS}</style>

      <header className="topbar">
        <div className="brand" onClick={() => switchView("work")} role="button" tabIndex={0}
             onKeyDown={(e) => { if (e.key === "Enter") switchView("work"); }} title="Home">
          <span className="mark" aria-hidden="true" />
          <span className="word">CHECKDIGIT</span>
          <span className="brand-by">container check-digit correction</span>
        </div>
        <nav className="nav" aria-label="Sections">
          <button className={`nav-b primary ${view === "work" ? "on" : ""}`} onClick={() => switchView("work")}>
            <ScanLine size={14} strokeWidth={2} /> Correct
          </button>
          {signedIn && (<>
          <span className="nav-div" aria-hidden="true" />
          {[["dashboard", "Dashboard"], ["history", "History"], ["containers", "Containers"]].map(([k, l]) => (
            <button key={k} className={`nav-b ${view === k ? "on" : ""}`} onClick={() => switchView(k)}>{l}</button>
          ))}
          </>)}
          <span className="nav-div" aria-hidden="true" />
          <button className={`nav-b ${view === "docs" ? "on" : ""}`} onClick={() => switchView("docs")}>
            <BookOpen size={14} strokeWidth={1.8} /> Docs
          </button>
          {backendSignedOut && (
            <button className="nav-b" onClick={signIn} title="Admin sign-in — dashboard, history, registry">
              <Lock size={13} strokeWidth={1.8} /> Sign in
            </button>
          )}
          {signedIn && (<>
            <span className="nav-div" aria-hidden="true" />
            <span className="who" title={admin.email}>{admin.email}</span>
            <button className="nav-b" onClick={signOut} title="Sign out">Sign out</button>
          </>)}
        </nav>
      </header>

      <main className="wrap">
        {view === "work" && (<>
        {/* Hero — orient a first-time visitor: what this is, what it does, one action */}
        <section className="hero">
          <div className="hero-main">
            <h1 className="hero-h">Fix broken container check digits<br />without corrupting the file.</h1>
            <p className="hero-p">
              Drop a terminal data file — EDIFACT, ANSI X12, SNX XML, Excel, or plain text —
              and CHECKDIGIT finds every equipment identifier, recomputes its ISO 6346 check
              digit, and splices in the correction. Same format, same bytes, only the wrong
              digits change. Then it shows you exactly what it touched.
            </p>
            <div className="hero-cta">
              <button className="go hero-go" onClick={() => inputRef.current?.click()}>
                <Upload size={16} strokeWidth={2} /> Validate a file
              </button>
              <button className="ghost-link" onClick={() => switchView("docs")}>
                How it works <ArrowRight size={14} />
              </button>
            </div>
            <div className="hero-meta">
              <span><ShieldCheck size={13} /> byte-exact, never re-serialized</span>
              <span><Lock size={13} /> self-hosted · no client IP stored</span>
              <span><Database size={13} /> every run audited</span>
            </div>
          </div>
          <ul className="hero-side">
            <li><span className="hs-k mono">ISO 6346</span> 20ft container, mod-11</li>
            <li><span className="hs-k mono">ILU</span> intermodal loading unit (EN 13044)</li>
            <li><span className="hs-k mono">UIC</span> rail wagon, Luhn</li>
            <li className="hs-note">Each identifier is routed to its own check-digit scheme — never the wrong algorithm.</li>
          </ul>
        </section>

        {/* Upload panel */}
        <section className="panel">
          <div
            className={`drop ${dragging ? "drag" : ""} ${file ? "has" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => { e.preventDefault(); setDragging(false); pick(e.dataTransfer.files[0]); }}
            onClick={() => inputRef.current?.click()}
            role="button" tabIndex={0}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") inputRef.current?.click(); }}
          >
            <input ref={inputRef} type="file" hidden onChange={(e) => pick(e.target.files[0])} />
            <Upload className="drop-ic" size={20} strokeWidth={1.6} />
            <div className="drop-main">{file ? file.name : "Drop a terminal file, or click to choose"}</div>
            <div className="drop-sub">{file ? "Ready to validate" : "Format is detected from content, not the extension — EDI, SNX, X12, Excel, or text"}</div>
          </div>

          <div className="or" aria-hidden="true"><span>or paste text</span></div>

          <textarea
            className={`paste ${pasted.trim() ? "has" : ""}`}
            value={pasted}
            onChange={(e) => paste(e.target.value)}
            placeholder={"Paste container numbers, EDIFACT, X12, or SNX here — format is detected from the content. A bare number follows the plain-text trust setting below."}
            rows={4}
            spellCheck={false}
          />

          <div className="controls">
            <div className="ctl">
              <span className="ctl-label">Owner policy</span>
              <div className="seg">
                {["strict", "lenient"].map(p => (
                  <button key={p} className={`seg-b ${policy === p ? "on" : ""}`} onClick={() => setPolicy(p)}>{p}</button>
                ))}
              </div>
              <span className="ctl-hint">Non-standard owner prefixes (e.g. <code>L01U</code>): strict flags, lenient corrects.</span>
            </div>

            <div className="ctl">
              <span className="ctl-label">Plain text</span>
              <button className={`toggle ${trust ? "on" : ""}`} onClick={() => setTrust(!trust)} role="switch" aria-checked={trust}>
                <span className="knob" /><span className="toggle-t">{trust ? "trusted list" : "low-trust"}</span>
              </button>
              <span className="ctl-hint">Trusted: correct failing check digits in .txt. Low-trust: flag them only.</span>
            </div>

            <div className="ctl grow">
              <span className="ctl-label">Service URL</span>
              <input className="api" value={apiBase} placeholder="(same origin)" onChange={(e) => setApiBase(e.target.value.trim())} />
              <span className="ctl-hint">Where the correction API is served. Leave blank when behind the same host.</span>
            </div>

            <button className="go" onClick={correct} disabled={loading}>
              <ScanLine size={16} strokeWidth={2} />{loading ? "Working…" : "Validate & correct"}
            </button>
          </div>

          <div className="samples">
            <span className="samples-l">No backend running? Explore with a sample:</span>
            <button className="chip" onClick={() => loadSample("snx")}><Boxes size={13} /> SNX XML</button>
            <button className="chip" onClick={() => loadSample("x12")}><FileText size={13} /> X12 terminal</button>
            <button className="chip" onClick={() => loadSample("baplie")}><Ship size={13} /> Vessel BAPLIE</button>
          </div>

          {error && <div className="err"><AlertCircle size={15} /> {error}</div>}
        </section>

        {/* Check-digit calculator — pure client-side mirror of the kernel */}
        {(() => {
          const calc = calcExplain(calcIn);
          return (
            <section className="panel calcp">
              <div className="calc-row">
                <ScanLine size={15} className="calc-ic" aria-hidden="true" />
                <input className="calc-in" value={calcIn} onChange={(e) => { setCalcIn(e.target.value); setNm(null); }}
                  placeholder="Quick check: type a number — 10 chars computes the digit, 11 verifies (UIC: 11–12 digits)"
                  spellCheck={false} maxLength={24} aria-label="Check-digit calculator" />
              </div>

              {calc && !calc.ok && (
                <div className="calc-err"><AlertCircle size={13} /> {calc.error}</div>
              )}

              {calc && calc.ok && calc.kind === "iso6346_ilu" && (
                <div className="calc-out">
                  <div className="calc-chip">
                    <CheckSlot body={calc.body}
                      printed={calc.verdict === "computed" ? String(calc.computed) : calc.printed}
                      computed={String(calc.computed)}
                      status={calc.verdict === "mismatch" ? "corrected" : "valid"} />
                  </div>
                  <div className="calc-tablewrap">
                    <table className="calc-table">
                      <tbody>
                        <tr><th>char</th>{calc.chars.map((c, i) => <td key={i}>{c.char}</td>)}</tr>
                        <tr><th>value</th>{calc.chars.map((c, i) => <td key={i}>{c.value}</td>)}</tr>
                        <tr><th>weight 2^i</th>{calc.chars.map((c, i) => <td key={i}>{c.weight}</td>)}</tr>
                        <tr><th>product</th>{calc.chars.map((c, i) => <td key={i}>{c.product}</td>)}</tr>
                      </tbody>
                    </table>
                  </div>
                  <div className="calc-math">
                    Σ&nbsp;=&nbsp;<b>{calc.sum}</b> &nbsp;·&nbsp; {calc.sum} mod 11 = <b>{calc.mod}</b>
                    {calc.remainderTen && <> &nbsp;→&nbsp;<b>0</b> <span className="calc-note">(ISO 6346: remainder 10 maps to check digit 0)</span></>}
                    &nbsp;·&nbsp; check digit&nbsp;<b className="calc-cd">{calc.computed}</b>
                  </div>
                  <div className="calc-verdict">
                    {calc.verdict === "computed" && <>Full number: <span className="mono">{calc.full}</span></>}
                    {calc.verdict === "valid" && <span className="vok">Valid — printed {calc.printed} matches computed {calc.computed}.</span>}
                    {calc.verdict === "mismatch" && <span className="vbad">Mismatch — printed {calc.printed}, computed {calc.computed}. If the body is right, the number is <span className="mono">{calc.full}</span>.</span>}
                    {calc.categorySet === "ilu" && <span className="calc-note"> Category “{calc.category}” is ILU (EN 13044) — same algorithm.</span>}
                    {calc.categorySet === "unknown" && <span className="calc-note"> Category “{calc.category}” is outside ISO 6346 (U/J/Z) and ILU (A/B/D/E/K).</span>}
                  </div>
                  {calc.verdict === "mismatch" && (
                    <div className="calc-caveat">A failing check means the digit <i>or</i> the body is wrong — verify against the source document before correcting a single number on its own.</div>
                  )}
                  {calc.verdict === "mismatch" && (
                    <div className="calc-nm">
                      <button className="dl" onClick={() => searchNearMiss(calc.normalized)} disabled={nm && nm.loading}>
                        {nm && nm.loading ? "Searching…" : "Search seen containers for the body"}
                      </button>
                      {nm && nm.err && <span className="calc-note"> {nm.err}</span>}
                      {nm && nm.items && nm.items.length === 0 && (
                        <span className="calc-note"> No near-misses among containers this system has seen.</span>
                      )}
                      {nm && nm.items && nm.items.length > 0 && (
                        <span className="nm-set">
                          <span className="nm-l">did you mean</span>
                          {nm.items.map(s => (
                            <button key={s.eqid} className="nm-chip" onClick={() => openDossier(s.eqid)}
                              title={s.distance === 0 ? "Exact body match — only the check digit differs" : `Edit distance ${s.distance} from this body`}>
                              {s.eqid} <i>seen {s.times_seen}×{s.distance === 0 ? " · body match" : ` · Δ${s.distance}`}</i>
                            </button>
                          ))}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              )}

              {calc && calc.ok && calc.kind === "uic" && (
                <div className="calc-out">
                  <div className="calc-math">
                    UIC wagon — Luhn mod 10 over the first 11 digits: Σ&nbsp;=&nbsp;<b>{calc.sum}</b>
                    &nbsp;·&nbsp; check digit&nbsp;<b className="calc-cd">{calc.computed}</b>
                  </div>
                  <div className="calc-verdict">
                    {calc.verdict === "computed" && <>Full number: <span className="mono">{calc.full}</span></>}
                    {calc.verdict === "valid" && <span className="vok">Valid — printed {calc.printed} matches computed {calc.computed}.</span>}
                    {calc.verdict === "mismatch" && <span className="vbad">Mismatch — printed {calc.printed}, computed {calc.computed}.</span>}
                  </div>
                </div>
              )}

              {!report && !calcIn.trim() && (
                <p className="calc-blurb">Every container number ends in a check digit derived from the ten characters
                  before it. Type one above for the worked math, or drop a file and CHECKDIGIT corrects every number
                  in place — never altering the rest of the file.</p>
              )}
            </section>
          );
        })()}

        {/* Results */}
        {report && summary && (
          <section className="results">
            <div className="res-head">
              <div className="res-id">
                <span className={`fmt ${report.detected_format}`}>{FORMAT_LABEL[report.detected_format] || report.detected_format}</span>
                <span className="fname">{filename}</span>
                <span className="pol">policy: {report.owner_policy}</span>
              </div>
              <div className="res-actions">
                <button className="dl" onClick={exportCsv} title="Export the per-container verdict table as CSV">
                  <FileText size={15} /> Export CSV
                </button>
                <button className="dl" onClick={download} disabled={report.corrected_text == null && !report.corrected_b64}
                  title={report.corrected_text == null ? "Corrected file is returned by the live service" : "Download the corrected file"}>
                  <Download size={15} /> Download corrected
                </button>
              </div>
            </div>

            <div className="stats">
              {STATS.map(s => (
                <div key={s.key} className={`stat ${s.tone}`}>
                  <div className="stat-n">{summary[s.key]}</div>
                  <div className="stat-l">{s.label}</div>
                </div>
              ))}
            </div>

            {/* Changes */}
            {report.corrected.length > 0 && (
              <div className="block">
                <div className="eyebrow"><ShieldCheck size={13} /> Corrections — before → after</div>
                <div className="changes">
                  {report.corrected.map((c, i) => {
                    const body = (c.old || "").slice(0, 10);
                    return (
                      <div className="change" key={i}>
                        <div className="change-top">
                          <CheckSlot body={body} printed={c.printed_check} computed={c.computed_check} status="corrected" />
                          <span className="reason">{c.reason}</span>
                        </div>
                        <div className="occs">
                          <span className="occs-l">{c.occurrences.length === 1 ? "1 location" : `${c.occurrences.length} locations · kept in sync`}</span>
                          {c.occurrences.map((o, k) => (
                            <div className="occ" key={k}>
                              <span className="occ-loc">{o.label}</span>
                              <DiffLine before={o.before} after={o.after} />
                            </div>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Flagged */}
            {report.flagged.length > 0 && (
              <div className="block">
                <div className="eyebrow flag"><FlagTriangleRight size={13} /> Flagged — needs your review</div>
                <div className="flags">
                  {report.flagged.map((f, i) => (
                    <div className="flag-row" key={i}>
                      <span className="mono fid">{f.eqid}</span>
                      <span className="sugg-pill">suggested {f.suggested_check}</span>
                      <span className="flag-why">{f.reason}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Inventory */}
            <div className="block">
              <div className="eyebrow inv">
                <ScanLine size={13} /> Container inventory
                <div className="filters">
                  {["all", "corrected", "flagged", "valid", "invalid"].map(f => (
                    <button key={f} className={`fbtn ${filter === f ? "on" : ""}`} onClick={() => setFilter(f)}>{f}</button>
                  ))}
                </div>
              </div>
              <div className="tablewrap">
                <table className="tbl">
                  <thead><tr>
                    <th>As found</th><th>Canonical</th><th>Owner</th><th>Type</th><th>Status</th><th className="num">Seen</th><th>Check</th>
                  </tr></thead>
                  <tbody>
                    {containers.map((c, i) => (
                      <Fragment key={i}>
                        <tr>
                          <td className="mono">{c.as_found}</td>
                          <td className="mono strong">
                            <button className="linklike" onClick={() => openDossier(c.canonical)}
                              title="Open this container's dossier (live service)">{c.canonical}</button>
                          </td>
                          <td className="mono">{c.owner || "—"}</td>
                          <td>{c.id_type}</td>
                          <td><span className={`badge ${c.status}`}>{STATUS_LABEL[c.status] || c.status}</span></td>
                          <td className="num">{c.occurrences}</td>
                          <td className="mono small">{c.printed_check || "·"}{c.printed_check !== c.computed_check ? ` → ${c.computed_check}` : ""}</td>
                        </tr>
                        {(c.near_misses || []).length > 0 && (
                          <tr className="nm-row">
                            <td colSpan={7}>
                              <span className="nm-l">did you mean</span>
                              {c.near_misses.map(s => (
                                <button key={s.eqid} className="nm-chip" onClick={() => openDossier(s.eqid)}
                                  title={s.distance === 0 ? "Exact body match — only the check digit differs" : `Edit distance ${s.distance} from this body`}>
                                  {s.eqid} <i>seen {s.times_seen}×{s.distance === 0 ? " · body match" : ` · Δ${s.distance}`}</i>
                                </button>
                              ))}
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    ))}
                    {containers.length === 0 && <tr><td colSpan={7} className="none">No containers with that status.</td></tr>}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        )}

        {/* Stowage visualization — the physical representation of the file */}
        {viz && !viz.error && <StowageView viz={viz} />}

        {/* Secondary capabilities — correction is the show; these are one click away */}
        <section className="caps">
          <div className="caps-h">Beyond a single file</div>
          <div className="caps-grid">
            <button className="cap" onClick={() => switchView("dashboard")}>
              <Database size={18} strokeWidth={1.6} className="cap-ic" />
              <div className="cap-t">Dashboard</div>
              <div className="cap-d">Data-quality analytics across everything you've ingested — fix rates, dirtiest partners, trends.</div>
              <span className="cap-go">Open <ArrowRight size={13} /></span>
            </button>
            <button className="cap" onClick={() => switchView("history")}>
              <Layers size={18} strokeWidth={1.6} className="cap-ic" />
              <div className="cap-t">History</div>
              <div className="cap-d">The audit log — every file processed, with format, counts, and outcome, as a system of record.</div>
              <span className="cap-go">Open <ArrowRight size={13} /></span>
            </button>
            <button className="cap" onClick={() => switchView("containers")}>
              <Boxes size={18} strokeWidth={1.6} className="cap-ic" />
              <div className="cap-t">Containers</div>
              <div className="cap-d">The registry — every container seen, its owner, sighting counts, and enriched dossier.</div>
              <span className="cap-go">Open <ArrowRight size={13} /></span>
            </button>
            <button className="cap" onClick={() => switchView("docs")}>
              <BookOpen size={18} strokeWidth={1.6} className="cap-ic" />
              <div className="cap-t">Documentation</div>
              <div className="cap-d">The algorithms, formats, guarantees, visualizers, and deployment — the full technical reference.</div>
              <span className="cap-go">Read <ArrowRight size={13} /></span>
            </button>
          </div>
          <div className="caps-extra">
            Also built in: <strong>batch correction</strong> (many files or a zip in, corrected zip out),
            an <strong>SFTP watch-folder</strong> for automated partner drops, opt-in
            <strong> carrier enrichment</strong>, an operator <strong>rules engine</strong>, and
            isometric <strong>stowage visualizers</strong> for vessel bay plans, rail consists, and truck chassis.
            See <button className="inline-link" onClick={() => switchView("docs")}>the docs</button> for each.
          </div>
        </section>
        </>)}

        {/* Documentation view */}
        {/* Admin sign-in gate — shown in place of dashboard/history/containers when
            a backend is reachable but no admin session is present. */}
        {backendSignedOut && ["dashboard", "history", "containers"].includes(view) && (
          <SignInGate kind={view} onSignIn={signIn} />
        )}

        {view === "docs" && <Docs />}

        {/* Dashboard view — analytics over the warehouse (Pass 22) */}
        {view === "dashboard" && !backendSignedOut && (
          <Dashboard data={insights} loading={insLoading} err={insErr}
                     onReload={loadInsights} apiBase={apiBase} />
        )}

        {/* History view — the audit log as a system of record */}
        {view === "history" && !backendSignedOut && (
          <section className="panel">
            <div className="list-head">
              <span className="lh-title">Ingestion history</span>
              <span className="lh-meta">{evTotal != null ? `${evTotal} run${evTotal === 1 ? "" : "s"} recorded · audit stores filename, format & counts — no IP, no file contents` : ""}</span>
              <button className="dl" onClick={() => loadEvents(true)} disabled={evLoading}>{evLoading ? "Loading…" : "Refresh"}</button>
            </div>
            {evErr && <div className="err"><AlertCircle size={15} /> {evErr}</div>}
            {!evErr && events && events.length === 0 && (
              <p className="none-blurb">No runs recorded yet — correct a file or pasted text on the Workbench and it will appear here.</p>
            )}
            {!evErr && events && events.length > 0 && (
              <div className="tablewrap">
                <table className="dtable">
                  <thead><tr>
                    <th>when (UTC)</th><th>file</th><th>format</th><th>status</th>
                    <th className="num">boxes</th><th className="num">corrected</th><th className="num">flagged</th>
                    <th className="num">valid</th><th className="num">invalid</th><th></th>
                  </tr></thead>
                  <tbody>
                    {events.map(ev => (
                      <tr key={ev.id}>
                        <td className="mono dim nowrap">{fmtTs(ev.ts)}</td>
                        <td className="cell-name" title={ev.filename}>{ev.filename || "—"}</td>
                        <td>{ev.detected_format ? <span className={`fmt ${ev.detected_format}`}>{FORMAT_LABEL[ev.detected_format] || ev.detected_format}</span> : "—"}</td>
                        <td className={ev.status === "rejected" ? "rej" : "dim"}>{ev.status}{ev.reason ? ` · ${ev.reason}` : ""}</td>
                        <td className="num">{ev.total_containers}</td>
                        <td className="num c-corr">{ev.corrected}</td>
                        <td className="num c-flag">{ev.flagged}</td>
                        <td className="num c-val">{ev.valid}</td>
                        <td className="num c-inv">{ev.invalid}</td>
                        <td>{ev.status === "processed" && ev.total_containers > 0 ? (
                          <a className="csvlink" href={`${apiBase}/events/${ev.id}/containers.csv`}
                            title="Download this run's per-container verdict table (CSV)">CSV</a>
                        ) : null}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {!evErr && events && evTotal != null && events.length < evTotal && (
              <button className="dl more" onClick={() => loadEvents(false)} disabled={evLoading}>
                Load more ({events.length}/{evTotal})
              </button>
            )}
          </section>
        )}

        {/* Containers view — registry list, or one container's dossier */}
        {view === "containers" && !backendSignedOut && (
          <section className="panel">
            {(dosLoading || dosErr || dossier) ? (
              <div className="dossier">
                <button className="back" onClick={() => { setDossier(null); setDosErr(""); }}>← All containers</button>
                {dosLoading && <p className="none-blurb">Loading…</p>}
                {dosErr && <div className="err"><AlertCircle size={15} /> {dosErr}</div>}
                {dossier && (<>
                  <div className="dos-head">
                    <span className="dos-id mono">{dossier.container.eqid}</span>
                    <span className="dos-meta">owner <b>{dossier.container.owner || "—"}</b> · category <b>{dossier.container.category || "—"}</b> · {dossier.container.id_type}</span>
                  </div>
                  <div className="dos-seen">
                    Seen <b>{dossier.container.times_seen}</b>× · first {fmtTs(dossier.container.first_seen)} · last {fmtTs(dossier.container.last_seen)} UTC
                  </div>

                  <div className="dos-sec">Enrichment</div>
                  {dossier.enrichment ? (
                    <div className="kv">
                      {dossier.enrichment.owner_name && <div className="kv-row"><span>owner name</span><b>{dossier.enrichment.owner_name}</b></div>}
                      {(dossier.enrichment.owner_city || dossier.enrichment.owner_country) && (
                        <div className="kv-row"><span>registered</span><b>{[dossier.enrichment.owner_city, dossier.enrichment.owner_country].filter(Boolean).join(", ")}</b></div>
                      )}
                      {Object.entries(dossier.enrichment.details || {}).map(([k, v]) => (
                        <div className="kv-row" key={k}><span>{k.replace(/_/g, " ")}</span><b>{String(v)}</b></div>
                      ))}
                      <div className="kv-row"><span>source</span><b>{dossier.enrichment.source || "—"}</b></div>
                      <div className="kv-row"><span>enriched</span><b>{fmtTs(dossier.enrichment.enriched_at)} UTC</b></div>
                    </div>
                  ) : (
                    <p className="none-blurb">No enrichment recorded. The owner registry and external sources are opt-in — see <span className="mono">GET /sources</span>.</p>
                  )}

                  <div className="dos-sec">Appearances — every run this box was in</div>
                  <div className="tablewrap">
                    <table className="dtable">
                      <thead><tr><th>when (UTC)</th><th>file</th><th>format</th><th>as found</th><th>verdict</th><th>check</th><th className="num">occ.</th><th></th></tr></thead>
                      <tbody>
                        {(dossier.history || []).map((h, i) => (
                          <tr key={i}>
                            <td className="mono dim nowrap">{fmtTs(h.ts)}</td>
                            <td className="cell-name" title={h.filename}>{h.filename || "—"}</td>
                            <td>{h.detected_format ? <span className={`fmt ${h.detected_format}`}>{FORMAT_LABEL[h.detected_format] || h.detected_format}</span> : "—"}</td>
                            <td className="mono">{h.as_found}</td>
                            <td><span className={`badge ${h.role}`}>{STATUS_LABEL[h.role] || h.role}</span></td>
                            <td className="mono small">{h.printed_check || "·"}{h.printed_check !== h.computed_check ? ` → ${h.computed_check}` : ""}</td>
                            <td className="num">{h.occurrences}</td>
                            <td><a className="csvlink" href={`${apiBase}/events/${h.event_id}/containers.csv`} title="Download that run's verdict CSV">CSV</a></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>)}
              </div>
            ) : (
              <>
                <div className="list-head">
                  <span className="lh-title">Container registry</span>
                  <input className="api regq" value={regQ} onChange={(e) => setRegQ(e.target.value)}
                    placeholder="Filter by number or owner…" spellCheck={false} aria-label="Filter containers" />
                  <button className="dl" onClick={loadRegs} disabled={regLoading}>{regLoading ? "Loading…" : "Refresh"}</button>
                </div>
                {regErr && <div className="err"><AlertCircle size={15} /> {regErr}</div>}
                {!regErr && regs && regs.length === 0 && (
                  <p className="none-blurb">No containers recorded yet — every distinct number from a corrected file or paste lands here.</p>
                )}
                {!regErr && regs && regs.length > 0 && (<>
                  <div className="tablewrap">
                    <table className="dtable">
                      <thead><tr><th>container</th><th>owner</th><th>type</th><th className="num">seen</th><th>first (UTC)</th><th>last (UTC)</th></tr></thead>
                      <tbody>
                        {regFiltered.map(r => (
                          <tr key={r.eqid} className="rowlink" onClick={() => openDossier(r.eqid)} title="Open dossier">
                            <td className="mono strong">{r.eqid}</td>
                            <td className="mono">{r.owner || "—"}</td>
                            <td>{r.id_type}</td>
                            <td className="num">{r.times_seen}</td>
                            <td className="mono dim nowrap">{fmtTs(r.first_seen)}</td>
                            <td className="mono dim nowrap">{fmtTs(r.last_seen)}</td>
                          </tr>
                        ))}
                        {regFiltered.length === 0 && <tr><td colSpan={6} className="none">No match for that filter.</td></tr>}
                      </tbody>
                    </table>
                  </div>
                  <p className="lh-meta foot-meta">{regFiltered.length} of {regs.length} loaded{regs.length === 1000 ? " (first 1000 by frequency — narrow with ?owner= on the API for more)" : ""}</p>
                </>)}
              </>
            )}
          </section>
        )}
      </main>

      <footer className="foot">CHECKDIGIT · corrects check digits in place, never the surrounding data · audit log records filename, format & counts (no IP)</footer>
    </div>
  );
}

const CSS = `
:root{
  --bg:#0E1418; --panel:#141C23; --raised:#1A242E; --line:#293743; --line-soft:#1F2B35;
  --text:#E7EEF4; --muted:#90A0B0; --faint:#5E6F7E;
  --brand:#F3A93B; --corrected:#F3A93B; --valid:#52B98A; --flagged:#5B9DF0; --invalid:#E5604F;
  --mono:ui-monospace,"SF Mono","JetBrains Mono","Cascadia Code",Menlo,Consolas,monospace;
  --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
}
*{box-sizing:border-box}
.root{background:var(--bg);color:var(--text);font-family:var(--sans);min-height:100vh;font-size:14px;line-height:1.45}
.mono{font-family:var(--mono)}
code{font-family:var(--mono);background:var(--raised);padding:1px 5px;border-radius:4px;font-size:.85em;color:var(--muted)}

.topbar{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:16px 28px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:11px}
.mark{width:15px;height:15px;background:var(--brand);border-radius:3px;box-shadow:0 0 0 3px rgba(243,169,59,.16)}
.word{font-family:var(--mono);font-weight:700;letter-spacing:.34em;font-size:15px}
.tag{color:var(--faint);font-size:12px;letter-spacing:.02em}

.wrap{max-width:1060px;margin:0 auto;padding:28px 24px 12px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px}

.drop{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:5px;text-align:center;
  border:1.5px dashed var(--line);border-radius:11px;padding:26px 18px;cursor:pointer;transition:border-color .15s,background .15s}
.drop:hover,.drop:focus-visible{border-color:var(--brand);background:rgba(243,169,59,.04);outline:none}
.drop.drag{border-color:var(--brand);background:rgba(243,169,59,.07)}
.drop.has{border-style:solid;border-color:var(--valid)}
.drop-ic{color:var(--brand)}
.drop-main{font-weight:600}
.drop-sub{color:var(--faint);font-size:12px}

.controls{display:flex;flex-wrap:wrap;gap:18px 22px;align-items:flex-end;margin-top:18px}
.ctl{display:flex;flex-direction:column;gap:6px;min-width:150px}
.ctl.grow{flex:1;min-width:200px}
.ctl-label{font-size:11px;letter-spacing:.13em;text-transform:uppercase;color:var(--faint)}
.ctl-hint{font-size:11px;color:var(--faint);max-width:240px;line-height:1.35}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;width:fit-content}
.seg-b{background:transparent;color:var(--muted);border:0;padding:7px 15px;font:inherit;font-size:13px;cursor:pointer;text-transform:capitalize}
.seg-b.on{background:var(--brand);color:#1a1205;font-weight:600}
.toggle{display:inline-flex;align-items:center;gap:9px;background:var(--raised);border:1px solid var(--line);border-radius:20px;padding:5px 12px 5px 6px;cursor:pointer;color:var(--muted);font:inherit;font-size:13px;width:fit-content}
.toggle .knob{width:15px;height:15px;border-radius:50%;background:var(--faint);transition:background .15s,transform .15s}
.toggle.on{border-color:var(--flagged);color:var(--text)}
.toggle.on .knob{background:var(--flagged);transform:translateX(2px)}
.api{background:var(--raised);border:1px solid var(--line);border-radius:8px;padding:8px 11px;color:var(--text);font-family:var(--mono);font-size:13px;width:100%}
.api:focus{outline:none;border-color:var(--brand)}
.or{display:flex;align-items:center;gap:12px;margin:13px 0 10px;color:var(--faint);font-size:11.5px;letter-spacing:.06em;text-transform:uppercase}
.or::before,.or::after{content:"";flex:1;height:1px;background:var(--line-soft)}
.paste{display:block;width:100%;background:var(--raised);border:1px solid var(--line);border-radius:8px;padding:10px 12px;color:var(--text);font-family:var(--mono);font-size:12.5px;line-height:1.5;resize:vertical;min-height:74px;margin-bottom:15px}
.paste::placeholder{color:var(--faint)}
.paste:focus{outline:none;border-color:var(--brand)}
.paste.has{border-color:var(--brand);box-shadow:0 0 0 3px rgba(243,169,59,.10)}
.go{display:inline-flex;align-items:center;gap:8px;background:var(--brand);color:#1a1205;border:0;border-radius:9px;padding:11px 18px;font:inherit;font-weight:700;font-size:14px;cursor:pointer;margin-left:auto;transition:filter .15s}
.go:hover{filter:brightness(1.06)}
.go:disabled{opacity:.6;cursor:default}

.samples{display:flex;align-items:center;gap:9px;margin-top:16px;flex-wrap:wrap;padding-top:15px;border-top:1px solid var(--line-soft)}
.samples-l{color:var(--faint);font-size:12px}
.chip{display:inline-flex;align-items:center;gap:6px;background:var(--raised);border:1px solid var(--line);color:var(--text);border-radius:7px;padding:6px 11px;font:inherit;font-size:12.5px;cursor:pointer}
.chip:hover{border-color:var(--brand)}
.err{display:flex;align-items:flex-start;gap:8px;margin-top:14px;color:var(--invalid);background:rgba(229,96,79,.08);border:1px solid rgba(229,96,79,.25);border-radius:8px;padding:10px 12px;font-size:13px;line-height:1.4}

.calcp{margin-top:14px}
.calc-row{display:flex;align-items:center;gap:10px}
.calc-ic{color:var(--brand);flex:none}
.calc-in{flex:1;background:var(--raised);border:1px solid var(--line);border-radius:8px;padding:9px 12px;color:var(--text);font-family:var(--mono);font-size:13.5px;letter-spacing:.05em;min-width:0}
.calc-in:focus{outline:none;border-color:var(--brand)}
.calc-out{margin-top:14px;display:flex;flex-direction:column;gap:11px}
.calc-chip{font-size:16px}
.calc-tablewrap{overflow-x:auto}
.calc-table{border-collapse:collapse;font-size:12px}
.calc-table th{color:var(--faint);font-weight:500;text-align:left;padding:3px 10px 3px 0;font-size:11px;white-space:nowrap}
.calc-table td{font-family:var(--mono);text-align:center;padding:3px 8px;border-left:1px solid var(--line-soft);color:var(--muted);min-width:30px}
.calc-table tr:first-child td{color:var(--text);font-weight:600}
.calc-math{font-size:13px;color:var(--muted)}
.calc-cd{color:var(--brand);font-size:15px}
.calc-note{color:var(--faint);font-size:12px}
.calc-verdict{font-size:13px;line-height:1.5}
.calc-verdict .vok{color:var(--valid)}
.calc-verdict .vbad{color:var(--corrected)}
.calc-caveat{font-size:12px;color:var(--faint);border-left:2px solid var(--line);padding-left:10px;line-height:1.5}
.calc-err{display:flex;align-items:center;gap:7px;margin-top:10px;color:var(--faint);font-size:12.5px}
.calc-blurb{margin:13px 2px 0;color:var(--muted);font-size:13px;line-height:1.6;max-width:680px}
.res-actions{display:flex;gap:9px;flex-wrap:wrap}

.nav{display:flex;gap:4px;background:var(--raised);border:1px solid var(--line);border-radius:9px;padding:3px}
.nav-b{background:transparent;border:0;color:var(--muted);font:inherit;font-size:12.5px;padding:6px 13px;border-radius:7px;cursor:pointer}
.nav-b.on{background:var(--panel);color:var(--text);box-shadow:0 0 0 1px var(--line)}
.nav-b:hover:not(.on){color:var(--text)}

/* ---- Pass 22 dashboard ---- */
.dash{display:flex;flex-direction:column;gap:16px}
.dash-head{display:flex;align-items:center;justify-content:space-between;gap:12px}
.dash-head h2{font-size:18px;margin:0;font-weight:600}
.dash-sub{font-size:12px;color:var(--muted);font-weight:400;margin-left:8px}
.dash-h3{font-size:14px;margin:0 0 4px;font-weight:600}
.dash-note{font-size:11px;color:var(--muted);font-weight:400;margin-left:6px}
.dash-cap{font-size:11.5px;color:var(--muted);margin:0 0 12px}
.dash-2col{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media (max-width:820px){.dash-2col{grid-template-columns:1fr}}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}
@media (max-width:820px){.kpis{grid-template-columns:repeat(2,1fr)}}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.kpi-v{font-size:24px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.1}
.kpi-l{font-size:11.5px;color:var(--text);margin-top:4px}
.kpi-s{font-size:10.5px;color:var(--muted);margin-top:2px}
.chart{width:100%;height:auto;display:block}
.bars{display:flex;flex-direction:column;gap:8px}
.bar-row{display:grid;grid-template-columns:130px 1fr 140px;align-items:center;gap:10px}
@media (max-width:820px){.bar-row{grid-template-columns:90px 1fr 96px}}
.bar-lab{font-size:12px;display:flex;flex-direction:column;line-height:1.2;overflow:hidden}
.bar-lab b{font-variant-numeric:tabular-nums}
.bar-sub{font-size:10px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar-track{display:flex;height:16px;background:var(--raised);border-radius:5px;overflow:hidden}
.bar-fill{height:100%}
.bar-val{font-size:11px;text-align:right;display:flex;flex-direction:column;align-items:flex-end;gap:1px}
.bar-counts{font-variant-numeric:tabular-nums;font-size:10.5px}
.pill{background:var(--raised);border-radius:20px;padding:1px 8px;font-size:10px;color:var(--muted)}
.tbl{width:100%;border-collapse:collapse;font-size:12.5px}
.tbl th{text-align:left;color:var(--muted);font-weight:500;font-size:11px;padding:5px 8px;border-bottom:1px solid var(--line)}
.tbl td{padding:6px 8px;border-bottom:1px solid var(--line)}
.tbl tr:last-child td{border-bottom:0}
.empty{color:var(--muted);font-size:12px;padding:16px;text-align:center;background:var(--raised);border-radius:8px}
.muted-row{color:var(--muted);font-size:13px;padding:8px}
.err-box{color:#c44;font-size:13px;padding:12px;background:rgba(204,68,68,.06);border:1px solid rgba(204,68,68,.2);border-radius:8px}

.list-head{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:13px}
.lh-title{font-weight:600;font-size:14px}
.lh-meta{color:var(--faint);font-size:12px;margin-right:auto}
.regq{max-width:260px;flex:1}
.tablewrap{overflow-x:auto}
.dtable{width:100%;border-collapse:collapse;font-size:12.5px}
.dtable th{text-align:left;color:var(--faint);font-weight:500;font-size:11px;letter-spacing:.05em;text-transform:uppercase;padding:7px 10px;border-bottom:1px solid var(--line)}
.dtable td{padding:7px 10px;border-bottom:1px solid var(--line-soft);vertical-align:top}
.dtable .num{text-align:right}
.dtable .dim{color:var(--faint)}
.dtable .nowrap{white-space:nowrap}
.cell-name{max-width:230px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rej{color:var(--invalid)}
.c-corr{color:var(--corrected)}.c-flag{color:var(--flagged)}.c-val{color:var(--valid)}.c-inv{color:var(--invalid)}
.csvlink{color:var(--brand);text-decoration:none;font-size:12px;border:1px solid var(--line);border-radius:6px;padding:3px 8px;white-space:nowrap}
.csvlink:hover{border-color:var(--brand)}
.more{margin-top:12px}
.rowlink{cursor:pointer}
.rowlink:hover td{background:rgba(243,169,59,.05)}
.linklike{background:none;border:0;padding:0;color:var(--text);font:inherit;font-family:var(--mono);font-weight:inherit;cursor:pointer;text-decoration:underline dotted var(--faint);text-underline-offset:3px}
.linklike:hover{color:var(--brand)}
.none-blurb{color:var(--faint);font-size:13px;padding:8px 2px;line-height:1.5}
.back{background:none;border:0;color:var(--muted);font:inherit;font-size:13px;cursor:pointer;padding:0;margin-bottom:13px}
.back:hover{color:var(--brand)}
.dos-head{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
.dos-id{font-size:19px;font-weight:700;letter-spacing:.05em}
.dos-meta{color:var(--muted);font-size:13px}
.dos-seen{color:var(--faint);font-size:12.5px;margin-top:6px}
.dos-sec{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--faint);margin:18px 0 9px;padding-top:14px;border-top:1px solid var(--line-soft)}
.kv{display:flex;flex-direction:column;gap:5px;max-width:560px}
.kv-row{display:flex;gap:14px;font-size:13px}
.kv-row span{color:var(--faint);min-width:118px;flex:none}
.kv-row b{font-weight:600;word-break:break-word}
.foot-meta{margin-top:10px;margin-right:0}

.calc-nm{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.nm-set{display:inline-flex;align-items:center;gap:8px;flex-wrap:wrap}
.nm-row td{background:rgba(91,157,240,.05);border-bottom:1px solid var(--line-soft);padding:6px 10px 8px}
.nm-l{color:var(--flagged);font-size:11px;letter-spacing:.06em;text-transform:uppercase;margin-right:9px}
.nm-chip{display:inline-flex;align-items:baseline;gap:7px;background:var(--raised);border:1px solid var(--line);color:var(--text);border-radius:7px;padding:4px 10px;font-family:var(--mono);font-size:12.5px;cursor:pointer;margin-right:7px}
.nm-chip:hover{border-color:var(--flagged)}
.nm-chip i{font-style:normal;color:var(--faint);font-size:11px;font-family:var(--sans)}

.empty{text-align:center;padding:46px 22px 26px;color:var(--muted);max-width:560px;margin:0 auto}
.empty-cd{font-size:22px;margin-bottom:20px}
.empty p{line-height:1.6}

.results{margin-top:20px;animation:fade .3s ease both}
@keyframes fade{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}
.res-head{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:14px}
.res-id{display:flex;align-items:center;gap:11px;flex-wrap:wrap}
.fmt{font-family:var(--mono);font-size:11px;font-weight:700;letter-spacing:.08em;padding:4px 9px;border-radius:6px;background:var(--raised);border:1px solid var(--line);color:var(--text)}
.fmt.snx{color:#9ad0ff;border-color:rgba(91,157,240,.4)}
.fmt.x12{color:#ffd28a;border-color:rgba(243,169,59,.4)}
.fmt.edifact{color:#9af0c4;border-color:rgba(82,185,138,.4)}
.fmt.txt{color:var(--muted)}
.fname{font-family:var(--mono);font-size:13px}
.pol{color:var(--faint);font-size:12px}
.dl{display:inline-flex;align-items:center;gap:7px;background:transparent;border:1px solid var(--line);color:var(--text);border-radius:8px;padding:8px 13px;font:inherit;font-size:13px;cursor:pointer}
.dl:hover:not(:disabled){border-color:var(--brand)}
.dl:disabled{opacity:.45;cursor:default}

.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:22px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:14px 15px}
.stat-n{font-family:var(--mono);font-size:26px;font-weight:700;line-height:1}
.stat-l{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--faint);margin-top:7px}
.stat.corrected{border-color:rgba(243,169,59,.45)} .stat.corrected .stat-n{color:var(--corrected)}
.stat.flagged .stat-n{color:var(--flagged)}
.stat.valid .stat-n{color:var(--valid)}
.stat.invalid .stat-n{color:var(--invalid)}

.block{margin-bottom:24px}
.eyebrow{display:flex;align-items:center;gap:8px;font-size:11px;letter-spacing:.13em;text-transform:uppercase;color:var(--muted);margin-bottom:12px}
.eyebrow svg{color:var(--corrected)} .eyebrow.flag svg{color:var(--flagged)} .eyebrow.inv svg{color:var(--muted)}

/* signature: the check-digit slot */
.cd{font-family:var(--mono);font-size:17px;letter-spacing:.04em;display:inline-flex;align-items:center}
.cd-body{color:var(--muted)}
.cd-slot{display:inline-flex;align-items:center;gap:3px;margin-left:3px;padding:1px 7px;border-radius:6px;background:var(--raised);border:1px solid var(--line)}
.cd-slot.corrected{border-color:rgba(243,169,59,.5);background:rgba(243,169,59,.1)}
.cd-slot.valid{border-color:rgba(82,185,138,.45);background:rgba(82,185,138,.1)}
.cd-slot.flagged{border-color:rgba(91,157,240,.45);background:rgba(91,157,240,.1)}
.cd-old{color:var(--invalid);text-decoration-thickness:1.5px;opacity:.85}
.cd-new{color:var(--brand)} .cd-ok{color:var(--valid)} .cd-empty{color:var(--faint)}
.cd-warn{color:var(--muted)} .cd-sugg{color:var(--flagged);font-style:normal}
.cd-arrow{color:var(--faint);font-size:13px}
.cd-bad{color:var(--invalid)}

.changes{display:flex;flex-direction:column;gap:10px}
.change{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:14px 15px}
.change-top{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:11px}
.reason{color:var(--faint);font-size:12.5px}
.occs{border-top:1px solid var(--line-soft);padding-top:10px}
.occs-l{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--faint);display:block;margin-bottom:7px}
.occ{display:flex;gap:14px;align-items:baseline;padding:2px 0;flex-wrap:wrap}
.occ-loc{font-family:var(--mono);font-size:12px;color:var(--flagged);min-width:150px}
.snip{font-family:var(--mono);font-size:12.5px;color:var(--muted)}
.snip-pre{color:var(--faint)}
.snip-old{color:var(--invalid);opacity:.8;text-decoration-thickness:1.5px}
.snip-new{color:var(--brand)}

.flags{display:flex;flex-direction:column;gap:8px}
.flag-row{display:flex;align-items:center;gap:13px;flex-wrap:wrap;background:var(--panel);border:1px solid var(--line);border-left:2.5px solid var(--flagged);border-radius:9px;padding:11px 14px}
.fid{font-size:14px;font-weight:600}
.sugg-pill{font-family:var(--mono);font-size:12px;color:var(--flagged);background:rgba(91,157,240,.1);border:1px solid rgba(91,157,240,.3);padding:2px 9px;border-radius:20px}
.flag-why{color:var(--faint);font-size:12.5px;flex:1;min-width:200px}

.filters,.filters .fbtn{display:inline-flex}
.filters{margin-left:auto;gap:4px}
.fbtn{background:transparent;border:1px solid transparent;color:var(--faint);border-radius:6px;padding:3px 9px;font:inherit;font-size:11px;cursor:pointer;text-transform:capitalize;letter-spacing:.02em}
.fbtn:hover{color:var(--text)} .fbtn.on{color:var(--text);border-color:var(--line);background:var(--raised)}

.tablewrap{border:1px solid var(--line);border-radius:11px;overflow:hidden}
.tbl{width:100%;border-collapse:collapse;font-size:13px}
.tbl thead th{text-align:left;font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--faint);font-weight:600;padding:10px 14px;background:var(--raised);border-bottom:1px solid var(--line)}
.tbl th.num,.tbl td.num{text-align:right}
.tbl td{padding:9px 14px;border-bottom:1px solid var(--line-soft)}
.tbl tbody tr:last-child td{border-bottom:0}
.tbl tbody tr:hover{background:rgba(255,255,255,.015)}
.tbl td.strong{color:var(--text);font-weight:600}
.tbl td.mono{font-family:var(--mono)}
.tbl td.small{font-size:12px;color:var(--muted)}
.none{color:var(--faint);text-align:center;padding:18px}
.badge{font-size:11px;font-weight:600;padding:2px 9px;border-radius:20px;letter-spacing:.02em}
.badge.corrected{color:var(--corrected);background:rgba(243,169,59,.12)}
.badge.flagged{color:var(--flagged);background:rgba(91,157,240,.12)}
.badge.valid{color:var(--valid);background:rgba(82,185,138,.12)}
.badge.invalid_structure{color:var(--invalid);background:rgba(229,96,79,.12)}

.foot{text-align:center;color:var(--faint);font-size:11.5px;padding:26px 20px 30px;border-top:1px solid var(--line-soft);margin-top:18px}

@media (max-width:680px){
  .stats{grid-template-columns:repeat(2,1fr)}
  .go{margin-left:0;width:100%;justify-content:center}
  .occ-loc{min-width:0}
}
@media (prefers-reduced-motion:reduce){.results{animation:none}.toggle .knob{transition:none}}

/* ---- redesign: brand lockup + nav hierarchy ---- */
.brand{cursor:pointer;display:flex;align-items:baseline;gap:8px}
.brand-by{font-size:11px;color:var(--faint);font-weight:400;letter-spacing:.01em}
@media (max-width:720px){.brand-by{display:none}}
.nav{display:flex;align-items:center;gap:4px;flex-wrap:wrap}
.nav-div{width:1px;height:18px;background:var(--line);margin:0 6px}
.nav-b{display:inline-flex;align-items:center;gap:5px}
.nav-b.primary{color:var(--text);font-weight:600}
.nav-b.primary.on{background:var(--brand);color:#1a1205;box-shadow:none}
.who{color:var(--muted);font-size:12px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.gate{display:flex;flex-direction:column;align-items:center;text-align:center;gap:9px;padding:52px 24px}
.gate-ic{color:var(--muted)}
.gate-t{font-size:15px;font-weight:600;color:var(--text)}
.gate-d{max-width:460px;color:var(--muted);font-size:12.5px;line-height:1.55}
.gate-btn{margin-top:10px;display:inline-flex;align-items:center;gap:6px;background:var(--brand);color:#1a1205;border:0;border-radius:8px;padding:9px 17px;font:inherit;font-weight:600;font-size:13px;cursor:pointer}
.gate-btn:hover{filter:brightness(1.06)}
.nav-b.primary:not(.on){box-shadow:0 0 0 1px var(--line)}

/* ---- hero ---- */
.hero{display:grid;grid-template-columns:1fr 280px;gap:28px;align-items:start;background:linear-gradient(135deg,rgba(243,169,59,.05),rgba(91,157,240,.03));border:1px solid var(--line);border-radius:16px;padding:32px 30px;margin-bottom:18px}
@media (max-width:820px){.hero{grid-template-columns:1fr;gap:18px;padding:24px 20px}}
.hero-h{font-size:30px;line-height:1.18;margin:0 0 14px;font-weight:700;letter-spacing:-.01em}
@media (max-width:820px){.hero-h{font-size:23px}}
.hero-p{font-size:14.5px;line-height:1.6;color:var(--muted);margin:0 0 20px;max-width:56ch}
.hero-cta{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:18px}
.hero-go{font-size:14px;padding:11px 20px}
.ghost-link{background:transparent;border:0;color:var(--brand);font:inherit;font-size:13.5px;cursor:pointer;display:inline-flex;align-items:center;gap:5px;padding:6px 4px}
.ghost-link:hover{text-decoration:underline}
.hero-meta{display:flex;gap:18px;flex-wrap:wrap}
.hero-meta span{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--faint)}
.hero-meta svg{color:var(--valid)}
.hero-side{list-style:none;margin:0;padding:18px;background:var(--panel);border:1px solid var(--line);border-radius:12px;font-size:12.5px}
.hero-side li{padding:7px 0;border-bottom:1px solid var(--line-soft);color:var(--muted);line-height:1.4}
.hero-side li:last-child{border-bottom:0}
.hs-k{display:inline-block;min-width:62px;color:var(--text);font-size:11px}
.hs-note{color:var(--faint);font-size:11.5px;padding-top:12px!important}

/* ---- capabilities strip ---- */
.caps{margin-top:26px;border-top:1px solid var(--line);padding-top:22px}
.caps-h{font-size:12px;color:var(--faint);text-transform:uppercase;letter-spacing:.08em;margin-bottom:14px}
.caps-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
@media (max-width:900px){.caps-grid{grid-template-columns:repeat(2,1fr)}}
@media (max-width:520px){.caps-grid{grid-template-columns:1fr}}
.cap{text-align:left;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;cursor:pointer;display:flex;flex-direction:column;gap:7px;transition:border-color .12s,transform .12s}
.cap:hover{border-color:var(--brand);transform:translateY(-2px)}
.cap-ic{color:var(--brand)}
.cap-t{font-size:14px;font-weight:600;color:var(--text)}
.cap-d{font-size:11.5px;color:var(--muted);line-height:1.45;flex:1}
.cap-go{font-size:11.5px;color:var(--brand);display:inline-flex;align-items:center;gap:4px;margin-top:2px}
.caps-extra{margin-top:16px;font-size:12px;color:var(--muted);line-height:1.6;background:var(--panel);border:1px solid var(--line-soft);border-radius:10px;padding:14px 16px}
.caps-extra strong{color:var(--text);font-weight:600}
.inline-link{background:transparent;border:0;color:var(--brand);font:inherit;font-size:inherit;cursor:pointer;padding:0;text-decoration:underline}

/* ---- documentation ---- */
.docs{display:grid;grid-template-columns:210px 1fr;gap:30px;align-items:start}
@media (max-width:820px){.docs{grid-template-columns:1fr}}
.doc-nav{position:sticky;top:18px;align-self:start;display:flex;flex-direction:column;gap:1px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:10px;max-height:calc(100vh - 40px);overflow:auto}
@media (max-width:820px){.doc-nav{position:relative;top:0;max-height:none;flex-direction:row;flex-wrap:wrap}}
.doc-nav-h{font-size:11px;color:var(--faint);text-transform:uppercase;letter-spacing:.08em;padding:6px 8px 8px}
@media (max-width:820px){.doc-nav-h{width:100%}}
.doc-nav-b{text-align:left;background:transparent;border:0;color:var(--muted);font:inherit;font-size:12.5px;padding:7px 10px;border-radius:7px;cursor:pointer}
.doc-nav-b:hover{color:var(--text);background:var(--raised)}
.doc-nav-b.on{color:var(--text);background:var(--raised);box-shadow:inset 2px 0 0 var(--brand)}
.doc-body{max-width:760px;font-size:14px;line-height:1.65}
.doc-h{font-size:20px;font-weight:650;margin:38px 0 14px;padding-top:6px;scroll-margin-top:18px;border-top:1px solid var(--line-soft)}
.doc-body > .doc-h:first-child{margin-top:0;border-top:0;padding-top:0}
.doc-body p{color:var(--muted);margin:0 0 14px}
.doc-body em{color:var(--text);font-style:italic}
.doc-body strong{color:var(--text);font-weight:600}
.doc-step{padding-left:14px;border-left:2px solid var(--line)}
.doc-ol,.doc-list{color:var(--muted);margin:0 0 16px;padding-left:22px;line-height:1.6}
.doc-ol li,.doc-list li{margin-bottom:8px}
.doc-list{list-style:none;padding-left:0}
.doc-list li{padding-left:18px;position:relative}
.doc-list li:before{content:"";position:absolute;left:2px;top:9px;width:6px;height:6px;border-radius:2px;background:var(--brand)}
.doc-note{font-size:12.5px;color:var(--faint)!important;font-style:italic}
.doc-ic{vertical-align:-2px;color:var(--brand)}
.doc-callout{background:rgba(91,157,240,.06);border:1px solid rgba(91,157,240,.2);border-radius:10px;padding:14px 16px;margin:0 0 18px;font-size:13px;color:var(--muted);line-height:1.55}
.doc-callout strong{color:var(--text)}
.doc-worked{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;margin:0 0 16px}
.dw-h{font-size:13px;font-weight:600;color:var(--text);margin-bottom:10px}
.dw-t{border-collapse:collapse;font-size:12.5px;width:auto;margin-bottom:10px}
.dw-t th{color:var(--faint);font-weight:500;text-align:right;padding:3px 14px 6px 0;font-size:11px}
.dw-t td{text-align:right;padding:2px 14px 2px 0;color:var(--muted);font-variant-numeric:tabular-nums}
.dw-t td.mono{text-align:left;color:var(--text)}
.dw-calc{font-size:13px;color:var(--text);padding-top:8px;border-top:1px solid var(--line-soft)}
.doc-table{width:100%;border-collapse:collapse;font-size:12.5px;margin:0 0 18px}
.doc-table th{text-align:left;color:var(--muted);font-weight:600;font-size:11.5px;padding:8px 10px;border-bottom:1px solid var(--line)}
.doc-table td{padding:8px 10px;border-bottom:1px solid var(--line-soft);color:var(--muted);vertical-align:top}
.doc-table tr:last-child td{border-bottom:0}
.doc-table.api td.api-m{color:var(--valid);font-size:11px}
.doc-foot{margin-top:32px;padding-top:16px;border-top:1px solid var(--line);font-size:11px;color:var(--faint);line-height:1.6}

/* ---- stowage visualization panel ---- */
.stow{margin-top:18px}
.stow-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;flex-wrap:wrap;margin-bottom:12px}
.stow-title{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.stow-badge{display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:600;color:var(--text);background:var(--raised);border:1px solid var(--line);border-radius:8px;padding:5px 11px}
.stow-badge svg{color:var(--brand)}
.stow-vessel{font-size:13px;color:var(--text);font-family:var(--mono)}
.stow-from{font-size:11.5px;color:var(--faint)}
.stow-counts{display:flex;gap:6px;flex-wrap:wrap}
.stow-chip{font-size:11px;color:var(--muted);background:var(--raised);border:1px solid var(--line-soft);border-radius:20px;padding:2px 9px;font-variant-numeric:tabular-nums}
.stow-chip.sc-reefer{color:var(--valid);border-color:rgba(82,185,138,.3)}
.stow-chip.sc-hazmat{color:var(--invalid);border-color:rgba(229,96,79,.3)}
.stow-chip.sc-undefined{color:var(--muted)}
.stow-chip.sc-empty{color:var(--faint)}
.stow-infer{display:flex;gap:9px;align-items:flex-start;background:rgba(243,169,59,.06);border:1px solid rgba(243,169,59,.25);border-radius:9px;padding:10px 13px;margin-bottom:12px;font-size:12px;color:var(--muted);line-height:1.5}
.stow-infer em{color:var(--text);font-style:italic}
.si-mark{flex:none;width:16px;height:16px;border-radius:4px;background:rgba(243,169,59,.18);color:var(--brand);font-size:11px;font-weight:700;display:grid;place-items:center;margin-top:1px}
.stow-bays{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:12px;border-bottom:1px solid var(--line);padding-bottom:10px}
.stow-bay-b{background:transparent;border:1px solid var(--line);color:var(--muted);font:inherit;font-size:12px;padding:4px 11px;border-radius:7px;cursor:pointer}
.stow-bay-b:hover{color:var(--text);border-color:var(--brand)}
.stow-bay-b.on{background:var(--brand);color:#1a1205;border-color:var(--brand);font-weight:600}
.stow-canvas{background:#fbfbfc;border:1px solid var(--line);border-radius:10px;padding:10px;overflow:auto;text-align:center}
.stow-canvas svg{max-width:100%;height:auto}
.stow-note{font-size:11.5px;color:var(--faint);margin-top:10px;line-height:1.5}
.stow-unplaced{font-size:12px;color:var(--muted);margin-top:10px;background:rgba(229,96,79,.06);border:1px solid rgba(229,96,79,.2);border-radius:8px;padding:10px 13px;line-height:1.5}
.stow-unplaced strong{color:var(--invalid)}
.stow-foot{font-size:11px;color:var(--faint);margin-top:12px;line-height:1.55;border-top:1px solid var(--line-soft);padding-top:10px}
`;
