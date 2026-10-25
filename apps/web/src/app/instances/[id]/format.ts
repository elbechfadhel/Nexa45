/** Mise en forme des libellés renvoyés par l'API (aucune donnée inventée). */

export const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** « odoo 17 @1.0.0 » → « Odoo 17 (1.0.0) » */
export function formatBaseline(label: string | null | undefined): string {
  if (!label) return "—";
  const [name, version] = label.split(" @");
  return version ? `${capitalize(name)} (${version})` : capitalize(label);
}

const TRANSPORT: Record<string, string> = { xmlrpc: "XML-RPC", jsonrpc: "JSON-RPC", rest: "REST", odata: "OData" };

/** interface « orm » + connecteur « odoo.xmlrpc » → « ORM (XML-RPC) » */
export function formatInterface(iface: string, connectorType?: string | null): string {
  const transport = connectorType?.split(".").pop();
  const t = transport ? (TRANSPORT[transport] ?? transport.toUpperCase()) : undefined;
  return t ? `${iface.toUpperCase()} (${t})` : iface.toUpperCase();
}
