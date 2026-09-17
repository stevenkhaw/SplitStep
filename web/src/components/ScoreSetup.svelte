<script lang="ts">
  import { untrack } from 'svelte'
  import type { ScoreRules } from '../lib/score'

  interface Props {
    initial: ScoreRules
    onstart: (rules: ScoreRules) => void
    oncancel: () => void
  }
  let { initial, onstart, oncancel }: Props = $props()

  // Seeded once, like QueueMode's one-shot reads of `detail` -- this is a
  // form's initial values, not a live mirror of `initial`, so a parent
  // re-render (e.g. while `settingUp` stays true) must not stomp on
  // whatever the reviewer has already typed. `untrack` tells svelte-check
  // that is intentional rather than a forgotten `$derived`.
  const seed = untrack(() => initial)
  let nameA = $state(seed.players[0])
  let nameB = $state(seed.players[1])
  let sets = $state<ScoreRules['sets']>(seed.sets)
  let ad = $state(seed.ad)
  let tiebreak = $state<ScoreRules['tiebreak']>(seed.tiebreak)
  let tiebreakTo = $state<ScoreRules['tiebreakTo']>(seed.tiebreakTo)
  let error = $state<string | null>(null)

  function submit(e: SubmitEvent) {
    e.preventDefault()
    const a = nameA.trim()
    const b = nameB.trim()
    if (!a || !b) {
      error = 'Each player needs a name'
      return
    }
    error = null
    onstart({ players: [a, b], sets, ad, tiebreak, tiebreakTo })
  }

  const FIELD = 'rounded border border-line bg-surface px-2 py-1 font-data text-data text-fg'
</script>

<form class="rounded-lg border border-line bg-surface p-3" onsubmit={submit} aria-label="score tracking rules">
  <div class="grid grid-cols-2 gap-2">
    <label class="text-caption text-dim">Player A
      <input type="text" class="{FIELD} mt-1 w-full" bind:value={nameA} maxlength="24" />
    </label>
    <label class="text-caption text-dim">Player B
      <input type="text" class="{FIELD} mt-1 w-full" bind:value={nameB} maxlength="24" />
    </label>
    <label class="text-caption text-dim">Format
      <select class="{FIELD} mt-1 w-full" bind:value={tiebreak}>
        <option value="at6">Sets, tiebreak at 6–6</option>
        <option value="none">Sets, no tiebreak</option>
        <option value="only">One tiebreak only</option>
      </select>
    </label>
    {#if tiebreak === 'only'}
      <label class="text-caption text-dim">Tiebreak to
        <select class="{FIELD} mt-1 w-full" bind:value={tiebreakTo}>
          <option value={7}>7</option>
          <option value={10}>10</option>
        </select>
      </label>
    {:else}
      <label class="text-caption text-dim">Best of
        <select class="{FIELD} mt-1 w-full" bind:value={sets}>
          <option value={1}>1 set</option>
          <option value={3}>3 sets</option>
          <option value={5}>5 sets</option>
        </select>
      </label>
      <label class="col-span-2 flex items-center gap-2 text-caption text-dim">
        <input type="checkbox" class="accent-fg" bind:checked={ad} />
        Deuce / advantage (untick for no-ad)
      </label>
    {/if}
  </div>
  {#if error}
    <p class="mt-2 text-caption text-danger" role="alert">{error}</p>
  {/if}
  <div class="mt-3 flex justify-end gap-2">
    <button type="button" class="rounded border border-line px-3 py-1 font-data text-data text-dim hover:text-fg" onclick={oncancel}>Cancel</button>
    <button type="submit" class="rounded bg-fg px-3 py-1 font-data text-data font-medium text-bg hover:bg-fg/90">Start</button>
  </div>
</form>
