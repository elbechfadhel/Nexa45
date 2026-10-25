// @ts-check
/**
 * Catalogue des use cases : SOURCE DE VÉRITÉ UNIQUE des pages Overview (admin `/overview`, fournisseur `/portal`).
 *
 * Règle du projet : tout use case visible par un utilisateur, nouveau ou modifié de manière notable, met à jour SA
 * définition ici. Les deux Overview sont générées depuis ces définitions (filtrées par audience) et ne tiennent
 * jamais de liste de workflows en dur. Ce fichier est une couche de PRÉSENTATION / navigation : aucune logique
 * métier, aucune intégration n'est exécutée d'ici.
 *
 * JavaScript pur (JSDoc + @ts-check) : vérifié par `pnpm typecheck`, exécutable tel quel par `node --test`
 * (`catalog.test.mjs`, lancé aussi par pytest). Les tests refusent une page de l'application qui n'est couverte
 * par aucun use case (voir `routes` et UNTRACKED_ROUTES).
 */

/** @typedef {"admin" | "supplier"} Audience */
/** @typedef {"available" | "coming_soon" | "experimental" | "deprecated"} UseCaseStatus */
/** @typedef {"registry" | "instances" | "organizations" | "product" | "partner" | "accounting" | "migration"} UseCaseDomain */
/** @typedef {"odoo" | "datev" | "lexware" | "file" | "go45"} SystemCode   système concerné ; `file` = fichier du fournisseur ;
 *   `go45` = la plateforme elle-même (use case interne, aucun système externe) */

/**
 * @typedef {object} UseCaseDefinition
 * @property {string} id                 identifiant stable « domaine.action[.cible] »
 * @property {UseCaseDomain} domain
 * @property {Audience[]} audiences      qui peut RÉELLEMENT le faire dans l'application
 * @property {UseCaseStatus} status      available = workflow réellement utilisable de bout en bout
 * @property {string} section            section de l'Overview (SECTIONS)
 * @property {number} order              ordre dans la section
 * @property {string} title
 * @property {string} summary            une phrase
 * @property {string} goal
 * @property {string[]} flow             étapes du flux affichées « A → B → C »
 * @property {{ source: SystemCode[], target: SystemCode[] }} systems   systèmes RÉELLEMENT concernés (logos de
 *   la carte) : `source` seul = le use case porte sur ces systèmes ; `target` non vide = flux source → cible
 * @property {{ instance?: string, minutes?: number }} [demo]   métadonnées de démo (facultatives)
 * @property {string[]} steps            déroulé de la démo
 * @property {string[]} expected         résultat attendu
 * @property {string[]} prerequisites
 * @property {{ route: string, label: string }} [start]   point de départ existant le plus proche
 * @property {string[]} routes           pages de l'application qui portent ce use case (couverture)
 * @property {string} [technical_reference]                 section de la documentation (admin)
 */

/** @typedef {{ id: string, title: string, order: number, supplier_title?: string }} Section */

/** @type {Section[]} */
export const SECTIONS = [
  { id: "systems", title: "Systems & Registry", order: 10, supplier_title: "Your systems" },
  { id: "product", title: "Product catalog", order: 20 },
  { id: "partner", title: "Customers & suppliers", order: 30 },
  { id: "accounting", title: "Accounting", order: 40 },
  { id: "administration", title: "Administration", order: 50 },
];

/** @type {readonly Audience[]} */
export const AUDIENCES = ["admin", "supplier"];
/** Systèmes citables par un use case : code (logo `public/systems/<code>.png`) → nom affiché.
 * @type {Readonly<Record<SystemCode, string>>} */
export const SYSTEM_NAMES = {
  odoo: "Odoo",
  lexware: "Lexware Office",
  datev: "DATEV",
  file: "File",
  go45: "GO45",
};
/** @type {readonly UseCaseStatus[]} */
export const STATUSES = ["available", "coming_soon", "experimental", "deprecated"];

/** @type {UseCaseDefinition[]} */
export const USE_CASES = [
  // ------------------------------------------------------------------ console admin
  {
    id: "registry.explore",
    domain: "registry",
    audiences: ["admin"],
    status: "available",
    section: "systems",
    order: 10,
    title: "Explore supported systems",
    summary: "Browse what GO45 knows in advance about each supported system and format.",
    goal: "Show the System Knowledge Registry: systems, formats, versions, interfaces and their published schemas.",
    flow: ["System", "Format", "Version", "Interface", "Snapshot"],
    systems: { source: ["odoo", "datev", "lexware"], target: [] },
    demo: { instance: "Odoo 17 · DATEV Buchungsstapel 13 · Lexware Office public API v1", minutes: 2 },
    steps: [
      "Open Systems",
      "Pick Odoo, DATEV or Lexware Office",
      "Open a published snapshot",
      "Browse entities and fields (search, types, required)",
    ],
    expected: ["Schemas are read-only, versioned and immutable"],
    prerequisites: ["Registry imported (uie registry import)"],
    start: { route: "/systems", label: "Open systems" },
    routes: ["/systems", "/snapshots/[id]"],
    technical_reference: "CLAUDE.md · Schema Browser + Version Compare",
  },
  {
    id: "registry.compare",
    domain: "registry",
    audiences: ["admin"],
    status: "available",
    section: "systems",
    order: 20,
    title: "Compare versions",
    summary: "See exactly what changed between two versions of a system or format.",
    goal: "Show added, removed and changed fields between two versions, computed by the engine.",
    flow: ["Version A", "Compare", "Version B"],
    systems: { source: ["odoo", "datev"], target: [] },
    demo: { instance: "DATEV Buchungsstapel 12 → 13", minutes: 2 },
    steps: ["Open Version Compare", "Pick DATEV, format Buchungsstapel, versions 12 and 13", "Review the changes"],
    expected: ["Field 125 added between Buchungsstapel 12 and 13"],
    prerequisites: ["Both versions present in the Registry"],
    start: { route: "/compare?from=datev:buchungsstapel:12&to=datev:buchungsstapel:13", label: "Start demo" },
    routes: ["/compare"],
    technical_reference: "CLAUDE.md · DATEV D1",
  },
  {
    id: "instances.manage",
    domain: "instances",
    audiences: ["admin"],
    status: "available",
    section: "systems",
    order: 30,
    title: "Connect and inspect instances",
    summary: "Connect a real system, test the connection, discover its schema and compare it with the standard.",
    goal: "Show how GO45 learns a customer installation: custom fields, third-party modules, missing elements.",
    flow: ["Instance", "Test connection", "Discover", "Instance diff"],
    systems: { source: ["odoo"], target: [] },
    demo: { instance: "customer17 · Odoo 17", minutes: 3 },
    steps: ["Open Instances", "Open customer17", "Test connection", "Discover the schema", "Review the diff"],
    expected: ["Custom fields (x_…) and third-party extensions are classified"],
    prerequisites: ["Odoo instance reachable (Docker odoo17-customer)"],
    start: { route: "/instances", label: "Open instances" },
    routes: ["/instances", "/instances/[id]"],
    technical_reference: "CLAUDE.md · Instances client (étapes 7-8)",
  },
  {
    id: "product.analysis.admin",
    domain: "product",
    audiences: ["admin"],
    status: "available",
    section: "product",
    order: 10,
    title: "Analyze a product catalog (technical view)",
    summary: "See how the engine identifies catalog fields and which questions remain.",
    goal: "Show the Product Domain and Semantic Enrichment: rules first, AI optional, human answers last.",
    flow: ["Odoo instance", "Product domain", "Semantic enrichment", "Questions"],
    systems: { source: ["odoo"], target: [] },
    demo: { instance: "customer17 · Odoo 17", minutes: 4 },
    steps: [
      "Open Instances → customer17",
      "Open Product Catalog Analysis",
      "Review identified and excluded fields",
      "Answer the open questions",
      "Open Advanced for the technical domain view",
    ],
    expected: ["Only real ambiguities are asked", "Answers are kept for the next analyses"],
    prerequisites: ["customer17 discovered"],
    start: { route: "/instances", label: "Open instances" },
    routes: ["/instances/[id]/catalog", "/instances/[id]/product-domain"],
    technical_reference: "CLAUDE.md · Product Domain V1 / Semantic Enrichment V1b",
  },
  {
    id: "organizations.manage",
    domain: "organizations",
    audiences: ["admin"],
    status: "available",
    section: "administration",
    order: 10,
    title: "Manage suppliers",
    summary: "Create supplier organizations, their users and attach their systems.",
    goal: "Show multi-tenant isolation: each supplier only sees its own systems and data.",
    flow: ["Organization", "Users", "Systems"],
    systems: { source: ["go45"], target: [] },
    demo: { minutes: 2 },
    steps: ["Open Suppliers", "Open a supplier", "Add a user or attach an instance"],
    expected: ["The supplier sees only its own systems in the portal"],
    prerequisites: [],
    start: { route: "/admin/suppliers", label: "Open suppliers" },
    routes: ["/admin/suppliers", "/admin/suppliers/[id]"],
    technical_reference: "CLAUDE.md · Comptes et isolation multi-tenant (V1d)",
  },
  // ------------------------------------------------------------------ portail fournisseur
  {
    id: "systems.connect",
    domain: "instances",
    audiences: ["supplier"],
    status: "available",
    section: "systems",
    order: 40,
    title: "Connect my system",
    summary: "Connect one of your systems so GO45 can work with it.",
    goal: "The supplier connects a system (Odoo, Lexware Office) on their own: test, then save.",
    flow: ["Select system", "Connection details", "Test", "Save"],
    systems: { source: ["odoo", "lexware"], target: [] },
    demo: { instance: "customer17 · Odoo 17", minutes: 2 },
    steps: ["Open My systems", "Connect a system", "Choose a system, fill in the details", "Test connection", "Save"],
    expected: ["The detected version is shown", "Credentials are never displayed again"],
    prerequisites: ["Supplier account"],
    start: { route: "/portal/systems/connect", label: "Connect a system" },
    routes: ["/portal/systems/connect"],
    technical_reference: "CLAUDE.md · Connect my system (V2b.1)",
  },
  {
    id: "systems.review",
    domain: "instances",
    audiences: ["supplier"],
    status: "available",
    section: "systems",
    order: 50,
    title: "My systems",
    summary: "Review the systems already connected and their analysis status.",
    goal: "One place per system: connection, catalog analysis, customers & suppliers.",
    flow: ["My systems", "System"],
    systems: { source: ["odoo", "lexware"], target: [] },
    demo: { minutes: 1 },
    steps: ["Open My systems", "Open a system"],
    expected: ["Connection and analysis status at a glance"],
    prerequisites: ["A connected system"],
    start: { route: "/portal/systems", label: "Open my systems" },
    routes: ["/portal/systems", "/portal/systems/[systemId]"],
  },
  {
    id: "migration.wizard",
    domain: "migration",
    audiences: ["supplier"],
    status: "experimental",
    section: "systems",
    order: 90,
    title: "AI Migration Wizard",
    summary:
      "Add your Excel or CSV file, choose the target system, answer at most three preselected decisions and download the file prepared for it.",
    goal: "Your own data shaped for the target in minutes: Registry knowledge first, AI proposals clearly marked, nothing sent.",
    flow: ["Your file", "Target", "Summary", "Prepared file"],
    systems: { source: ["go45"], target: [] },
    demo: { instance: "any target system, a small .xlsx or .csv file", minutes: 5 },
    steps: [
      "Open the AI Migration Wizard (Simple mode by default: your file first)",
      "Drop your Excel or CSV file and choose the target: your connected systems first, then the Registry",
      "Follow the analysis: reading the file, analyzing the columns, checking against the target",
      "Read the summary: products ready, rows not imported with their reason, decisions already applied",
      "Answer at most three preselected decisions and confirm the uncertain columns (Confirm all)",
      "Download the prepared file and import it in the order shown (for example categories first)",
      "Expert mode (switch at the top): the full questionnaire, every field and status, templates",
    ],
    expected: [
      "At most three questions, each with its answer preselected; usual values applied and listed, changeable in one click",
      "Columns with the same name are matched for sure; AI matches are marked, uncertain ones must be confirmed",
      "Your values are copied as they are; the file is never stored and nothing is written to your systems",
      "A prepared file is never called ready to import when the target format is not officially verified",
    ],
    prerequisites: ["AI assistant configured on the server"],
    start: { route: "/portal/wizard", label: "Start" },
    routes: ["/portal/wizard"],
    technical_reference: "CLAUDE.md · AI Migration Wizard",
  },
  {
    id: "migration.wizard.datev",
    domain: "migration",
    audiences: ["supplier"],
    status: "experimental",
    section: "systems",
    order: 91,
    title: "Prepare data for DATEV",
    summary:
      "For tax advisors: add a customer list or invoices as Excel or CSV and download the file prepared for DATEV.",
    goal: "A DATEV import file from your client's own lists, with the same checks as the wizard and nothing sent.",
    flow: ["Your file", "DATEV format", "Summary", "DATEV file"],
    systems: { source: ["file"], target: ["datev"] },
    demo: { instance: "a customer list or an invoice list (.xlsx or .csv)", minutes: 5 },
    steps: [
      "Open Prepare data for DATEV",
      "Drop your customer list or your invoices and choose Bookings or Customers and suppliers",
      "Read the summary: rows prepared, rows not exported with their reason, decisions already applied",
      "Enter the advisor and client numbers when asked, then download the DATEV file",
    ],
    expected: [
      "Only the DATEV formats are offered: no AI-proposed system and no Expert mode on this page",
      "The same screens as the AI Migration Wizard: only the title, the introduction and the targets differ",
      "Nothing is written to your systems and nothing is sent to DATEV",
    ],
    prerequisites: ["DATEV formats published in the Registry"],
    start: { route: "/portal/prepare-datev", label: "Start" },
    routes: ["/portal/prepare-datev"],
    technical_reference: "CLAUDE.md · AI Migration Wizard",
  },
  {
    id: "product.analysis",
    domain: "product",
    audiences: ["supplier"],
    status: "available",
    section: "product",
    order: 20,
    title: "Analyze my catalog",
    summary: "GO45 reads your product structure and asks only what it cannot decide.",
    goal: "Turn a connected system into a ready catalog with a handful of answers.",
    flow: ["My system", "Analyze", "Answer questions", "Catalog ready"],
    systems: { source: ["odoo"], target: [] },
    demo: { instance: "customer17 · Odoo 17", minutes: 3 },
    steps: ["Open My systems → customer17", "Analyze", "Answer the questions", "Continue to catalog"],
    expected: ["Catalog analysis complete"],
    prerequisites: ["A connected system"],
    start: { route: "/portal/systems", label: "Open my systems" },
    routes: [],
  },
  {
    id: "product.catalog",
    domain: "product",
    audiences: ["supplier"],
    status: "available",
    section: "product",
    order: 30,
    title: "Browse my catalog",
    summary: "See your real products, variants and images, read from your system.",
    goal: "Show the canonical catalog built from the analysis, without changing the source system.",
    flow: ["My system", "Sync", "Catalog", "Product"],
    systems: { source: ["odoo"], target: [] },
    demo: { instance: "customer17 · Odoo 17", minutes: 2 },
    steps: ["Open Catalog", "Sync catalog", "Search a product", "Open a product"],
    expected: ["Products with variants, prices and images"],
    prerequisites: ["Catalog analysis complete"],
    start: { route: "/portal/catalog", label: "Open catalog" },
    routes: ["/portal/catalog", "/portal/catalog/[productId]"],
  },
  {
    id: "product.import",
    domain: "product",
    audiences: ["supplier"],
    status: "available",
    section: "product",
    order: 40,
    title: "Import my catalog",
    summary: "Upload a catalog file; GO45 maps the columns, previews and imports it into your system.",
    goal: "From a spreadsheet to products in the ERP, with learned mappings and an explicit preview.",
    flow: ["File", "Analyze", "Review mapping", "Preview", "Import"],
    systems: { source: ["file"], target: ["odoo"] },
    demo: { instance: "customer17 · Odoo 17", minutes: 5 },
    steps: [
      "Open Imports → New import",
      "Upload a .csv or .xlsx file",
      "Choose the target system and analyze",
      "Review the column mapping",
      "Open the preview",
      "Import and review the result",
    ],
    expected: ["Columns matched or explained", "Products created or updated", "Re-import updates, never duplicates"],
    prerequisites: ["Catalog analysis complete"],
    start: { route: "/portal/imports/new", label: "Start" },
    routes: [
      "/portal/imports",
      "/portal/imports/new",
      "/portal/imports/[importId]",
      "/portal/imports/[importId]/preview",
      "/portal/imports/[importId]/runs/[runId]",
    ],
    technical_reference: "CLAUDE.md · Imports de fichiers (V2c / V2d)",
  },
  {
    id: "partner.analysis",
    domain: "partner",
    audiences: ["supplier"],
    status: "available",
    section: "partner",
    order: 10,
    title: "Analyze customers & suppliers",
    summary: "GO45 identifies your customer and supplier data and asks only what it cannot decide.",
    goal: "Prepare partner data for an export with the same engine as the catalog.",
    flow: ["My system", "Customers & suppliers", "Answer questions"],
    systems: { source: ["odoo"], target: [] },
    demo: { instance: "customer17 · Odoo 17", minutes: 2 },
    steps: ["Open My systems → customer17", "Customers & suppliers → Analyze partners", "Answer the question"],
    expected: ["Partner analysis complete"],
    prerequisites: ["A connected system"],
    start: { route: "/portal/systems", label: "Open my systems" },
    routes: [],
  },
  {
    id: "partner.export.datev",
    domain: "partner",
    audiences: ["supplier"],
    status: "available",
    section: "partner",
    order: 20,
    title: "Export customers & suppliers to DATEV",
    summary: "Export customers and suppliers from Odoo or Lexware Office into a DATEV-compatible EXTF file.",
    goal: "Produce a DATEV Debitoren/Kreditoren file with stable account numbers.",
    flow: ["Odoo · Lexware Office", "Partner", "DATEV Debitoren/Kreditoren"],
    systems: { source: ["odoo", "lexware"], target: ["datev"] },
    demo: { instance: "customer17 · Odoo 17", minutes: 4 },
    steps: [
      "Open customer17 and analyze Customers & suppliers",
      "Continue to export",
      "Fill in the export settings (DATEV client)",
      "Preview: review Ready / Warning / Blocked records",
      "Download the DATEV file",
    ],
    expected: [
      "EXTF file generated",
      "Persistent DATEV account numbers reused",
      "Invalid records clearly identified, blocked records not exported",
      "A customer or supplier already exported from another system (same VAT ID) is blocked, never given a second " +
        "account number",
    ],
    prerequisites: [
      "Partner analysis complete (a system with a published interface, such as Lexware Office, needs none)",
      "DATEV format in the Registry",
    ],
    start: { route: "/portal/exports", label: "Start demo" },
    routes: ["/portal/exports"],
    technical_reference: "CLAUDE.md · Domaine PARTNER",
  },
  {
    id: "accounting.read",
    domain: "accounting",
    audiences: ["supplier"],
    status: "experimental",
    section: "accounting",
    order: 5,
    title: "Read accounting documents",
    summary:
      "Read the invoices and credit notes of one company for one month from Odoo or Lexware Office, without changing " +
      "anything.",
    goal: "Check what an accounting export would start from: documents, source accounts, taxes and issues.",
    flow: ["Odoo · Lexware Office", "Accounting documents"],
    systems: { source: ["odoo", "lexware"], target: [] },
    demo: { instance: "accounting17 · Odoo 17 (SKR03)", minutes: 3 },
    steps: [
      "Open My systems → accounting17",
      "Accounting → Analyze accounting, answer the questions (Lexware Office: no analysis, go straight to reading)",
      "Choose the company and the accounting period (2026-01)",
      "Read accounting: open a document to review its lines as debit / credit and its balance",
    ],
    expected: [
      "Documents counted by type and status",
      "Every document balanced (0.00) with its due date",
      "Draft and cancelled documents shown, never eligible for mapping",
      "Unsupported documents kept and flagged",
    ],
    prerequisites: [
      "A connected system with accounting: Odoo (accounting analysis complete) or Lexware Office (no analysis needed)",
    ],
    start: { route: "/portal/systems", label: "Open my systems" },
    routes: [],
    technical_reference: "temp/RESULTAT_ACCOUNTING_PHASE3.md",
  },
  {
    id: "accounting.datev.prepare",
    domain: "accounting",
    audiences: ["supplier"],
    status: "experimental",
    section: "accounting",
    order: 8,
    title: "Prepare accounting for DATEV",
    summary: "See how the documents of one month would be booked in DATEV Buchungsstapel, without creating a file.",
    goal: "Confirm account and tax mappings and check every document is ready before the DATEV export exists.",
    flow: ["Accounting documents", "Mappings", "DATEV Buchungsstapel preview"],
    systems: { source: ["odoo", "lexware"], target: ["datev"] },
    demo: { instance: "accounting17 · Odoo 17 (SKR03)", minutes: 5 },
    steps: [
      "Export customers & suppliers of accounting17 once (DATEV accounts)",
      "Open My systems → accounting17 → Accounting → Read accounting (2026-01)",
      "Prepare for DATEV: currency, then confirm the G/L account and tax mappings",
      "Open RE/2026/00002: two bookings, Konto / Gegenkonto, Soll/Haben, tax key",
    ],
    expected: [
      "Suggestions are never applied without confirmation",
      "Eligible documents and DATEV-ready documents counted separately",
      "Draft and cancelled documents not prepared; unsupported documents blocked",
    ],
    prerequisites: [
      "Accounting analysis complete (a system with a published interface, such as Lexware Office, needs none)",
      "DATEV client set up in the customers & suppliers export",
    ],
    start: { route: "/portal/systems", label: "Open my systems" },
    routes: [],
    technical_reference: "temp/RESULTAT_ACCOUNTING_PHASE4.md",
  },
  {
    id: "accounting.export.datev",
    domain: "accounting",
    audiences: ["supplier"],
    status: "experimental",
    section: "accounting",
    order: 10,
    title: "Export accounting to a DATEV file",
    summary:
      "Download the new bookings of one month as a DATEV Buchungsstapel file (EXTF), without duplicates. Nothing is sent.",
    goal: "A real DATEV Buchungsstapel file with the same customer and supplier accounts as the partner export.",
    flow: ["Odoo · Lexware Office", "Accounting", "DATEV preparation", "DATEV Buchungsstapel file"],
    systems: { source: ["odoo", "lexware"], target: ["datev"] },
    demo: { instance: "accounting17 · Odoo 17 (SKR03)", minutes: 6 },
    steps: [
      "Open My systems → accounting17 (or your Lexware Office system) → Accounting → Read accounting (2026-01)",
      "Prepare for DATEV: mappings confirmed, documents ready",
      "Choose the Festschreibung in the DATEV settings, then Save",
      "Preview DATEV file: header, booking rows, export history (New / Already exported / Changed since export)",
      "Download DATEV file: the documents are now marked as exported",
      "Preview again: nothing new to export — the same documents are never exported twice by accident",
    ],
    expected: [
      "RE/2026/00002 is written as two booking rows (654,50 and 85,39)",
      "Blocked, draft and cancelled documents are never written",
      "Already exported documents are skipped; a document changed since its export waits for an explicit decision",
      "The same document already exported from another system of the same DATEV client (for example sent from Odoo " +
        "to Lexware Office) is blocked, never exported twice",
      "The downloaded file is exactly the previewed one; nothing is sent to DATEV",
    ],
    prerequisites: ["DATEV preparation ready for at least one document"],
    start: { route: "/portal/systems", label: "Open my systems" },
    routes: [],
    technical_reference: "temp/RESULTAT_ACCOUNTING_D11.md",
  },
  {
    id: "accounting.datev.submit",
    domain: "accounting",
    audiences: ["supplier"],
    status: "coming_soon",
    section: "accounting",
    order: 20,
    title: "Send accounting to DATEV",
    summary: "Transmit the bookings to DATEV directly instead of importing the downloaded file.",
    goal: "No manual file import in DATEV.",
    flow: ["DATEV Buchungsstapel file", "DATEV"],
    systems: { source: ["odoo"], target: ["datev"] },
    steps: [],
    expected: [],
    prerequisites: ["DATEV file export"],
    routes: [],
  },
  // ------------------------------------------------------------------ Lexware Office comme CIBLE
  // Aperçu et envoi des contacts (phases 1a-1b) et des pièces comptables (phases 2a-2c) : experimental ; articles :
  // coming_soon (sans point de départ). Lexware comme SOURCE : voir les use cases PARTNER et ACCOUNTING ci-dessus.
  {
    id: "partner.lexware.prepare",
    domain: "partner",
    audiences: ["supplier"],
    status: "experimental",
    section: "partner",
    order: 80,
    title: "Prepare customers & suppliers for Lexware Office",
    summary: "Preview what each customer and supplier would become as a Lexware Office contact. Nothing is sent.",
    goal: "See, before anything is written, which partners are ready, which need a fix, and why.",
    flow: ["Source system", "Partner", "Lexware Office contact (preview)"],
    systems: { source: ["odoo"], target: ["lexware"] },
    demo: { instance: "accounting17 · Odoo 17 → a Lexware Office test account", minutes: 4 },
    steps: [
      "Connect a system → Lexware Office: the form asks only for a name and an API key",
      "Test connection: the company of the account is shown; Save",
      "Open My systems → the system that holds the customers and suppliers → Customers & suppliers",
      "Prepare for <your Lexware Office system>",
      "Read the preview: Ready / Warning / Blocked, the prepared values, the reasons",
    ],
    expected: [
      "A banner says: Nothing is sent to Lexware Office until you select records and confirm",
      "A person without a separate last name is Blocked: the last name is never guessed from the full name",
      "Bank accounts and customer numbers are left out with a warning (Lexware Office assigns its own numbers)",
      "The same preparation works for any source system that provides customers and suppliers",
    ],
    prerequisites: ["Customers & suppliers analysis complete", "A Lexware Office system connected with an API key"],
    start: { route: "/portal/systems", label: "Open my systems" },
    routes: ["/portal/systems/[systemId]/partners/[targetId]"],
    technical_reference: "temp/RESULTAT_LEXWARE_PHASE1A.md",
  },
  {
    id: "partner.export.lexware",
    domain: "partner",
    audiences: ["supplier"],
    status: "experimental",
    section: "partner",
    order: 90,
    title: "Send customers & suppliers to Lexware Office",
    summary: "Create and update your customers and suppliers as Lexware Office contacts, after your confirmation.",
    goal: "Write contacts only when you decide: never a blocked record, never a duplicate, never a silent retry.",
    flow: ["Source system", "Partner", "Lexware Office contacts"],
    systems: { source: ["odoo"], target: ["lexware"] },
    demo: { instance: "customer17 · Odoo 17 → the Lexware Office test account", minutes: 6 },
    steps: [
      "Open the preparation for your Lexware Office system (see « Prepare customers & suppliers »)",
      "Select one or more records that can be sent → Send selected → Confirm and send",
      "Read the results record by record",
      "Possible duplicate: link it to the existing contact, or confirm a new one",
      "Unknown outcome: check in Lexware Office, then tell us whether it was created",
    ],
    expected: [
      "Blocked records cannot be selected; nothing is written before Confirm and send",
      "An existing contact with the same name or e-mail is shown to you instead of being duplicated",
      "Sent again unchanged → Already sent, skipped; changed in the source → updated, nothing erased in Lexware",
      "No reliable answer from Lexware Office → Unknown outcome, never retried until you check",
    ],
    prerequisites: ["Customers & suppliers prepared for Lexware Office (preview)"],
    start: { route: "/portal/systems", label: "Open my systems" },
    routes: ["/portal/systems/[systemId]/partners/[targetId]"],
    technical_reference: "temp/RESULTAT_LEXWARE_PHASE1B.md",
  },
  {
    id: "accounting.export.lexware",
    domain: "accounting",
    audiences: ["supplier"],
    status: "experimental",
    section: "accounting",
    order: 90,
    title: "Send accounting documents to Lexware Office",
    summary:
      "Preview the invoices and credit notes of one month as Lexware Office bookkeeping vouchers, select, confirm, send.",
    goal: "Lexware Office bookkeeping fed from the source system, each document sent only once.",
    flow: ["Source system", "Accounting", "Lexware Office vouchers"],
    systems: { source: ["odoo"], target: ["lexware"] },
    demo: { instance: "accounting17 · Odoo 17 → the Lexware Office test account", minutes: 6 },
    steps: [
      "Open My systems → the system that holds the accounting → Accounting → Read accounting",
      "Prepare for <your Lexware Office system>",
      "Choose the Lexware Office category of each account (the list is read from Lexware Office)",
      "Read each document: Ready / Warning / Blocked, amounts, tax, contact, category",
      "Select ready documents → Send selected → check numbers and amounts → Confirm and send",
      "Read the results; the preview then shows « Already sent »",
    ],
    expected: [
      "Nothing is sent until you select documents and confirm; nothing is written before Confirm and send",
      "Only documents that are Ready and were never sent can be selected",
      "A document whose customer or supplier is not yet in Lexware Office is Blocked",
      "A document number that already exists in Lexware Office is shown to you, never created twice",
      "No reliable answer from Lexware Office → Unknown outcome, never retried until you check",
    ],
    prerequisites: ["Accounting analysis complete", "Customers & suppliers sent to Lexware Office"],
    start: { route: "/portal/systems", label: "Open my systems" },
    routes: ["/portal/systems/[systemId]/accounting/[targetId]"],
    technical_reference: "temp/RESULTAT_LEXWARE_PHASE2C_ETAPE2.md",
  },
  {
    id: "product.lexware.prepare",
    domain: "product",
    audiences: ["supplier"],
    status: "experimental",
    section: "product",
    order: 85,
    title: "Prepare products for Lexware Office",
    summary: "Preview what each product and variant would become as a Lexware Office article. Nothing is sent.",
    goal: "See, before anything is written, which articles are ready, which need a decision or a fix, and why.",
    flow: ["Source system", "Product", "Lexware Office article (preview)"],
    systems: { source: ["odoo"], target: ["lexware"] },
    demo: { instance: "customer17 · Odoo 17 → a Lexware Office test account", minutes: 4 },
    steps: [
      "Open My systems → the system that holds the catalog (analysis complete)",
      "Send to a connected system → Prepare for <your Lexware Office system>",
      "Settings: say whether the prices of your system are net or gross",
      "Tax rates: choose the rate of each sales tax (0, 7 or 19 %)",
      "Read the preview: Ready / Warning / Blocked, the prepared values, the reasons",
    ],
    expected: [
      "One article per product without variants, and one per variant (its own SKU and price)",
      "Nothing is guessed: without a price basis or a tax rate decision, articles are Blocked with the reason",
      "A product without SKU, with several sales taxes or that is neither goods nor a service is Blocked",
      "Archived products are not prepared; nothing is sent to Lexware Office",
    ],
    prerequisites: ["Catalog analysis complete", "A Lexware Office system connected with an API key"],
    start: { route: "/portal/systems", label: "Open my systems" },
    routes: ["/portal/systems/[systemId]/products/[targetId]"],
    technical_reference: "temp/RESULTAT_LEXWARE_ARTICLES_PHASE1A.md",
  },
  {
    id: "product.export.lexware",
    domain: "product",
    audiences: ["supplier"],
    status: "experimental",
    section: "product",
    order: 90,
    title: "Send products to Lexware Office",
    summary: "Create and update your products and services as Lexware Office articles, after your confirmation.",
    goal: "Write articles only when you decide: never a blocked product, never a duplicate, never a silent retry.",
    flow: ["Source system", "Product", "Lexware Office articles"],
    systems: { source: ["odoo"], target: ["lexware"] },
    demo: { instance: "accounting17 · Odoo 17 (EUR) → the Lexware Office test account", minutes: 6 },
    steps: [
      "Open the preparation for your Lexware Office system (see « Prepare products for Lexware Office »)",
      "Select one or more articles that can be sent → Send selected → Confirm and send",
      "Read the results article by article",
      "Possible duplicate (same article number): link it to the existing article, or confirm a new one",
      "Unknown outcome: check in Lexware Office, then tell us whether it was created",
    ],
    expected: [
      "Blocked articles cannot be selected; nothing is written before Confirm and send",
      "An existing article with the same article number (SKU) is shown to you instead of being duplicated",
      "Sent again unchanged → Already sent, skipped; changed in the source → updated with its version, nothing erased",
      "No reliable answer from Lexware Office → Unknown outcome, never retried until you check",
    ],
    prerequisites: ["Products prepared for Lexware Office (preview)"],
    start: { route: "/portal/systems", label: "Open my systems" },
    routes: ["/portal/systems/[systemId]/products/[targetId]"],
    technical_reference: "temp/RESULTAT_LEXWARE_ARTICLES_PHASE1B.md",
  },
];

/**
 * Pages de l'application qui ne sont PAS un use case (entrées, Overview elles-mêmes), avec la raison.
 * Toute autre page doit être citée par au moins un use case (`routes`, ou `start.route`) : test de couverture.
 * @type {Record<string, string>}
 */
export const UNTRACKED_ROUTES = {
  "/": "redirection vers l'accueil du profil",
  "/login": "connexion",
  "/overview": "Overview admin (générée depuis ce catalogue)",
  "/portal": "Overview fournisseur (générée depuis ce catalogue)",
  "/portal/capabilities": "tableau des flux source → cible (généré depuis ce catalogue, lecture seule)",
};

// --------------------------------------------------------------------------- //
// Règles de présentation (pures) : les seules utilisées par les pages Overview
// --------------------------------------------------------------------------- //

/** Use cases d'une audience, prêts à afficher : sans les dépréciés, triés par section puis ordre.
 * @param {Audience} audience
 * @param {UseCaseDefinition[]} [catalog]
 * @returns {UseCaseDefinition[]} */
export function listUseCases(audience, catalog = USE_CASES) {
  const order = new Map(SECTIONS.map((s) => [s.id, s.order]));
  return catalog
    .filter((u) => u.audiences.includes(audience) && u.status !== "deprecated")
    .sort((a, b) => (order.get(a.section) ?? 99) - (order.get(b.section) ?? 99) || a.order - b.order);
}

/** Use cases d'AUTRES audiences (pour la carte complète d'une démo admin), non démarrables ici.
 * @param {Audience} audience
 * @param {UseCaseDefinition[]} [catalog] */
export function listOtherUseCases(audience, catalog = USE_CASES) {
  return AUDIENCES.filter((a) => a !== audience)
    .flatMap((a) => listUseCases(a, catalog))
    .filter((u, i, all) => !u.audiences.includes(audience) && all.findIndex((x) => x.id === u.id) === i);
}

/** Un use case se démarre seulement s'il est disponible (ou expérimental) et a un point de départ.
 * @param {UseCaseDefinition} useCase */
export function canStart(useCase) {
  return (useCase.status === "available" || useCase.status === "experimental") && !!useCase.start;
}

/** Sections non vides d'une liste de use cases, dans l'ordre.
 * @param {UseCaseDefinition[]} useCases */
export function groupBySection(useCases) {
  return SECTIONS.map((section) => ({ section, items: useCases.filter((u) => u.section === section.id) })).filter(
    (g) => g.items.length > 0,
  );
}

/** Titre d'une section pour une audience (le fournisseur ne voit pas les concepts internes, ex. Registry).
 * @param {Section} section
 * @param {Audience} audience */
export function sectionTitle(section, audience) {
  return audience === "supplier" ? (section.supplier_title ?? section.title) : section.title;
}

/** Chemin d'une route sans paramètres de requête ni fragment.
 * @param {string} route */
export function routePath(route) {
  return route.split(/[?#]/)[0];
}

/** Erreurs du catalogue (vide = valide). `pages` : routes réelles de l'application (« /instances/[id] »…) ;
 * sans elles, les contrôles de routes sont sautés.
 * @param {UseCaseDefinition[]} [catalog]
 * @param {string[]} [pages]
 * @returns {string[]} */
export function validateCatalog(catalog = USE_CASES, pages) {
  const errors = [];
  const ids = new Set();
  const sections = new Set(SECTIONS.map((s) => s.id));
  for (const u of catalog) {
    if (ids.has(u.id)) errors.push(`${u.id} : identifiant en double`);
    ids.add(u.id);
    if (!/^[a-z]+(\.[a-z]+)+$/.test(u.id)) errors.push(`${u.id} : identifiant « domaine.action » attendu`);
    if (!u.audiences.length) errors.push(`${u.id} : aucune audience`);
    for (const a of u.audiences) if (!AUDIENCES.includes(a)) errors.push(`${u.id} : audience inconnue ${a}`);
    if (!STATUSES.includes(u.status)) errors.push(`${u.id} : statut inconnu ${u.status}`);
    if (!sections.has(u.section)) errors.push(`${u.id} : section inconnue ${u.section}`);
    for (const c of [...(u.systems?.source ?? []), ...(u.systems?.target ?? [])])
      if (!(c in SYSTEM_NAMES)) errors.push(`${u.id} : système inconnu ${c}`);
    if (!u.systems) errors.push(`${u.id} : systèmes non déclarés (systems)`);
    else if (u.systems.source.length === 0) errors.push(`${u.id} : aucun système (interne = go45)`);
    if (u.status === "available" && !u.start) errors.push(`${u.id} : disponible sans point de départ`);
    if ((u.status === "coming_soon" || u.status === "deprecated") && u.start)
      errors.push(`${u.id} : ${u.status} ne peut pas être démarré (start interdit)`);
    if (u.status === "available" && u.steps.length === 0) errors.push(`${u.id} : disponible sans déroulé de démo`);
    const portal = (/** @type {string} */ r) => routePath(r) === "/portal" || routePath(r).startsWith("/portal/");
    const own = [...u.routes, ...(u.start ? [u.start.route] : [])];
    if (u.audiences.length === 1 && u.audiences[0] === "supplier" && own.some((r) => !portal(r)))
      errors.push(`${u.id} : use case fournisseur pointant hors du portail`);
    if (u.audiences.length === 1 && u.audiences[0] === "admin" && own.some((r) => portal(r)))
      errors.push(`${u.id} : use case admin pointant dans le portail`);
    if (pages) {
      for (const r of own) {
        if (!pages.includes(routePath(r))) errors.push(`${u.id} : route inexistante ${r}`);
      }
      if (u.start && /\[/.test(routePath(u.start.route))) errors.push(`${u.id} : point de départ non navigable`);
    }
  }
  if (pages) {
    const covered = new Set(catalog.flatMap((u) => [...u.routes, ...(u.start ? [routePath(u.start.route)] : [])]));
    for (const p of pages) {
      if (!covered.has(p) && !(p in UNTRACKED_ROUTES)) errors.push(`page ${p} : aucun use case (catalog.mjs)`);
    }
    for (const p of Object.keys(UNTRACKED_ROUTES)) {
      if (!pages.includes(p)) errors.push(`UNTRACKED_ROUTES : page inexistante ${p}`);
    }
  }
  return errors;
}
