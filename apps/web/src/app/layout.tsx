import type { Metadata } from "next";
import { DM_Mono, DM_Sans } from "next/font/google";

import { AppHeader } from "@/components/app-header";
import { AuthGate } from "@/components/auth-gate";

import { Providers } from "./providers";
import "./globals.css";

// charte Transliate : DM Sans (texte), DM Mono (références, identifiants) ; auto-hébergées par next/font
const dmSans = DM_Sans({ variable: "--font-dm-sans", subsets: ["latin"], weight: ["400", "500", "600", "700", "800"] });
const dmMono = DM_Mono({ variable: "--font-dm-mono", subsets: ["latin"], weight: ["400", "500"] });

export const metadata: Metadata = {
  title: "Nexa45 · Universal Integration Platform",
  description: "Nexa45 Universal Integration Platform: admin console and supplier portal",
};

// charte Transliate : thème clair uniquement. Le thème sombre (.dark dans globals.css, ThemeToggle) est
// conservé dans le code mais n'est plus appliqué ni proposé.
export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${dmSans.variable} ${dmMono.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col bg-background text-foreground">
        <Providers>
          <AppHeader />
          {/* largeur commune : app-container (globals.css), alignée sur le header */}
          <main className="app-container flex-1 py-8">
            <AuthGate>{children}</AuthGate>
          </main>
        </Providers>
      </body>
    </html>
  );
}
