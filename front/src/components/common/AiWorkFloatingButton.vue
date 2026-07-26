<template>
  <a
    href="/aiWork"
    target="_blank"
    rel="noopener noreferrer"
    aria-label="打开 AI 对话记录"
    title="AI 对话记录"
    class="group fixed bottom-5 right-5 z-[70] flex size-14 items-center justify-center rounded-full border border-white/60 bg-primary-600 text-white shadow-xl shadow-primary-950/25 transition duration-200 hover:-translate-y-1 hover:bg-primary-500 hover:shadow-2xl hover:shadow-primary-950/30 focus:outline-none focus-visible:ring-4 focus-visible:ring-primary-300 dark:border-primary-300/20 dark:bg-primary-500 dark:hover:bg-primary-400"
  >
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      class="size-7"
      stroke="currentColor"
      stroke-width="1.8"
      stroke-linecap="round"
      stroke-linejoin="round"
    >
      <path d="M7.5 17.5 4 20v-4.3A7.5 7.5 0 0 1 2.5 11C2.5 6.9 6.5 3.5 11.5 3.5S20.5 6.9 20.5 11s-4 7.5-9 7.5c-1.45 0-2.8-.28-4-.78Z" />
      <path d="M8 10.75h7" />
      <path d="M8 13.75h4.5" />
      <path d="M18.5 17.5v3" />
      <path d="M17 19h3" />
    </svg>
    <span
      class="pointer-events-none absolute bottom-full right-0 mb-3 hidden whitespace-nowrap rounded-lg bg-slate-950 px-3 py-1.5 text-xs font-semibold text-white shadow-lg group-hover:block group-focus-visible:block"
    >
      AI 对话记录
    </span>
  </a>
</template>
