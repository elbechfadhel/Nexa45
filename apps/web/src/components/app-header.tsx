"use client";

/** Header global de la console : sticky, 68px, fond translucide flouté, fine bordure ; marque Nexa45 à
 *  gauche, navigation typographique à droite (menu propre au projet, conservé avec la charte Transliate). Sous 900px, les
 *  liens passent dans un menu déroulant (la maquette les masque, une application doit les garder).
 *  V1d : navigation selon le rôle (console admin ou portail supplier), utilisateur connecté et Logout. */
import { ChevronDown, LogOut, Menu, UserRound, X } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { MeOut } from "@/lib/api/client";
import { homeFor, isAdmin, isPortalPath, useLogout, useMe } from "@/lib/auth";
import { cn } from "@/lib/utils";

type NavLink = { href: string; label: string; match: string[]; exact?: boolean };
/** Entrée du header : un lien, ou un groupe de liens réunis sous un seul menu déroulant. */
type NavItem = NavLink & { children?: NavLink[] };

const ADMIN_NAV: NavItem[] = [
  { href: "/overview", label: "Overview", match: ["/overview"] },
  { href: "/systems", label: "Systems", match: ["/systems", "/snapshots"] },
  { href: "/compare", label: "Version Compare", match: ["/compare"] },
  { href: "/instances", label: "Instances", match: ["/instances"] },
  { href: "/admin/suppliers", label: "Suppliers", match: ["/admin/suppliers"] },
];

const PORTAL_NAV: NavItem[] = [
  { href: "/portal/wizard", label: "Smart export", match: ["/portal/wizard"] },
  {
    href: "/portal",
    label: "Overview",
    match: [],
    children: [
      { href: "/portal", label: "Use cases", match: ["/portal"], exact: true },
      { href: "/portal/capabilities", label: "Capabilities", match: ["/portal/capabilities"] },
    ],
  },
  { href: "/portal/systems", label: "My systems", match: ["/portal/systems"] },
  {
    href: "/portal/catalog",
    label: "Data",
    match: [],
    children: [
      { href: "/portal/catalog", label: "Catalog", match: ["/portal/catalog"] },
      { href: "/portal/exports", label: "Exports", match: ["/portal/exports"] },
      { href: "/portal/imports", label: "Imports", match: ["/portal/imports"] },
    ],
  },
];

function navFor(me: MeOut | null | undefined, pathname: string): NavItem[] {
  if (!me || pathname === "/login") return [];
  return isAdmin(me) && !isPortalPath(pathname) ? ADMIN_NAV : PORTAL_NAV;
}

export function AppHeader() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const me = useMe().data;
  const NAV = navFor(me, pathname);
  const signedIn = !!me && pathname !== "/login";
  const isLinkActive = ({ match, exact }: NavLink) =>
    match.some((m) => pathname === m || (!exact && pathname.startsWith(`${m}/`)));
  const isActive = (item: NavItem) => (item.children ? item.children.some(isLinkActive) : isLinkActive(item));

  // Échap ferme le menu mobile
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <header className="sticky top-0 z-20 border-b border-nav-border bg-nav-bg backdrop-blur-[16px]">
      <div className="app-container flex h-[68px] items-center justify-between gap-5">
        {/* logo Nexa45 (captures/NEXA45 Universal Integration Platform.png, recadré, fond transparent, 3x) :
            36px de haut */}
        <Link href={me ? homeFor(me) : "/login"} className="flex shrink-0 items-center">
          <Image
            src="/brand/nexa45-logo.png"
            alt="Nexa45 · Universal Integration Platform"
            width={779}
            height={144}
            priority
            className="h-9 w-auto"
          />
        </Link>

        <div className="hidden items-center gap-6 min-[900px]:flex">
          <nav aria-label="Main" className="flex items-center gap-6 text-sm">
            {NAV.map((item) => {
              const active = isActive(item);
              // la ligne active est posée sur la bordure du header
              const linkClass = cn(
                "relative flex h-[68px] items-center transition-colors",
                active
                  ? "font-semibold text-brand after:absolute after:inset-x-0 after:-bottom-px after:h-0.5 after:rounded-full after:bg-brand"
                  : "text-nav-link hover:text-foreground",
              );
              if (item.children) {
                return <NavGroup key={item.label} item={item} className={linkClass} isActive={isLinkActive} />;
              }
              return (
                <Link key={item.href} href={item.href} aria-current={active ? "page" : undefined} className={linkClass}>
                  {item.label}
                </Link>
              );
            })}
          </nav>
          {signedIn && <UserMenu me={me} />}
        </div>

        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls="mobile-nav"
          aria-label={open ? "Close menu" : "Open menu"}
          className="rounded-lg p-2 text-nav-link hover:bg-foreground/5 hover:text-foreground min-[900px]:hidden"
        >
          {open ? <X className="size-5" /> : <Menu className="size-5" />}
        </button>
      </div>

      {open && (
        <nav id="mobile-nav" aria-label="Main" className="border-t border-nav-border min-[900px]:hidden">
          <div className="app-container flex flex-col py-2">
            {NAV.flatMap((item) => {
              // un groupe : son titre, puis ses liens en retrait
              const links = item.children ?? [item];
              const title = item.children ? (
                <p key={`${item.label}-title`} className="eyebrow pt-2.5 pb-1 pl-3 text-muted-foreground">
                  {item.label}
                </p>
              ) : null;
              return [
                title,
                ...links.map((link) => {
                  const active = isLinkActive(link);
                  return (
                    <Link
                      key={link.href}
                      href={link.href}
                      onClick={() => setOpen(false)}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "border-l-2 py-2.5 text-[0.9375rem] transition-colors",
                        item.children ? "pl-6" : "pl-3",
                        active
                          ? "border-brand font-semibold text-brand"
                          : "border-transparent text-nav-link hover:text-foreground",
                      )}
                    >
                      {link.label}
                    </Link>
                  );
                }),
              ];
            })}
            {signedIn && <LogoutButton className="mt-1 self-start" />}
          </div>
        </nav>
      )}
    </header>
  );
}

/** Groupe de liens sous un seul titre du header (menu déroulant) ; actif si une de ses pages l'est. */
function NavGroup({
  item,
  className,
  isActive,
}: {
  item: NavItem;
  className: string;
  isActive: (link: NavLink) => boolean;
}) {
  const router = useRouter();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger className={cn(className, "gap-1")}>
        {item.label}
        <ChevronDown className="size-3.5" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        {(item.children ?? []).map((link) => (
          <DropdownMenuItem
            key={link.href}
            onClick={() => router.push(link.href)}
            className={cn(isActive(link) && "font-semibold text-brand")}
          >
            {link.label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function currentOrganization(me: MeOut) {
  return me.memberships.find((m) => m.organization_id === me.current_organization_id);
}

function UserMenu({ me }: { me: MeOut }) {
  const logout = useLogoutAndLeave();
  const org = currentOrganization(me);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-nav-link transition-colors hover:bg-foreground/5 hover:text-foreground"
        aria-label="Account"
      >
        <UserRound className="size-4" />
        <span className="max-w-40 truncate">{me.user.display_name}</span>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-60">
        <DropdownMenuLabel>
          <div className="font-medium text-foreground">{me.user.display_name}</div>
          <div className="meta truncate">{me.user.email}</div>
          <div className="meta">
            {isAdmin(me) ? "Platform administrator" : org ? org.organization_name : "No organization selected"}
          </div>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={logout}>
          <LogOut /> Log out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function LogoutButton({ className }: { className?: string }) {
  const logout = useLogoutAndLeave();
  return (
    <button
      type="button"
      onClick={logout}
      className={cn("flex items-center gap-2 py-2 pl-3 text-[0.9375rem] text-nav-link hover:text-foreground", className)}
    >
      <LogOut className="size-4" /> Log out
    </button>
  );
}

/** Révoque la session côté API puis revient à /login (le cache de la session est vidé). */
function useLogoutAndLeave() {
  const logout = useLogout();
  const router = useRouter();
  return () => logout.mutate(undefined, { onSettled: () => router.replace("/login") });
}
