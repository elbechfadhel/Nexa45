import type { NextConfig } from "next";

// API du moteur (FastAPI). La console l'appelle via /uie-api/* sur SA propre origine : le cookie de session
// (HttpOnly, SameSite=Lax) reste de même origine, quelle que soit l'adresse d'accès (localhost ou 127.0.0.1).
const UIE_API_URL = (process.env.UIE_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

const nextConfig: NextConfig = {
  // en bas à droite : en bas à gauche, l'indicateur de dev recouvre la bascule de thème du menu latéral
  devIndicators: { position: "bottom-right" },
  // dev : la console est aussi ouverte sur 127.0.0.1 (pas seulement localhost) ; sans cette entrée, Next 16 bloque
  // ses ressources de dev depuis cette origine, la page ne s'hydrate pas (formulaire de login inerte)
  allowedDevOrigins: ["127.0.0.1"],
  rewrites: async () => [{ source: "/uie-api/:path*", destination: `${UIE_API_URL}/:path*` }],
  experimental: {
    // Analyze (V2b.2) et Sync sont synchrones (pas encore de worker) : avec de vrais appels LLM, une analyse
    // dépasse les 30 s par défaut du proxy Next → 500. 5 minutes.
    proxyTimeout: 300_000,
  },
};

export default nextConfig;
