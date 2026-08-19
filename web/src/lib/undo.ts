const DEFAULT_DEPTH = 200

export class UndoStack<T> {
  #entries: T[] = []
  #depth: number

  constructor(depth: number = DEFAULT_DEPTH) {
    this.#depth = depth
  }

  push(entry: T): void {
    this.#entries.push(entry)
    if (this.#entries.length > this.#depth) this.#entries.shift()
  }

  pop(): T | undefined {
    return this.#entries.pop()
  }

  get size(): number {
    return this.#entries.length
  }

  clear(): void {
    this.#entries = []
  }
}
