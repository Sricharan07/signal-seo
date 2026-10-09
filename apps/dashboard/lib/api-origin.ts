export function validatedSignalApiBaseUrl(value: string): URL {
  const parsed = new URL(value);
  if (
    !["http:", "https:"].includes(parsed.protocol) ||
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash ||
    !["", "/"].includes(parsed.pathname)
  ) {
    throw new Error("Invalid Signal API base URL");
  }
  return parsed;
}
