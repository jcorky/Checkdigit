/*
 * Byte <-> text conversion that mirrors checkdigit/service.py: decode as strict
 * UTF-8, otherwise as ISO-8859-1 (EDI is frequently latin-1), and re-encode the
 * corrected text with the same codec so output bytes stay faithful. A UTF-8
 * byte order mark is kept in the text (Python's "utf-8" codec keeps it too).
 *
 * "latin1" is implemented by hand: the browser's TextDecoder maps that label to
 * windows-1252, which would change bytes 0x80-0x9F on the way back out.
 */

export type Codec = "utf-8" | "latin-1";

export function decodeBytes(bytes: Uint8Array): { text: string; encoding: Codec } {
  try {
    const text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
    return { text, encoding: "utf-8" };
  } catch {
    let text = "";
    const chunk = 8192;
    for (let i = 0; i < bytes.length; i += chunk) {
      text += String.fromCharCode(...bytes.subarray(i, i + chunk));
    }
    return { text, encoding: "latin-1" };
  }
}

export function encodeText(text: string, encoding: Codec): Uint8Array {
  if (encoding === "utf-8") return new TextEncoder().encode(text);
  const out = new Uint8Array(text.length);
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i);
    if (code > 0xff) throw new Error(`character U+${code.toString(16).toUpperCase()} cannot be encoded as latin-1`);
    out[i] = code;
  }
  return out;
}

export async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes as BufferSource);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function sha256Text(text: string): Promise<string> {
  return sha256Hex(new TextEncoder().encode(text));
}
