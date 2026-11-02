<script lang="ts">
  import { onMount } from "svelte";
  import { fetchStreamStatus, type StreamStatus } from "../../lib/api";
  import { upcoming, formatDay, formatTime, type Slot } from "../../lib/schedule";

  let {
    slots,
    timeZone,
    href,
    apiBase = "",
    variant = "compact",
    pollMs = 60_000,
  }: {
    slots: Slot[];
    timeZone: string;
    href: string;
    apiBase?: string;
    variant?: "compact" | "full";
    pollMs?: number;
  } = $props();

  let status = $state<StreamStatus | null>(null);
  let now = $state<Date | null>(null);

  const next = $derived(now ? upcoming(slots, now, timeZone).find((o) => o.platform !== "discord" && o.startsAt > now!) : null);
  const label = $derived(status === null ? "Statut…" : status.live ? "En direct" : "Hors ligne");

  onMount(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let ctrl: AbortController | undefined;

    const tick = async () => {
      ctrl?.abort();
      ctrl = new AbortController();
      now = new Date();
      status = await fetchStreamStatus(apiBase, slots, timeZone, ctrl.signal);
      if (!document.hidden) timer = setTimeout(tick, pollMs);
    };
    const onVisibility = () => {
      clearTimeout(timer);
      if (!document.hidden) tick();
    };

    tick();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      clearTimeout(timer);
      ctrl?.abort();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  });
</script>

<a
  class="live {variant}"
  class:is-live={status?.live}
  class:is-pending={status === null}
  {href}
  aria-live="polite"
  title={status?.source === "simulated" ? "Statut estimé à partir du planning" : undefined}
>
  <span class="dot" aria-hidden="true"></span>
  <span class="label">{label}</span>
  {#if variant === "full"}
    <span class="detail">
      {#if status?.live && status.title}
        — {status.title}
      {:else if next}
        · prochain live {formatDay(next.startsAt)} à {formatTime(next.startsAt)}
      {/if}
    </span>
  {/if}
</a>

<style>
  .live {
    --dot: color-mix(in oklab, var(--encre) 45%, var(--papier));
    display: inline-flex;
    align-items: center;
    gap: 0.55rem;
    min-height: 2.25rem;
    padding: 0.35rem 0.9rem 0.35rem 0.75rem;
    border: 1.5px solid var(--line);
    border-radius: 99px;
    background: var(--papier);
    color: var(--encre);
    font-family: var(--font-display);
    font-weight: 600;
    font-size: 0.9rem;
    letter-spacing: 0.02em;
    text-decoration: none;
    white-space: nowrap;
    transition:
      background-color 0.3s,
      border-color 0.3s,
      color 0.3s,
      transform 0.3s var(--ease-spring);
  }
  .live:hover {
    transform: translateY(-1px);
    border-color: var(--encre);
  }
  .live.full {
    white-space: normal;
    font-size: 0.95rem;
  }
  .label {
    white-space: nowrap;
  }
  .full {
    flex-wrap: wrap;
    row-gap: 0;
    border-radius: 1.2rem;
  }
  .detail {
    font-weight: 500;
    color: var(--fg-muted);
  }
  .dot {
    position: relative;
    flex: none;
    width: 0.6rem;
    height: 0.6rem;
    border-radius: 50%;
    background: var(--dot);
  }
  .is-pending .dot {
    animation: blink 1.2s ease-in-out infinite;
  }
  .is-live {
    background: var(--encre);
    border-color: var(--encre);
    color: var(--papier);
    --dot: var(--ble);
  }
  .is-live .detail {
    color: color-mix(in oklab, var(--papier) 80%, var(--encre));
  }
  .is-live .dot::after {
    content: "";
    position: absolute;
    inset: 0;
    border-radius: inherit;
    background: var(--ble);
    animation: ping 1.6s var(--ease-out) infinite;
  }
  @keyframes ping {
    to {
      transform: scale(2.8);
      opacity: 0;
    }
  }
  @keyframes blink {
    50% {
      opacity: 0.3;
    }
  }
</style>
