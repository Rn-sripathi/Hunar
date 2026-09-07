"use client";

import { Headphones, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { DemoBanner } from "@/components/demo-banner";
import { UnlockGate } from "@/components/unlock-gate";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// Two applications, one shell. They share the voice infrastructure, the
// database and this frame; what differs is whether the person on the
// other end applied for the job or was found without asking.
const NAV = [
  { href: "/jobs", label: "Screening" },
  { href: "/sourcing", label: "Sourcing" },
  { href: "/sourcing/prospects", label: "Prospects" },
  { href: "/sourcing/campaigns", label: "Campaigns" },
];

function ThemeToggle() {
  const { setTheme, resolvedTheme } = useTheme();

  // Both icons are rendered and CSS picks one from the `dark` class that
  // next-themes puts on <html> before hydration. That avoids the usual
  // "wait for mount" effect entirely: no hydration mismatch, no cascading
  // render, and no icon flashing in on load.
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label="Toggle between light and dark"
      onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
      className="size-8"
    >
      <Moon className="size-4 dark:hidden" />
      <Sun className="hidden size-4 dark:block" />
    </Button>
  );
}

/**
 * The frame every screen sits in.
 *
 * The demo banner lives here rather than on individual pages so it
 * cannot be forgotten on one of them. A dashboard that presents
 * simulated calls as real ones would be the single most misleading thing
 * this application could do.
 */
/** Whether a nav item points at the page being viewed.
 *
 * `/sourcing` must not light up while on `/sourcing/prospects`, which has
 * its own entry, so a prefix match is only used where no more specific
 * item exists.
 */
function isActive(pathname: string, href: string): boolean {
  if (pathname === href) return true;
  const deeper = NAV.some(
    (item) =>
      item.href !== href &&
      item.href.startsWith(`${href}/`) &&
      pathname.startsWith(item.href),
  );
  return !deeper && pathname.startsWith(`${href}/`);
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="flex min-h-screen flex-col">
      <header className="bg-background/85 sticky top-0 z-40 border-b backdrop-blur-sm">
        <div className="mx-auto flex h-14 w-full max-w-[1400px] items-center gap-6 px-4 sm:px-6">
          <Link href="/jobs" className="flex items-center gap-2.5">
            <span className="bg-primary text-primary-foreground flex size-7 items-center justify-center rounded-md">
              <Headphones className="size-4" />
            </span>
            <span className="text-[15px] leading-none font-semibold tracking-tight">
              Hunar Recruiting
            </span>
          </Link>

          {/* Wide screens: inline. The same list appears below the header
              on a phone, rather than behind a hamburger — four items fit,
              and a menu you have to open is a menu people do not find. */}
          <nav className="hidden items-center gap-1 sm:flex">
            {NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                aria-current={
                  isActive(pathname, item.href) ? "page" : undefined
                }
                className={cn(
                  "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                  isActive(pathname, item.href)
                    ? "bg-muted text-foreground"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted/60",
                )}
              >
                {item.label}
              </Link>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-1">
            <ThemeToggle />
          </div>
        </div>
      </header>

      {/* Phones get the nav as its own row. Horizontally scrollable so it
          survives a longer label or a fifth screen without wrapping into
          a second line that pushes the content down. */}
      <nav className="bg-background/85 sticky top-14 z-30 border-b backdrop-blur-sm sm:hidden">
        <div
          className="flex gap-1 overflow-x-auto px-4 py-2"
          style={{ scrollbarWidth: "none" }}
        >
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              aria-current={isActive(pathname, item.href) ? "page" : undefined}
              className={cn(
                "shrink-0 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                isActive(pathname, item.href)
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground bg-muted/50",
              )}
            >
              {item.label}
            </Link>
          ))}
        </div>
      </nav>

      <DemoBanner />

      <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 sm:px-6 sm:py-8">
        {/* Wraps every screen, so a new page cannot be added that
            forgets to ask. */}
        <UnlockGate>{children}</UnlockGate>
      </main>

      <footer className="text-muted-foreground border-t px-4 py-4 text-xs sm:px-6">
        <div className="mx-auto flex w-full max-w-[1400px] flex-wrap items-center justify-between gap-2">
          <span>
            AI voice agents for screening applicants and reaching people who
            never applied.
          </span>
          <span>Outbound calls go only to consented numbers.</span>
        </div>
      </footer>
    </div>
  );
}
