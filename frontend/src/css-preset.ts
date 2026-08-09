export const BASE_THEME_CSS = `:root {
  /* Extrovert · Ink (dark) — https://extrovert.redforged.eu */
  /* Surfaces */
  --theme-bg: 16 19 31;
  --theme-bg-secondary: 24 27 46;
  --theme-bg-elevated: 33 37 64;
  --theme-bg-hover: 42 47 77;
  --theme-bg-active: 42 47 77;
  --theme-overlay: 0 0 0;

  /* Messages */
  --theme-msg-user: 56 36 59;
  --theme-msg-ai: 24 27 46;

  /* Code */
  --theme-code-bg: 27 30 48;

  /* Text */
  --theme-text: 242 240 251;
  --theme-text-secondary: 168 170 204;
  --theme-text-muted: 118 121 160;
  --theme-text-subtle: 143 146 184;
  --theme-text-accent: 255 92 138;
  --theme-text-accent-dim: 122 236 239;
  --theme-text-danger: 255 93 108;
  --theme-icon-muted: 58 63 94;

  /* Accent (interactive) — rose */
  --theme-accent: 255 92 138;
  --theme-accent-hover: 255 125 163;

  /* Danger */
  --theme-danger: 255 93 108;
  --theme-danger-hover: 255 122 133;

  /* Borders */
  --theme-border: 42 46 72;
  --theme-border-light: 58 63 94;

  /* Icons */
  --theme-icon-user: 255 92 138;
  --theme-icon-ai: 92 223 224;

  /* Special */
  --theme-purple: 167 139 250;
  --theme-amber: 255 206 77;
  --theme-spinner: 255 92 138;
  --theme-focus-ring: 255 125 163;
  --theme-switch-off: 72 77 106;
  --theme-blue: 92 223 224;
  --theme-blue-text: 122 236 239;

  /* Preview */
  --theme-preview-bg: 255 255 255;

  /* Border radius */
  --theme-radius: 0.75rem;
  --theme-radius-sm: 0.5rem;
  --theme-radius-md: 0.75rem;
  --theme-radius-lg: 1rem;
  --theme-radius-xl: 1.375rem;
  --theme-radius-2xl: 1.5rem;
  --theme-radius-full: 9999px;

  /* Shadows */
  --theme-shadow-xl: 0 6px 22px rgb(0 0 0 / 0.28);
  --theme-shadow-2xl: 0 18px 48px rgb(0 0 0 / 0.38);

  /* Font families */
  --theme-font-sans: "Hanken Grotesk", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  --theme-font-mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;

  /* Font weights */
  --theme-font-medium: 500;
  --theme-font-semibold: 600;
  --theme-font-bold: 700;

  /* Transitions */
  --theme-duration: 200ms;
  --theme-ease: cubic-bezier(0.4, 0, 0.2, 1);

  /* Layout */
  --theme-sidebar-width: 18rem;
  --theme-chat-max-width: 56rem;
  --theme-panel-max-width: 48rem;
  --theme-dropdown-width: 16rem;
  --theme-bubble-max-width: 75%;
  --theme-side-panel-width: 420px;
  --theme-avatar-size: 2rem;
  --theme-message-gap: 1rem;
  --theme-preview-height: 24rem;
  --theme-auth-max-width: 24rem;

  /* Z-index */
  --theme-z-modal: 50;

  /* Animation */
  --theme-spin-duration: 1s;
  --theme-pulse-duration: 2s;
}`

const GLOBAL_CSS = `
* {
  scrollbar-width: thin;
  scrollbar-color: #374151 transparent;
}

body {
  margin: 0;
  font-family: var(--theme-font-sans);
}

pre::-webkit-scrollbar {
  height: 6px;
}

pre::-webkit-scrollbar-track {
  background: transparent;
}

pre::-webkit-scrollbar-thumb {
  background: #374151;
  border-radius: 3px;
}

code::before,
code::after {
  content: none;
}`

export const DEFAULT_CSS = BASE_THEME_CSS + '\n' + GLOBAL_CSS
