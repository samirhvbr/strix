// Slim, dependency-free extract of the platform's display-number helper. The
// full version queries a database to compute org-wide finding numbers; the local
// viewer only ever needs the pure formatter (a local run has no org context).
export function formatFindingId(num: number): string {
  return `SHVIA-${num}`;
}

/** Format an integer with locale thousands separators (e.g. 68339486 -> "68,339,486"). */
export function formatNumber(num: number): string {
  return new Intl.NumberFormat("en-US").format(num);
}
