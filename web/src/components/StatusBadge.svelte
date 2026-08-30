<script lang="ts">
  import type { StatusBadge } from '../lib/status'

  let { badge }: { badge: StatusBadge } = $props()

  // `quiet` is bare text rather than a pill: a settled state is the common
  // case, and giving every row a chip makes the two that actually want
  // attention indistinguishable from the rest -- the same failure the raw
  // status string had.
  const TONE = {
    quiet: 'text-faint',
    active: 'rounded-full border border-fg/50 px-2 py-0.5 text-fg',
    danger: 'rounded-full border border-danger/30 bg-danger/10 px-2 py-0.5 text-danger',
  } as const
</script>

<span class="shrink-0 font-data text-caption {TONE[badge.tone]}">{badge.label}</span>
