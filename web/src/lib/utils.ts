import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatDate(dateString: string): string {
  return new Date(dateString).toLocaleString()
}

/** Milliseconds since `dateString`, or Infinity if there is no usable date.
 *
 * Infinity rather than 0 so that a missing timestamp reads as "long ago"
 * wherever this is compared against a recency window. Treating unknown as
 * "just happened" would raise an alarm for something nobody can date. */
export function msSince(dateString: string | null | undefined): number {
  if (!dateString) return Number.POSITIVE_INFINITY
  const at = new Date(dateString).getTime()
  return Number.isNaN(at) ? Number.POSITIVE_INFINITY : Date.now() - at
}

/** Coarse relative time: "3m ago", "5h ago", "3d ago". */
export function formatAgo(dateString: string | null | undefined): string {
  const ms = msSince(dateString)
  if (!Number.isFinite(ms)) return "at an unknown time"
  const minutes = Math.floor(ms / 60_000)
  if (minutes < 1) return "just now"
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}
