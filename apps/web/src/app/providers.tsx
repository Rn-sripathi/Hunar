"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { useState, type ReactNode } from "react";

import { ApiError } from "@/lib/api";

/**
 * Client-side providers.
 *
 * The retry policy is the part worth explaining. Retrying a request the
 * server has already rejected on its merits just delays the error the
 * user needs to see, so only genuine transport failures and server-side
 * faults are retried. A validation error, a conflict or an exhausted
 * voice quota is reported immediately.
 */
export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 10_000,
            refetchOnWindowFocus: false,
            retry: (failureCount, error) => {
              if (error instanceof ApiError) {
                // 4xx means the request was wrong; repeating it will not
                // help. 503 from an expired key is equally final.
                if (error.status >= 400 && error.status < 500) return false;
                if (error.isVoiceUnavailable) return false;
              }
              return failureCount < 2;
            },
          },
          mutations: { retry: false },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider
        attribute="class"
        defaultTheme="system"
        enableSystem
        disableTransitionOnChange
      >
        {children}
      </ThemeProvider>
    </QueryClientProvider>
  );
}
