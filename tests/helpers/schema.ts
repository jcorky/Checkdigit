/*
 * A small JSON Schema checker covering the subset the contract schemas use:
 * type, required, properties, additionalProperties (false), enum, const,
 * pattern, minLength, minimum, items, $ref to #/$defs, and the project's
 * x-enum annotation (a reference into contracts/enums.json). It returns every
 * violation with its path so tests can assert on them.
 */

export interface SchemaDoc {
  $defs: Record<string, unknown>;
}

type Node = Record<string, unknown>;

export function validate(
  schema: SchemaDoc,
  node: Node,
  value: unknown,
  enums: Record<string, readonly string[] | unknown>,
  path = "$",
): string[] {
  const errors: string[] = [];
  const ref = node["$ref"];
  if (typeof ref === "string") {
    const m = /^#\/\$defs\/(.+)$/.exec(ref);
    if (!m) return [`${path}: unsupported $ref ${ref}`];
    const target = schema.$defs[m[1] as string];
    if (!target) return [`${path}: missing $def ${m[1]}`];
    return validate(schema, target as Node, value, enums, path);
  }

  const types = node["type"];
  if (types !== undefined) {
    const allowed = (Array.isArray(types) ? types : [types]) as string[];
    if (!allowed.some((t) => matchesType(t, value))) {
      errors.push(`${path}: expected ${allowed.join("|")}, got ${describe(value)}`);
      return errors;
    }
  }
  if (node["const"] !== undefined && value !== node["const"]) {
    errors.push(`${path}: must equal ${JSON.stringify(node["const"])}`);
  }
  if (Array.isArray(node["enum"]) && !(node["enum"] as unknown[]).includes(value)) {
    errors.push(`${path}: ${JSON.stringify(value)} not in enum`);
  }
  const xEnum = node["x-enum"];
  if (typeof xEnum === "string") {
    const values = enums[xEnum];
    if (!Array.isArray(values)) errors.push(`${path}: x-enum ${xEnum} is not defined in enums.json`);
    else if (!(values as string[]).includes(value as string)) errors.push(`${path}: ${JSON.stringify(value)} not in enum ${xEnum}`);
  }
  if (typeof value === "string") {
    if (typeof node["pattern"] === "string" && !new RegExp(node["pattern"] as string).test(value)) {
      errors.push(`${path}: does not match ${node["pattern"]}`);
    }
    if (typeof node["minLength"] === "number" && value.length < (node["minLength"] as number)) {
      errors.push(`${path}: shorter than ${node["minLength"]}`);
    }
  }
  if (typeof value === "number" && typeof node["minimum"] === "number" && value < (node["minimum"] as number)) {
    errors.push(`${path}: below minimum ${node["minimum"]}`);
  }
  if (Array.isArray(value) && node["items"]) {
    value.forEach((item, i) => errors.push(...validate(schema, node["items"] as Node, item, enums, `${path}[${i}]`)));
  }
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const obj = value as Record<string, unknown>;
    for (const key of (node["required"] as string[] | undefined) ?? []) {
      if (!(key in obj)) errors.push(`${path}: missing required ${key}`);
    }
    const props = (node["properties"] as Record<string, Node> | undefined) ?? {};
    for (const [key, sub] of Object.entries(props)) {
      if (key in obj) errors.push(...validate(schema, sub, obj[key], enums, `${path}.${key}`));
    }
    const extra = node["additionalProperties"];
    if (extra === false) {
      for (const key of Object.keys(obj)) if (!(key in props)) errors.push(`${path}: unexpected property ${key}`);
    } else if (extra && typeof extra === "object") {
      for (const key of Object.keys(obj)) {
        if (!(key in props)) errors.push(...validate(schema, extra as Node, obj[key], enums, `${path}.${key}`));
      }
    }
  }
  return errors;
}

function matchesType(t: string, v: unknown): boolean {
  switch (t) {
    case "object":
      return v !== null && typeof v === "object" && !Array.isArray(v);
    case "array":
      return Array.isArray(v);
    case "string":
      return typeof v === "string";
    case "integer":
      return typeof v === "number" && Number.isInteger(v);
    case "number":
      return typeof v === "number";
    case "boolean":
      return typeof v === "boolean";
    case "null":
      return v === null;
    default:
      return false;
  }
}

function describe(v: unknown): string {
  if (v === null) return "null";
  if (Array.isArray(v)) return "array";
  return typeof v;
}
