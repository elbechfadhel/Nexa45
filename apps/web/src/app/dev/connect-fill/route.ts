/** Aide DEV (tests manuels V2b.1) : valeurs de connexion d'une instance Odoo LOCALE de test, pour pré-remplir
 *  « Connect a system ». N'existe qu'avec `next dev` (404 sinon) et seulement pour une requête locale.
 *  Les valeurs viennent de variables SERVEUR non publiques (`UIE_DEV_ODOO_*`, dans apps/web/.env.local,
 *  ignoré par git) : jamais dans le code, jamais dans le bundle JavaScript, jamais lues depuis le SecretStore
 *  ni l'API UIE. Sans configuration complète : `{ configured: false }`. */

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);
const NO_STORE = { "Cache-Control": "no-store" };

export function GET(request: Request) {
  if (process.env.NODE_ENV !== "development") return new Response(null, { status: 404 });
  if (!LOCAL_HOSTS.has(new URL(request.url).hostname)) return new Response(null, { status: 404 });

  const values = {
    name: process.env.UIE_DEV_ODOO_NAME || "customer17",
    url: process.env.UIE_DEV_ODOO_URL,
    database: process.env.UIE_DEV_ODOO_DB,
    username: process.env.UIE_DEV_ODOO_USERNAME,
    password: process.env.UIE_DEV_ODOO_PASSWORD,
  };
  if (!values.url || !values.database || !values.username || !values.password) {
    return Response.json({ configured: false }, { headers: NO_STORE });
  }
  // `system` : le système auquel ces valeurs s'appliquent (le formulaire ne les propose que pour lui)
  return Response.json({ configured: true, system: "odoo", ...values }, { headers: NO_STORE });
}
