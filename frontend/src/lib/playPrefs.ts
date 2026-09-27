// Playback preferences shared by the player and the lists that feed it, kept
// in this browser. No imports, so both sides can read them without a cycle.

const CONTINUE_KEY = "discoverContinue";
const SAMPLER_KEY = "playSampler";

/** Carry on to the next row of a list when a release's tracks run out. */
export function continueOn(): boolean {
  try { return localStorage.getItem(CONTINUE_KEY) === "true"; } catch { return false; }
}
export function setContinueOn(on: boolean) {
  try { localStorage.setItem(CONTINUE_KEY, String(on)); } catch { /* not remembered */ }
}

/** Tracks to play of each release: 0 = all of them, else the N most played. */
export function samplerSize(): number {
  try {
    const n = Number(localStorage.getItem(SAMPLER_KEY) || "0");
    return Number.isFinite(n) && n > 0 ? n : 0;
  } catch {
    return 0;
  }
}
export function setSamplerSize(n: number) {
  try { localStorage.setItem(SAMPLER_KEY, String(n)); } catch { /* not remembered */ }
}

// Releases listened to: seeded from the Discover feed, added to by the player.
// The walk down a list reads it at each step, so something heard a minute ago
// is skipped too.
export function heardKey(artist: string, album?: string | null): string {
  return `${artist.toLowerCase()}|${(album || "").toLowerCase()}`;
}
export const heardReleases = new Set<string>();
