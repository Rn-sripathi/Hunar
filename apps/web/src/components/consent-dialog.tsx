"use client";

import { Clock, PhoneCall, ShieldAlert } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import type { CampaignDetail } from "@/lib/types";

/**
 * The last thing between a button and a stranger's phone ringing.
 *
 * Deliberately not a "are you sure?" It states the exact count, lists the
 * masked numbers that will actually be dialled, shows the calling window
 * against the current time, and reproduces the opening sentence the
 * person will hear. Then it requires a ticked box.
 *
 * The friction is the feature. Everything shown here is something the
 * operator would otherwise have to take on trust, and cold-calling people
 * who never applied is exactly the case where trust should be replaced by
 * a visible fact.
 */
export function ConsentDialog({
  open,
  onOpenChange,
  campaign,
  onConfirm,
  pending,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  campaign: CampaignDetail;
  onConfirm: () => void;
  pending?: boolean;
}) {
  const [acknowledged, setAcknowledged] = useState(false);

  const dialable = (campaign.targets ?? []).filter(
    (target) => target.status === "PENDING" || target.status === "DEFERRED",
  );
  const introduction = campaign.script_preview?.introduction ?? "";

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setAcknowledged(false);
        onOpenChange(next);
      }}
    >
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldAlert className="size-4 text-amber-600 dark:text-amber-400" />
            Call {dialable.length} {dialable.length === 1 ? "person" : "people"}
            ?
          </DialogTitle>
          <DialogDescription className="leading-relaxed">
            These are real phone calls to people who did not apply and have not
            heard of this company. Every one is on the operator&rsquo;s consent
            allowlist, and the gate is checked again server-side before each
            call is placed.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div>
            <p className="text-xs font-medium">Numbers that will be dialled</p>
            <ScrollArea className="mt-1.5 max-h-32">
              <ul className="space-y-1">
                {dialable.map((target) => (
                  <li
                    key={target.id}
                    className="flex items-center justify-between gap-3 text-sm"
                  >
                    <span className="truncate">{target.prospect_name}</span>
                    <span className="text-muted-foreground shrink-0 font-mono text-xs">
                      {target.mobile_masked}
                    </span>
                  </li>
                ))}
                {dialable.length === 0 && (
                  <li className="text-muted-foreground text-sm italic">
                    Nobody here can be called.
                  </li>
                )}
              </ul>
            </ScrollArea>
          </div>

          <Separator />

          <div>
            <p className="flex items-center gap-1.5 text-xs font-medium">
              <Clock className="size-3.5" />
              What they will hear first
            </p>
            <p className="bg-muted mt-1.5 rounded-md p-3 text-[13px] leading-relaxed italic">
              &ldquo;{introduction}&rdquo;
            </p>
          </div>

          <label className="flex cursor-pointer items-start gap-2.5 text-[13px] leading-relaxed">
            <Checkbox
              checked={acknowledged}
              onCheckedChange={(checked) => setAcknowledged(checked === true)}
              className="mt-0.5"
            />
            <span>
              I confirm these numbers have consented to be contacted, and that
              this outreach is permitted where the recipients are.
            </span>
          </label>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={!acknowledged || dialable.length === 0 || pending}
            onClick={onConfirm}
          >
            <PhoneCall className="size-4" />
            {pending ? "Placing calls…" : `Call ${dialable.length}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
