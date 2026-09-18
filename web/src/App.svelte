<script lang="ts">
  import AppBar from './components/AppBar.svelte'
  import CourtGround from './components/CourtGround.svelte'
  import Library from './routes/Library.svelte'
  import Audit from './routes/Audit.svelte'
  import Reel from './routes/Reel.svelte'
  import Reels from './routes/Reels.svelte'
  import Session from './routes/Session.svelte'
  import Setup from './routes/Setup.svelte'
  import { appmode } from './lib/appmode.svelte'
  import { createRouter } from './lib/router.svelte'

  const router = createRouter()
  // Fire-and-forget: the store defaults to 'dev' and corrects itself when
  // this lands; nothing below blocks on it.
  appmode.load()

  const isReview = $derived(router.current.name === 'session')
</script>

<CourtGround tier={isReview ? 'review' : 'browse'} />
<AppBar />

<!--
  Was `max-w-6xl` -- a 1152px column centred in whatever the monitor is,
  which on a 3440px ultrawide left 1144px dead on each side and capped the
  review video at 1152 against a proxy that is 1920x1080.

  Review is capped at 1920 rather than at the container, because past that
  the video is upscaling and gains nothing. The width it does not use shows
  the quieted court and nothing else: queue review is a keyboard loop with
  the eyes on one rectangle, and a panel beside it is something to look at
  that is not the rally.
-->
<main
  class="mx-auto w-full p-6 {isReview
    ? 'max-w-[1920px]'
    : 'max-w-[min(2600px,92vw)]'}"
>
  {#if router.current.name === 'library'}
    <Library />
  {:else if router.current.name === 'setup'}
    <Setup id={router.current.id} />
  {:else if router.current.name === 'reels'}
    <Reels />
  {:else if router.current.name === 'reel'}
    <Reel slug={router.current.slug} />
  {:else if router.current.name === 'audit'}
    <Audit id={router.current.id} />
  {:else}
    <Session id={router.current.id} />
  {/if}
</main>
