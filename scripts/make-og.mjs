// Renders public/og.png (1200x630), the social-share card. Run: node scripts/make-og.mjs
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import sharp from "sharp";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <rect width="1200" height="630" fill="#ffffff"/>
  <rect width="1200" height="12" fill="#1459b8"/>
  <g opacity="0.05" transform="translate(720,190)">
    <rect x="0" y="0" width="420" height="290" rx="26" fill="none" stroke="#1459b8" stroke-width="18"/>
    <rect x="300" y="0" width="120" height="290" rx="26" fill="#1459b8" fill-opacity="0.5" stroke="#1459b8" stroke-width="18"/>
  </g>
  <g transform="translate(90,86)">
    <rect x="0" y="2" width="46" height="32" rx="6" fill="none" stroke="#1459b8" stroke-width="4"/>
    <rect x="32" y="2" width="14" height="32" rx="6" fill="#1459b8" fill-opacity="0.2" stroke="#1459b8" stroke-width="4"/>
    <text x="64" y="28" font-family="Arial, Helvetica, sans-serif" font-size="30" font-weight="700" fill="#172b3a">Checkdigit</text>
  </g>
  <text x="92" y="240" font-family="Arial, Helvetica, sans-serif" font-size="24" letter-spacing="6" fill="#526171">ISO 6346 · ILU · UIC</text>
  <text x="88" y="322" font-family="Arial, Helvetica, sans-serif" font-size="74" font-weight="800" fill="#172b3a">Container Check Digit</text>
  <text x="88" y="404" font-family="Arial, Helvetica, sans-serif" font-size="74" font-weight="800" fill="#172b3a">Calculator</text>
  <text x="90" y="466" font-family="Arial, Helvetica, sans-serif" font-size="29" fill="#526171">Calculate a missing digit or check a complete number, in your browser.</text>
  <g transform="translate(90,516)">
    <rect x="0" y="0" width="392" height="64" rx="12" fill="#f4f6f8" stroke="#d8e0e7" stroke-width="2"/>
    <text x="26" y="43" font-family="'Courier New', Courier, monospace" font-size="32" font-weight="700" fill="#172b3a">CSQU 305438</text>
    <rect x="300" y="12" width="46" height="40" rx="7" fill="#ffffff" stroke="#1459b8" stroke-width="3"/>
    <text x="311" y="43" font-family="'Courier New', Courier, monospace" font-size="32" font-weight="700" fill="#0f478f">3</text>
  </g>
  <text x="1108" y="588" text-anchor="end" font-family="Arial, Helvetica, sans-serif" font-size="27" font-weight="700" fill="#1459b8">thecheckdigit.com</text>
</svg>`;

await sharp(Buffer.from(svg)).png().toFile(resolve(root, "public/og.png"));
console.log("wrote public/og.png");
