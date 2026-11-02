<script lang="ts">
  import { onMount } from "svelte";
  import { upcoming, countdown, formatTime, formatDay, DAY_NAMES, type Slot } from "../../lib/schedule";

  let {
    slots,
    timeZone,
    links,
    showWeek = true,
  }: {
    slots: Slot[];
    timeZone: string;
    links: Record<string, string>;
    showWeek?: boolean;
  } = $props();

  // Avant hydratation : heure de référence (rendu statique stable)
  let now = $state<Date | null>(null);
  let localZone = $state(timeZone);

  const occ = $derived(now ? upcoming(slots, now, timeZone) : []);
  const current = $derived(occ.find((o) => now && o.startsAt <= now && now < o.endsAt));
  const next = $derived(current ?? occ.find((o) => now && o.startsAt > now));
  const cd = $derived(next && now ? countdown(next.startsAt.getTime() - now.getTime()) : null);
  const week = $derived([...slots].sort((a, b) => a.weekday - b.weekday || a.start.localeCompare(b.start)));
  const byId = $derived(new Map(occ.map((o) => [o.id, o])));

  const KIND: Record<Slot["kind"], string> = { stream: "Stream", special: "Spécial", discord: "Discord" };
  const PLATFORM: Record<Slot["platform"], string> = { twitch: "Twitch", youtube: "YouTube", discord: "Discord" };
  const pad = (n: number) => String(n).padStart(2, "0");

  function timeOf(slot: Slot) {
    const o = byId.get(slot.id);
    return o ? formatTime(o.startsAt) : slot.start.replace(":", " h ");
  }
  function dayOf(slot: Slot) {
    const o = byId.get(slot.id);
    return o ? formatDay(o.startsAt) : DAY_NAMES[slot.weekday - 1];
  }

  onMount(() => {
    localZone = Intl.DateTimeFormat().resolvedOptions().timeZone || timeZone;
    now = new Date();
    let id: ReturnType<typeof setInterval> | undefined;
    const start = () => {
      clearInterval(id);
      id = setInterval(() => (now = new Date()), 1000);
    };
    const onVis = () => (document.hidden ? clearInterval(id) : ((now = new Date()), start()));
    start();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVis);
    };
  });
</script>

<div class="schedule">
  <article class="next" class:is-live={!!current}>
    <p class="kicker">{current ? "En ce moment" : "Prochain rendez-vous"}</p>
    {#if next}
      <h3>{next.title}</h3>
      <p class="when">
        <span class="cap">{formatDay(next.startsAt)}</span> à {formatTime(next.startsAt)} · {PLATFORM[next.platform]}
      </p>
      {#if current}
        <a class="btn btn--ble" href={links[current.platform]} rel="noopener" target="_blank">Rejoindre le live</a>
      {:else if cd}
        <div class="countdown" role="timer" aria-label="Compte à rebours">
          {#each [["j", cd.d], ["h", cd.h], ["min", cd.m], ["s", cd.s]] as [u, v] (u)}
            <span class="cell"><b>{pad(v as number)}</b><small>{u}</small></span>
          {/each}
        </div>
      {/if}
    {:else}
      <h3>Le planning arrive</h3>
      <p class="when">Les horaires s'affichent dans ton fuseau horaire.</p>
    {/if}
  </article>

  {#if showWeek}
    <ol class="week" aria-label="Planning de la semaine">
      {#each week as slot (slot.id)}
        <li class="slot k-{slot.kind}" class:is-next={next?.id === slot.id}>
          <span class="day">{dayOf(slot)}</span>
          <span class="time">{timeOf(slot)}</span>
          <span class="title">{slot.title}</span>
          <span class="tag">{KIND[slot.kind]} · {PLATFORM[slot.platform]}</span>
        </li>
      {/each}
    </ol>
    <p class="tz">
      Horaires affichés pour le fuseau <strong>{localZone}</strong>.
      <a class="link" href="/planning.ics">Ajouter à mon agenda</a>
    </p>
  {/if}
</div>

<style>
  .schedule {
    container-type: inline-size;
    display: grid;
    gap: 1.25rem;
  }
  .next {
    position: relative;
    display: grid;
    gap: 0.6rem;
    padding: clamp(1.4rem, 1rem + 2vw, 2.25rem);
    border-radius: var(--radius-l);
    background: var(--encre);
    color: var(--papier);
    overflow: hidden;
    isolation: isolate;
  }
  .next::after {
    content: "";
    position: absolute;
    inset: auto -22% -75% auto;
    width: 18rem;
    aspect-ratio: 1;
    border-radius: 50%;
    border: 1.5rem solid var(--ble);
    opacity: 0.9;
    z-index: -1;
  }
  .kicker {
    font-family: var(--font-display);
    font-size: 0.85rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--ble);
  }
  .next h3 {
    font-size: var(--step-3);
    max-width: 18ch;
  }
  .when {
    color: color-mix(in oklab, var(--papier) 82%, var(--encre));
  }
  .cap::first-letter {
    text-transform: uppercase;
  }
  .countdown {
    display: flex;
    gap: 0.5rem;
    margin-top: 0.6rem;
    flex-wrap: wrap;
  }
  .cell {
    display: grid;
    place-items: center;
    min-width: 4.2rem;
    padding: 0.6rem 0.5rem 0.45rem;
    border-radius: var(--radius-s);
    background: color-mix(in oklab, var(--papier) 10%, var(--encre));
    border: 1px solid color-mix(in oklab, var(--papier) 18%, var(--encre));
  }
  .cell b {
    font-family: var(--font-display);
    font-size: var(--step-2);
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    line-height: 1;
  }
  .cell small {
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    opacity: 0.8;
  }
  .next .btn {
    justify-self: start;
    margin-top: 0.5rem;
  }

  .week {
    list-style: none;
    padding: 0;
    display: grid;
    border-top: 1.5px solid var(--encre);
  }
  .slot {
    display: grid;
    grid-template-columns: 7.5rem 5rem 1fr auto;
    align-items: center;
    gap: 1rem;
    padding: 1rem 0.75rem;
    border-bottom: 1px solid var(--line);
    transition: background-color 0.25s;
  }
  .slot:hover {
    background: var(--ble-voile);
  }
  .slot.is-next {
    background: var(--ble-clair);
  }
  .day {
    font-family: var(--font-display);
    font-weight: 600;
    text-transform: capitalize;
  }
  .time {
    font-variant-numeric: tabular-nums;
    font-weight: 600;
  }
  .title {
    font-weight: 500;
  }
  .tag {
    font-size: 0.8rem;
    font-weight: 600;
    padding: 0.25rem 0.65rem;
    border-radius: 99px;
    border: 1.5px solid var(--encre);
    white-space: nowrap;
  }
  .k-special .tag {
    background: var(--ble);
    border-color: var(--ble);
  }
  .k-discord .tag {
    background: var(--casaque);
    border-color: var(--casaque);
    color: var(--papier);
  }
  .tz {
    font-size: 0.9rem;
    color: var(--fg-muted);
  }
  @container (max-width: 34rem) {
    .slot {
      grid-template-columns: 1fr auto;
      gap: 0.15rem 1rem;
    }
    .title {
      grid-column: 1 / -1;
      grid-row: 2;
    }
    .tag {
      grid-row: 1 / 3;
      grid-column: 3;
    }
    .day,
    .time {
      grid-row: 1;
    }
    .slot {
      grid-template-columns: auto 1fr auto;
    }
  }
</style>
