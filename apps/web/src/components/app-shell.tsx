"use client";

import { Headphones, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { DemoBanner } from "@/components/demo-banner";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// Two applications, one shell. They share the voice infrastructure, the
// database and this frame; what differs is whether the person on the
// other end applied for the job or was found without asking.
const NAV = [
  { href: "/jobs", label: "Screening" },
  { href: "/sourcing", label: "Sourcing" },
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

          <nav className="hidden items-center gap-1 sm:flex">
            {NAV.map((item) => {
              const active =
                pathname === item.href || pathname.startsWith(`${item.href}/`);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                    active
                      ? "bg-muted text-foreground"
                      : "text-muted-foreground hover:text-foreground hover:bg-muted/60",
                  )}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>

          <div className="ml-auto flex items-center gap-1">
            <ThemeToggle />
          </div>
        </div>
      </header>

      <DemoBanner />

      <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 sm:px-6 sm:py-8">
        {children}
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
