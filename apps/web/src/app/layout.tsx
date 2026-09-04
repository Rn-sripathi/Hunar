import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { AppShell } from "@/components/app-shell";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Providers } from "@/app/providers";

import "./globals.css";

const sans = Geist({ variable: "--font-sans", subsets: ["latin"] });
const mono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Screening Console",
  description:
    "Screen job candidates by AI voice call, and review the answers in one place.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    // suppressHydrationWarning is required by next-themes: it sets the
    // theme class on <html> before React hydrates, which would otherwise
    // be reported as a mismatch on every load.
    <html lang="en" suppressHydrationWarning>
      <body
        className={`${sans.variable} ${mono.variable} bg-background text-foreground antialiased`}
      >
        <Providers>
          <TooltipProvider delayDuration={200}>
            <AppShell>{children}</AppShell>
            <Toaster position="bottom-right" richColors closeButton />
          </TooltipProvider>
        </Providers>
      </body>
    </html>
  );
}
