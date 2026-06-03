export const DEFAULT_CSS = `:root {
  /* Surfaces */
  --theme-bg: 3 7 18;
  --theme-bg-secondary: 17 24 39;
  --theme-bg-elevated: 31 41 55;
  --theme-bg-hover: 55 65 81;
  --theme-bg-active: 55 65 81;
  --theme-overlay: 0 0 0;

  /* Messages */
  --theme-msg-user: 4 120 87;
  --theme-msg-ai: 31 41 55;

  /* Code */
  --theme-code-bg: 26 27 38;

  /* Text */
  --theme-text: 243 244 246;
  --theme-text-secondary: 209 213 219;
  --theme-text-muted: 107 114 128;
  --theme-text-subtle: 156 163 175;
  --theme-text-accent: 52 211 153;
  --theme-text-accent-dim: 110 231 183;
  --theme-text-danger: 248 113 113;
  --theme-icon-muted: 55 65 81;

  /* Accent (interactive) */
  --theme-accent: 5 150 105;
  --theme-accent-hover: 16 185 129;

  /* Danger */
  --theme-danger: 220 38 38;
  --theme-danger-hover: 239 68 68;

  /* Borders */
  --theme-border: 31 41 55;
  --theme-border-light: 55 65 81;

  /* Icons */
  --theme-icon-user: 37 99 235;
  --theme-icon-ai: 5 150 105;

  /* Special */
  --theme-purple: 192 132 252;
  --theme-amber: 251 191 36;
  --theme-spinner: 52 211 153;
  --theme-focus-ring: 16 185 129;
  --theme-switch-off: 75 85 99;
  --theme-blue: 37 99 235;
  --theme-blue-text: 147 197 253;

  /* Preview */
  --theme-preview-bg: 255 255 255;

  /* Border radius */
  --theme-radius: 0.25rem;
  --theme-radius-sm: 0.125rem;
  --theme-radius-md: 0.375rem;
  --theme-radius-lg: 0.5rem;
  --theme-radius-xl: 0.75rem;
  --theme-radius-2xl: 1rem;
  --theme-radius-full: 9999px;

  /* Shadows */
  --theme-shadow-xl: 0 20px 25px -5px rgb(0 0 0 / 0.1);
  --theme-shadow-2xl: 0 25px 50px -12px rgb(0 0 0 / 0.25);

  /* Font families */
  --theme-font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --theme-font-mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;

  /* Font sizes */
  --theme-text-xs: 0.75rem;
  --theme-text-sm: 0.875rem;
  --theme-text-base: 1rem;
  --theme-text-lg: 1.125rem;
  --theme-text-xl: 1.25rem;
  --theme-text-2xl: 1.5rem;

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

  /* Z-index */
  --theme-z-modal: 50;

  /* Animation */
  --theme-spin-duration: 1s;
  --theme-pulse-duration: 2s;
}

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
