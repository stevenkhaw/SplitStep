<script lang="ts">
  import { playerName, scoreboardRows } from '../lib/score'
  import type { Player, ScoreRules, ScoreState } from '../lib/score'

  interface Props {
    rules: ScoreRules
    /** The score ENTERING the rally on screen (lib/score.ts::scoreBefore),
     *  which is also what the numbered render burns -- one number, two
     *  places, so the panel and the reel can never disagree. */
    state: ScoreState
    /** Points before this rally that carry no winner. Shown so the board
     *  reads as provisional instead of silently wrong. */
    unscored: number
    /** P was just pressed with tracking on: ask who won. */
    prompting: boolean
    onwin: (p: Player) => void
  }
  let { rules, state, unscored, prompting, onwin }: Props = $props()

  const rows = $derived(scoreboardRows(state, rules))
  const winnerName = $derived(state.finished ? playerName(rules, state.finished) : null)
  const KEY =
    'inline-block min-w-6 rounded border border-line bg-surface-2 px-1.5 text-center' +
    ' font-data text-caption text-dim'
</script>

<!-- A surface card, never bare court: the secondary text here is dim/faint
     and the contrast rule in app.css only holds it on a surface. -->
<aside class="rounded-lg border border-line bg-surface p-3" aria-label="match score">
  <table class="w-full font-data text-data tabular-nums">
    <tbody>
      {#each rows as row, i (i)}
        <tr data-testid="score-row" class={row[row.length - 1] === 'W' ? 'text-fg' : 'text-dim'}>
          <td class="pr-3 text-left text-fg">{row[0]}</td>
          {#each row.slice(1) as cell, c (c)}
            <!-- Explicit space mustache, not raw markup whitespace: Svelte
                 trims whitespace-only text nodes at an {#each} block's
                 boundary, which left cells rendering with no separator at
                 all in the DOM's textContent (score-panel.test.ts caught
                 it). -->
            <td class="px-1 text-right">{' '}{cell}</td>
          {/each}
        </tr>
      {/each}
    </tbody>
  </table>

  {#if winnerName}
    <p class="mt-2 font-data text-data text-fg">{winnerName} wins</p>
  {/if}

  {#if prompting}
    <p class="mt-2 font-data text-data text-fg" role="status" aria-live="polite">
      Who won? <span class={KEY}>A</span> {rules.players[0]} · <span class={KEY}>B</span>
      {rules.players[1]} · <span class={KEY}>Esc</span> skip
    </p>
  {/if}

  <div class="mt-2 flex gap-2">
    <button
      type="button"
      class="flex-1 rounded border border-line px-2 py-1 font-data text-data text-fg
             hover:bg-surface-2 motion-safe:transition-colors"
      onclick={() => onwin('a')}
    >
      <span class={KEY}>A</span> {rules.players[0]} won
    </button>
    <button
      type="button"
      class="flex-1 rounded border border-line px-2 py-1 font-data text-data text-fg
             hover:bg-surface-2 motion-safe:transition-colors"
      onclick={() => onwin('b')}
    >
      <span class={KEY}>B</span> {rules.players[1]} won
    </button>
  </div>

  {#if unscored > 0}
    <p class="mt-2 font-data text-caption text-faint">{unscored} unscored point{unscored === 1 ? '' : 's'} before this</p>
  {/if}
</aside>
