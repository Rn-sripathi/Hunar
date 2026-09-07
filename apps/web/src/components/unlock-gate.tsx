"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Lock, PlugZap } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError, queryKeys } from "@/lib/api";

/**
 * Asks for the shared password when the deployment is protected.
 *
 * Driven by whether the API answers at all rather than by a build-time
 * flag, so the same bundle works locally with no password and deployed
 * with one. The API replies `locked`; anything else means we are through.
 *
 * This is not a login. There are no accounts and no record of who did
 * what. It exists because the deployed database holds real candidates'
 * names and phone numbers, and a URL in a submission email should not be
 * readable by anyone who finds it.
 */
export function UnlockGate({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const [password, setPassword] = useState("");

  const { data, isPending, error } = useQuery({
    queryKey: queryKeys.meta,
    queryFn: api.meta,
    retry: false,
    staleTime: 5 * 60 * 1000,
  });

  const unlock = useMutation({
    mutationFn: () => api.unlock(password),
    onSuccess: () => {
      setPassword("");
      // Everything fetched while locked is a 401; drop the lot rather
      // than leaving screens showing an error they have recovered from.
      void queryClient.resetQueries();
    },
  });

  if (isPending) {
    return (
      <div className="flex min-h-64 items-center justify-center">
        <Loader2 className="text-muted-foreground size-5 animate-spin" />
      </div>
    );
  }

  // Fail closed. The app is rendered only on a definite success, never
  // merely on the absence of a recognised failure.
  //
  // The first version of this asked "did the API say `locked`?" and
  // rendered the app whenever the answer was no. That is wrong in the
  // one case that matters: a request blocked by CORS never becomes a 401
  // in JavaScript at all, because the browser refuses it before any
  // response body is readable. `fetch` simply rejects, the code saw
  // `network_error` rather than `locked`, concluded the deployment was
  // not protected, and rendered every screen — each then failing on its
  // own with no explanation and no password prompt in sight.
  if (data) return <>{children}</>;

  const unreachable =
    error instanceof ApiError && error.code === "network_error";
  if (unreachable) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center px-4">
        <Card className="w-full max-w-md gap-0 p-6">
          <span className="flex size-9 items-center justify-center rounded-md bg-amber-100 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300">
            <PlugZap className="size-4" />
          </span>
          <h1 className="mt-4 text-lg font-semibold tracking-tight">
            Cannot reach the API
          </h1>
          <p className="text-muted-foreground mt-1.5 text-sm leading-relaxed">
            The request was refused before any reply could be read, which almost
            always means the backend does not allow this site&rsquo;s origin.
            Set <code className="text-xs">CORS_ORIGINS</code> on the API to{" "}
            <code className="text-xs">
              {typeof window === "undefined" ? "" : window.location.origin}
            </code>{" "}
            and restart it.
          </p>
          <p className="text-muted-foreground mt-2 text-xs leading-relaxed">
            A browser caches a refused preflight for ten minutes, so once it is
            fixed, reload in a private window rather than waiting.
          </p>
          <Button
            variant="outline"
            className="mt-4"
            onClick={() => queryClient.resetQueries()}
          >
            Try again
          </Button>
        </Card>
      </div>
    );
  }

  return (
    <div className="flex min-h-[60vh] items-center justify-center px-4">
      <Card className="w-full max-w-sm gap-0 p-6">
        <span className="bg-primary/10 text-primary flex size-9 items-center justify-center rounded-md">
          <Lock className="size-4" />
        </span>

        <h1 className="mt-4 text-lg font-semibold tracking-tight">
          This demo is password protected
        </h1>
        <p className="text-muted-foreground mt-1.5 text-sm leading-relaxed">
          It holds real candidates&rsquo; names and phone numbers, so it is not
          open to the internet. The password is in the submission email.
        </p>

        <form
          className="mt-5 space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (password.trim()) unlock.mutate();
          }}
        >
          <div>
            <Label htmlFor="demo-password" className="text-[13px]">
              Password
            </Label>
            <Input
              id="demo-password"
              type="password"
              autoFocus
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="mt-1.5"
            />
          </div>

          {unlock.isError && (
            <p className="text-sm text-red-700 dark:text-red-400">
              {unlock.error instanceof ApiError &&
              unlock.error.code === "bad_password"
                ? "That password is not right."
                : "Could not reach the API. Try again in a moment."}
            </p>
          )}

          <Button
            type="submit"
            className="w-full"
            disabled={!password.trim() || unlock.isPending}
          >
            {unlock.isPending ? (
              <>
                <Loader2 className="size-4 animate-spin" />
                Unlocking…
              </>
            ) : (
              "Unlock"
            )}
          </Button>
        </form>
      </Card>
    </div>
  );
}
