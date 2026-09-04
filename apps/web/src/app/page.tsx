import { redirect } from "next/navigation";

/** Roles are the only entry point today, so the root goes straight there. */
export default function Home() {
  redirect("/jobs");
}
