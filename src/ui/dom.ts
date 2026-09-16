/** Small DOM helpers shared by the pages. No framework, no dependencies. */

export function esc(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function byId<T extends HTMLElement = HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing element #${id}`);
  return el as T;
}

/** Tagged template that escapes interpolated values; use `raw()` to pass through markup. */
export function html(strings: TemplateStringsArray, ...values: unknown[]): string {
  let out = "";
  strings.forEach((s, i) => {
    out += s;
    if (i < values.length) {
      const v = values[i];
      out += v instanceof Raw ? v.markup : esc(String(v ?? ""));
    }
  });
  return out;
}

class Raw {
  constructor(public readonly markup: string) {}
}

export const raw = (markup: string): Raw => new Raw(markup);

/** Human label and CSS class for a kernel status value. */
export const STATUS_LABEL: Record<string, string> = {
  valid: "Valid",
  corrected: "Corrected",
  flagged: "Flagged",
  not_a_target: "Not a target",
  invalid_structure: "Invalid structure",
};

export function badge(status: string): string {
  const label = STATUS_LABEL[status] ?? status;
  return html`<span class="badge ${status}">${label}</span>`;
}
