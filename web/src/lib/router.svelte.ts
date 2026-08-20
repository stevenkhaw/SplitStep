export type Route = { name: 'library' } | { name: 'session'; id: string }

export function parseHash(hash: string): Route {
  const path = hash.replace(/^#/, '')
  const parts = path.split('/').filter(Boolean)
  if (parts.length === 2 && parts[0] === 's') {
    return { name: 'session', id: parts[1] }
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
