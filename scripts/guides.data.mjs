// Content for the guide pages under /guides. Authored here as one source of
// truth; scripts/gen-guides.mjs stamps out src/guides/<slug>.html from it.
// Facts mirror the reference library (src/pages/reference.ts); prose is written
// fresh so the guides do not duplicate the in-app reference verbatim.

export const REVIEWED = "2026-09-18";

export const guides = [
  {
    slug: "iso-6346",
    title: "ISO 6346 container numbers explained",
    description:
      "ISO 6346 explained: the owner code, equipment category, serial number and check digit that make up a shipping container number, with a worked check-digit example.",
    summary: "The owner code, category, serial and check digit that make up a container number.",
    lead:
      "ISO 6346 is the international standard for coding, identifying and marking intermodal (shipping) containers. It defines the container number stencilled on the box, the size and type code beside it, and the operational markings. It is the format the calculator on this site checks.",
    body: `
      <h2>The four parts of a container number</h2>
      <p>A container number under ISO 6346 is eleven characters — for example <span class="id">CSQU3054383</span> — made of four parts:</p>
      <table>
        <thead><tr><th>Part</th><th>Length</th><th>Example</th><th>Meaning</th></tr></thead>
        <tbody>
          <tr><td>Owner code</td><td>3 letters</td><td class="id">CSQ</td><td>The owner or principal operator, registered as a <a href="/guides/bic-code">BIC code</a></td></tr>
          <tr><td>Equipment category</td><td>1 letter</td><td class="id">U</td><td>U for freight containers, J for detachable container-related equipment, Z for trailers and chassis</td></tr>
          <tr><td>Serial number</td><td>6 digits</td><td class="id">305438</td><td>Chosen by the owner</td></tr>
          <tr><td>Check digit</td><td>1 digit</td><td class="id">3</td><td>Calculated from the first ten characters</td></tr>
        </tbody>
      </table>
      <p>The prefix holder, the owner, a lessor, the operator and the carrier moving the box today can all be different parties. A refrigerated container is still category U — refrigeration is described by the size/type code, not the category letter.</p>

      <h2>How the ISO 6346 check digit is calculated</h2>
      <p>The check digit is a mod-11 self-check over the first ten characters. Each character is given a value: digits keep their own value, and letters run from A = 10 to Z = 38, skipping every multiple of 11 (so 11, 22 and 33 are never used, and no letter value collides with the modulus). Each value is multiplied by a weight — the powers of two from 1 to 512, left to right — the ten results are added, and the remainder after dividing by 11 is the check digit. A remainder of 10 is written as 0.</p>
      <div class="worked">
        <p>Worked example — <span class="id">CSQU305438</span>: the letters are C = 13, S = 30, Q = 28, U = 32. The weighted sum C·1 + S·2 + Q·4 + U·8 + 3·16 + 0·32 + 5·64 + 4·128 + 3·256 + 8·512 is 6185, and 6185 mod 11 is 3, so the check digit is 3 and the full number is <span class="id">CSQU3054383</span>. <a href="/check#CSQU3054383">Open this example in the checker</a>.</p>
      </div>

      <h2>The size and type code</h2>
      <p>Next to the container number you will usually see a four-character size/type code such as 22G1 or 45R1. It describes the container's dimensions and kind and, unlike the number, has no check digit. See <a href="/guides/container-size-type-codes">container size and type codes</a> for how to read it.</p>

      <h2>What a valid ISO 6346 number does not prove</h2>
      <p>A correct check digit confirms only that the printed digit agrees with the first ten characters under the standard's arithmetic. It does not prove that the container exists, who owns or operates it, where it is, or that anyone may release it — and it cannot catch every mistake, because <a href="/guides/container-number-errors">some errors are invisible to the check digit</a>.</p>`,
    related: ["container-size-type-codes", "bic-code", "container-number-errors"],
  },

  {
    slug: "container-size-type-codes",
    title: "Container size and type codes (ISO 6346)",
    description:
      "What container size and type codes such as 22G1, 42G1 and 45R1 mean: the length, height, width and type characters — and why the size/type code has no check digit.",
    summary: "How to read the 22G1-style size/type code stencilled beside the number.",
    lead:
      "The four-character size/type code stencilled near a container number — for example 22G1 or 45R1 — describes the equipment's dimensions and type. Under ISO 6346 it is purely descriptive: unlike the container number itself, the size/type code has no check digit.",
    body: `
      <h2>How to read a size/type code</h2>
      <p>The four characters split into three fields:</p>
      <ul>
        <li><b>Character 1 — length.</b> 2 is 20 ft, 4 is 40 ft and L is 45 ft. Length code 5 is unassigned, so a 45 ft unit uses L rather than 5.</li>
        <li><b>Character 2 — height and width.</b> It encodes the container's height together with whether it is standard or extra width; for example a value here distinguishes a standard 8 ft 6 in box from a 9 ft 6 in high cube.</li>
        <li><b>Characters 3 and 4 — type.</b> A letter and a digit naming the kind of container and a variant, such as G for a general-purpose box or R for a refrigerated (reefer) one.</li>
      </ul>

      <h2>Common size/type codes</h2>
      <table>
        <thead><tr><th>Code</th><th>Means</th></tr></thead>
        <tbody>
          <tr><td class="id">22G1</td><td>20 ft standard-height general-purpose container</td></tr>
          <tr><td class="id">42G1</td><td>40 ft standard-height general-purpose container</td></tr>
          <tr><td class="id">45G1</td><td>40 ft high-cube general-purpose container</td></tr>
          <tr><td class="id">45R1</td><td>40 ft high-cube refrigerated (reefer) container</td></tr>
          <tr><td class="id">22R1</td><td>20 ft refrigerated container</td></tr>
        </tbody>
      </table>
      <p>Type groups you will meet include G (general purpose), R and H (thermal/reefer), U (open top), T (tank), P (platform and flat rack) and S (named cargo). The exact type digit depends on the variant.</p>

      <h2>Decode a specific code</h2>
      <p>The <a href="/reference">reference library</a> has an interactive size/type decoder that resolves a code to its length, height, width, type and TEU, and marks anything unknown as undefined rather than guessing.</p>

      <h2>Why the size/type code has no check digit</h2>
      <p>The size/type code is descriptive metadata, not an identifier, so it is never a check-digit target and is never altered when Checkdigit corrects a file. Only the <a href="/guides/iso-6346">container number</a> carries a check digit.</p>`,
    related: ["iso-6346", "bic-code", "ilu-code"],
  },

  {
    slug: "ilu-code",
    title: "ILU codes (EN 13044) explained",
    description:
      "ILU codes under EN 13044 identify intermodal loading units — four letters, six digits and a check digit — and are described as compatible with the BIC/ISO 6346 container code.",
    summary: "Intermodal loading unit codes under EN 13044, and their tie to ISO 6346.",
    lead:
      "An ILU code identifies an intermodal loading unit — a swap body, certain semi-trailers or a container used in European intermodal transport — under the standard EN 13044. It looks like a container number and is checked with the same arithmetic.",
    body: `
      <h2>The format of an ILU code</h2>
      <p>An ILU code is four letters, six digits and one check digit. The fourth letter is the category and is A, B, D, E or K — distinguishing intermodal loading units from ISO 6346 containers, which use U, J or Z. The owner is identified by the first three letters, registered in the ILU register operated by UIRR.</p>

      <h2>Relationship to the ISO 6346 container code</h2>
      <p>UIRR, which manages the ILU register, describes the ILU code as fully compatible with the BIC code used for maritime containers under <a href="/guides/iso-6346">ISO 6346</a>, and refers to a defined procedure for the check digit. Checkdigit routes categories A, B, D, E and K to the ILU set and checks them with the same mod-11 arithmetic as ISO 6346.</p>
      <p>That arithmetic has not been independently verified here against the normative EN 13044-1 text, so this is a statement of compatibility, not a claim of normative compliance. The <a href="/">calculator</a> labels ILU results accordingly.</p>

      <h2>Where you will see ILU codes</h2>
      <p>ILU codes appear on swap bodies and intermodal units in European rail and combined transport, alongside container numbers and, on rail vehicles, <a href="/guides/uic-wagon-number">UIC wagon numbers</a>.</p>`,
    related: ["iso-6346", "uic-wagon-number", "container-size-type-codes"],
  },

  {
    slug: "uic-wagon-number",
    title: "UIC wagon numbers and the Luhn check digit",
    description:
      "UIC wagon numbers are twelve digits with a Luhn (mod-10) check digit over the first eleven. How the number and its self-check work, and how they differ from a container number.",
    summary: "Twelve-digit rail vehicle numbers with a Luhn check, not the container mod-11.",
    lead:
      "A UIC wagon number is the twelve-digit identifier carried by a European rail vehicle. Its last digit is a check digit, but it is computed differently from a container number — with the Luhn (mod-10) algorithm rather than the ISO 6346 mod-11.",
    body: `
      <h2>How the UIC check digit works</h2>
      <p>The check digit is the twelfth digit, computed by the Luhn algorithm over the first eleven digits. Reading from the right, digits are weighted 2, 1, 2, 1 and so on; any product of ten or more has its two digits added together; the products are summed; and the check digit is (10 − sum mod 10) mod 10.</p>
      <div class="worked">
        <p>Worked example — the first eleven digits <span class="id">21 81 2471 217</span> give a Luhn check digit of 3, so the complete number ends 217-3. <a href="/check#uic:218124712173">Open a UIC example in the checker</a>.</p>
      </div>

      <h2>How it differs from a container number</h2>
      <p>A <a href="/guides/iso-6346">container number</a> uses a mod-11 check over four letters and six digits; a UIC wagon number uses Luhn mod-10 over eleven digits. The two are never cross-applied. A bare twelve-digit run is also not self-identifying, so Checkdigit only treats it as a UIC number where the context (a rail-vehicle field, or the UIC option in the calculator) says so.</p>

      <h2>A note on other rail schemes</h2>
      <p>North American railcar reporting marks are a different identification scheme with their own rules and are not handled here.</p>`,
    related: ["iso-6346", "ilu-code", "container-number-errors"],
  },

  {
    slug: "bic-code",
    title: "BIC codes and container owner prefixes",
    description:
      "The BIC code is the three-letter owner prefix at the start of a container number, registered with the Bureau International des Containers. What it is and how to look one up.",
    summary: "The registered three-letter owner prefix that starts every container number.",
    lead:
      "The first three letters of a container number are the owner code, or BIC code — the registered prefix that identifies the container's owner or principal operator. It is issued by the Bureau International des Containers (BIC).",
    body: `
      <h2>What a BIC code identifies</h2>
      <p>A BIC code is a three-letter owner prefix. Together with the equipment category letter it forms the first four characters of an <a href="/guides/iso-6346">ISO 6346 container number</a>. One company can hold several prefixes, and the party that owns a prefix may differ from the lessor, the operator or the carrier currently moving the container.</p>

      <h2>Looking up an owner</h2>
      <p>Owner prefixes are searchable in BIC's own register. A registered prefix tells you who holds the code — not whether a particular serial number exists or is in service.</p>
      <ul>
        <li>Search the owner register: <a href="https://www.bic-code.org/bic-codes/">bic-code.org</a></li>
        <li>Technical equipment data (BoxTech): <a href="https://www.bic-boxtech.org/">bic-boxtech.org</a></li>
      </ul>
      <p>Checkdigit validates a number's format and check digit but does not look owners up automatically, and never fabricates a registry result. Owner lookup is an explicit link to BIC.</p>

      <h2>BIC code versus check digit</h2>
      <p>A valid check digit and a registered BIC code answer different questions. The check digit confirms the number is internally consistent; the BIC register confirms the prefix belongs to a real owner. Neither proves the specific container exists or where it is.</p>`,
    related: ["iso-6346", "container-number-errors", "container-size-type-codes"],
  },

  {
    slug: "container-number-errors",
    title: "Container number errors the check digit catches — and misses",
    description:
      "The container check digit catches most typos and transpositions, but not all. The errors it detects, the blind spot it has, and why O and 0 are never swapped automatically.",
    summary: "What the check digit detects, the errors it can't see, and the O-versus-0 trap.",
    lead:
      "A container check digit is a good guard against mistakes made when a number is written down, keyed or transmitted — but it is not perfect. Knowing what it catches, and the one class of error it cannot see, helps you trust a passing result appropriately.",
    body: `
      <h2>What the check digit catches</h2>
      <p>The ISO 6346 mod-11 check digit detects every single-character error — one wrong letter or digit anywhere in the first ten characters — and most transpositions, where two adjacent characters are swapped. That covers the large majority of real keying and transcription mistakes.</p>

      <h2>The blind spot</h2>
      <p>The check is blind to a substitution whose two characters have values that differ by a multiple of 11, because both contribute the same amount modulo 11. The classic pair is the digit 1 (value 1) and the letter B (value 12): swapping one for the other leaves the check digit unchanged. A transposition inside the serial can also survive the check. When the first ten characters are wrong but still produce the printed check digit, only comparison against another source — a booking, a load list, the physical box — will reveal it.</p>

      <h2>Why O and 0 are never swapped for you</h2>
      <p>The most common real-world errors are the look-alikes: the letter O read as the digit 0, the letter I as the digit 1, or the letter B as the digit 8. Checkdigit never substitutes one for the other on its own. If a serial contains a letter where a digit belongs, it says so and keeps your original entry, rather than quietly "fixing" it into a different container number.</p>

      <h2>How Checkdigit reports a failure</h2>
      <p>When a number fails, the <a href="/">calculator</a> shows the expected check digit and the number it would form if the first ten characters are correct — but it never assumes which end is wrong. Verify the whole number against your source, because the error may be in the body rather than the check digit.</p>`,
    related: ["iso-6346", "bic-code", "uic-wagon-number"],
  },
];
