<script lang="ts">
  import { untrack } from 'svelte'
  import { POINT_LABELS, anchorFromDraft, draftFromState, isTiebreakAt, withSetCount } from '../lib/anchor'
  import type { AnchorDraft } from '../lib/anchor'
  import type { AnchorState, ScoreRules, ScoreState } from '../lib/score'

  interface Props {
    rules: ScoreRules
    /** The score the replay currently puts on this rally: every field
     *  seeds from it, so correcting one number is one edit. Not named
     *  `state`: a prop by that name shadows the `$state` rune. */
    replayed: ScoreState
    onsave: (state: AnchorState) => void
    oncancel: () => void
  }
  let { rules, replayed, onsave, oncancel }: Props = $props()

  // Seeded once, like ScoreSetup: a form's initial values, not a live
  // mirror. The board behind this form re-derives on every keypress, and
  // the reviewer's half-typed correction must not be stomped by it.
  let draft = $state<AnchorDraft>(untrack(() => draftFromState(replayed)))
  let error = $state<string | null>(null)

  // Games decide whether the points are a tiebreak count or a game label.
  // Re-derived from the draft's games, not the seed's: typing 6-6 flips
  // the points fields, exactly as the engine will read them.
  const tiebreak = $derived(isTiebreakAt([Number(draft.games[0]), Number(draft.games[1])], rules))
  const canAddSet = $derived(draft.sets.length < rules.sets - 1)

  function submit(e: SubmitEvent) {
    e.preventDefault()
    const r = anchorFromDraft(draft, rules)
    if (!r.ok) {
      error = r.error
      return
    }
    error = null
    onsave(r.state)
  }

  const FIELD = 'w-14 rounded border border-line bg-surface px-2 py-1 text-right font-data text-data text-fg'
  const SMALL = 'rounded border border-line px-2 py-0.5 font-data text-caption text-dim hover:text-fg' +
    ' disabled:cursor-not-allowed disabled:opacity-40'
</script>

<!-- Its own surface card: the labels here are dim, which only holds
     contrast on a surface (app.css). -->
<form class="rounded-lg border border-line bg-surface p-3" onsubmit={submit} aria-label="score entering this rally">
  <p class="text-caption text-dim">Score entering this rally</p>
  <table class="mt-2 font-data text-data tabular-nums">
    <thead>
      <tr class="text-caption text-dim">
        <th class="pr-2 text-left font-normal"></th>
        {#each draft.sets as _, i (i)}
          <th class="px-1 text-center font-normal">Set {i + 1}</th>
        {/each}
        {#if rules.tiebreak !== 'only'}
          <th class="px-1 text-center font-normal">Games</th>
        {/if}
        <th class="px-1 text-center font-normal">{tiebreak ? 'TB' : 'Pts'}</th>
      </tr>
    </thead>
    <tbody>
      {#each ['a', 'b'] as const as side, p (side)}
        <tr>
          <td class="pr-2 text-left text-fg">{rules.players[p]}</td>
          {#each draft.sets as _, i (i)}
            <td class="px-1">
              <input type="number" min="0" step="1" name="set{i}{side}" class={FIELD} bind:value={draft.sets[i][p]} />
            </td>
          {/each}
          {#if rules.tiebreak !== 'only'}
            <td class="px-1">
              <input type="number" min="0" step="1" name="games{side.toUpperCase()}" class={FIELD} bind:value={draft.games[p]} />
            </td>
          {/if}
          <td class="px-1">
            {#if tiebreak}
              <input type="number" min="0" step="1" name="points{side.toUpperCase()}" class={FIELD} bind:value={draft.points[p]} />
            {:else}
              <select name="points{side.toUpperCase()}" class={FIELD} bind:value={draft.points[p]}>
                {#each POINT_LABELS as label (label)}
                  {#if label !== 'Ad' || rules.ad}
                    <option value={label}>{label}</option>
                  {/if}
                {/each}
              </select>
            {/if}
          </td>
        </tr>
      {/each}
    </tbody>
  </table>

  {#if rules.tiebreak !== 'only'}
    <div class="mt-2 flex gap-2">
      <button type="button" data-testid="add-set" class={SMALL} disabled={!canAddSet}
        onclick={() => (draft = withSetCount(draft, draft.sets.length + 1))}>+ set</button>
      <button type="button" data-testid="remove-set" class={SMALL} disabled={draft.sets.length === 0}
        onclick={() => (draft = withSetCount(draft, draft.sets.length - 1))}>− set</button>
    </div>
  {/if}

  {#if error}
    <p class="mt-2 text-caption text-danger" role="alert">{error}</p>
  {/if}
  <div class="mt-3 flex justify-end gap-2">
    <button type="button" data-testid="anchor-cancel" class="rounded border border-line px-3 py-1 font-data text-data text-dim hover:text-fg" onclick={oncancel}>Cancel</button>
    <button type="submit" class="rounded bg-fg px-3 py-1 font-data text-data font-medium text-bg hover:bg-fg/90">Anchor</button>
  </div>
</form>
