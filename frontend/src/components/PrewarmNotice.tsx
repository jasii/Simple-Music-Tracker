// While the audio pre-load runs, pages don't look audio up themselves: the
// pre-load is already spending the catalogues' request budgets on the same
// releases, so play uses only what's been found (your library, saved
// previews, ones looked up before). These say so, instead of "no audio".
import { useQuery } from "@tanstack/react-query";
import { LuHourglass } from "react-icons/lu";
import { toast } from "sonner";
import { api } from "../api";
import { Alert, AlertDescription, AlertTitle } from "./ui/alert";

/** The pre-load's state, shared with Settings (same query key). */
export function usePrewarm() {
  const { data } = useQuery({
    queryKey: ["prewarm"],
    queryFn: () => api.prewarmStatus(),
    refetchInterval: (q) => (q.state.data?.running ? 5000 : 30000),
  });
  return data;
}

/** A line above a list with play buttons, while the pre-load runs. */
export function PrewarmNotice() {
  const data = usePrewarm();
  if (!data?.running) return null;
  return (
    <Alert className="mb-4">
      <LuHourglass aria-hidden />
      <AlertTitle>
        Pre-loading audio{data.total ? ` (${data.done}/${data.total})` : ""}
      </AlertTitle>
      <AlertDescription>
        Until it's done, play uses only audio that's already been found -- your library,
        saved previews and ones looked up before -- so the sites' request limits aren't
        exceeded. Everything else can be played once it finishes.
      </AlertDescription>
    </Alert>
  );
}

export function pausedMessage(what: string): string {
  return `The audio pre-load is running, so ${what} won't be looked up until it's done.`;
}

/**
 * Nothing played for *what*: say it's the pre-load holding lookups back when
 * that's why, else that there's no audio.
 */
export function explainNoAudio(what: string, fallback: string) {
  api
    .prewarmStatus()
    .then((s) => (s.running ? toast.info(pausedMessage(what)) : toast.error(fallback)))
    .catch(() => toast.error(fallback));
}
