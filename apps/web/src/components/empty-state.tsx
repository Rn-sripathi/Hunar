import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * The state a screen is in most often when someone is learning it.
 *
 * Every empty state names the next action rather than merely reporting
 * absence, because "No candidates yet" leaves a first-time user stuck
 * while "Add a candidate to start screening" does not.
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-lg border border-dashed px-6 py-14 text-center",
        className,
      )}
    >
      <span className="bg-muted text-muted-foreground mb-4 flex size-11 items-center justify-center rounded-full">
        <Icon className="size-5" />
      </span>
      <h3 className="text-[15px] font-semibold">{title}</h3>
      <p className="text-muted-foreground mt-1.5 max-w-md text-sm leading-relaxed text-balance">
        {description}
      </p>
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
