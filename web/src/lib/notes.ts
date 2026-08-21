import type { Rally } from './types'

/**
 * The longest note that still renders as two lines inside the caption pill at
 * 4K without shrinking the type. Mirrors NOTE_MAX_CHARS in
 * bootleg/db/rallies.py -- the server enforces it too, because the UI is not
 * the only writer a library ever has.
 */
export const NOTE_MAX_CHARS = 120

/**
 * What actually gets sent and stored: trimmed, then capped.
 *
 * Trim happens BEFORE the cap so 120 characters of text followed by spaces is
 * not treated as over the limit -- the same order the server's validator uses,
 * so the two can never disagree about whether a note fits.
 */
export function normalizeNote(raw: string): string {
  return raw.trim().slice(0, NOTE_MAX_CHARS)
}

/**
 * Whether committing `buffer` would change what the server holds.
 *
 * Compares normalized forms, so opening the field and closing it -- or adding
 * and removing a trailing space -- costs no POST at all.
 */
export function isDirty(buffer: string, saved: string): boolean {
  return normalizeNote(buffer) !== normalizeNote(saved)
}

/**
 * Rally id -> note, for the notes a session actually has.
 *
 * Empty notes are omitted rather than stored as '', so `has(id)` is the whole
 * question the ✎ indicator asks and no caller has to distinguish "absent" from
 * "present but empty".
 */
export function seedNotes(rallies: Rally[]): Map<string, string> {
  const m = new Map<string, string>()
  for (const r of rallies) {
    if (r.note) m.set(r.id, r.note)
  }
  return m
}
