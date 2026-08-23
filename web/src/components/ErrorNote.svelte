<script lang="ts">
  import { describeApiError } from '../lib/errors'

  interface Props {
    /** Whatever was caught. Not pre-formatted: the raw value carries the
     *  status this needs to say anything useful. */
    error: unknown
    /** What the page was showing when it failed -- 'session', 'reel',
     *  'source'. A "not found" without a noun is the same vagueness the raw
     *  thrown string had. */
    subject: string
  }
  let { error, subject }: Props = $props()

  const friendly = $derived(describeApiError(error, subject))
</script>

<div class="rounded-lg border border-danger/30 bg-danger/10 p-4" role="alert">
  <p class="text-body text-danger">{friendly.message}</p>
  {#if friendly.detail}
    <!-- Kept, not hidden: this app's user is the same person running the
         server, so the request that failed is the next thing they want. It
         sits at caption weight so it reads as a footnote to the sentence
         above rather than as the message itself. -->
    <p class="mt-2 font-data text-caption break-all text-danger/70">{friendly.detail}</p>
  {/if}
</div>
