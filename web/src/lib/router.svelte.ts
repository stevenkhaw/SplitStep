export type Route =
  | { name: 'library' }
  | { name: 'session'; id: string }
  | { name: 'setup'; id: string }
  | { name: 'reels' }
  | { name: 'reel'; slug: string }
  | { name: 'audit'; id: string }

export function parseHash(hash: string): Route {
  const path = hash.replace(/^#/, '')
  const parts = path.split('/').filter(Boolean)
  if (parts.length === 1 && parts[0] === 'reels') {
    return { name: 'reels' }
  }
  // The slug is one segment by construction -- slugify() collapses every
  // run of non-alphanumerics to a single hyphen, so a slug can never contain
  // a slash. A deeper path is therefore not a reel, and falls through.
  if (parts.length === 2 && parts[0] === 'reels') {
    return { name: 'reel', slug: parts[1] }
  }
  if (parts.length === 2 && parts[0] === 'setup') {
    return { name: 'setup', id: parts[1] }
  }
  if (parts.length === 2 && parts[0] === 's') {
    return { name: 'session', id: parts[1] }
  }
  // Keyed on a source, not a session: a blind labelling pass walks one
  // source's own timeline, because every window it draws is a span of that
  // source's proxy.
  if (parts.length === 2 && parts[0] === 'audit') {
    return { name: 'audit', id: parts[1] }
  }
  return { name: 'library' }
}

export function createRouter() {
  let route = $state<Route>(parseHash(window.location.hash))
  window.addEventListener('hashchange', () => {
    route = parseHash(window.location.hash)
  })
  return {
    get current() {
      return route
    },
  }
}

export function navigate(to: string): void {
  window.location.hash = to
}
