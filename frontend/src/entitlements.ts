// P9 — entitlement keys, labels and grouping for the Admin Panel UI.
// Mirrors backend/app/entitlements.py DEFAULT_ENTITLEMENTS.
export type EntitlementGroup = 'tools' | 'capabilities' | 'planned'

export interface EntitlementMeta {
  key: string
  label: string
  group: EntitlementGroup
  hint?: string
}

export const ENTITLEMENT_META: EntitlementMeta[] = [
  // tools (model-visible)
  { key: 'web_search', label: 'Web search', group: 'tools', hint: 'web_search + web_scrape tools' },
  { key: 'document_editor', label: 'Document editor', group: 'tools', hint: 'edit_document tool + Documents' },
  { key: 'render', label: 'Render / SVG / Video', group: 'tools', hint: 'render_html, render_svg, render_video tools' },
  { key: 'sandbox', label: 'Sandbox (run_command)', group: 'tools', hint: 'run_command tool' },
  { key: 'git_access', label: 'Agentic Git access', group: 'tools', hint: 'git_* tools + Git management' },
  { key: 'theme_editing', label: 'Theme editing', group: 'tools', hint: 'theme/css tools + Appearance' },
  // capabilities
  { key: 'image_generation', label: 'Image generation', group: 'capabilities' },
  { key: 'file_upload', label: 'File uploads', group: 'capabilities' },
  { key: 'voice_input', label: 'Voice input (STT)', group: 'capabilities' },
  { key: 'tts', label: 'Text-to-speech (P7)', group: 'capabilities' },
  { key: 'youtube_previews', label: 'YouTube previews (P8)', group: 'capabilities' },
  { key: 'conversation_branching', label: 'Conversation branching', group: 'capabilities' },
  { key: 'memory', label: 'Memory', group: 'capabilities', hint: 'cross-session memory + Memory tab' },
  // planned agentic features
  { key: 'extrovert_agentic', label: 'Extrovert agentic (P1)', group: 'planned' },
  { key: 'obsidian_vault', label: 'Obsidian vault (P2)', group: 'planned' },
  { key: 'vps_agent', label: 'VPS agent (P3)', group: 'planned' },
  { key: 'proton_pass', label: 'Proton Pass (P10)', group: 'planned' },
  { key: 'skills_management', label: 'Skill management (P11)', group: 'planned' },
  { key: 'skill_marketplace', label: 'Skill marketplace (P12)', group: 'planned' },
]

export const ENTITLEMENT_GROUPS: { key: EntitlementGroup; label: string }[] = [
  { key: 'tools', label: 'Tools (model-visible)' },
  { key: 'capabilities', label: 'Capabilities' },
  { key: 'planned', label: 'Planned features (locked now, enforced when built)' },
]

export function entitlementLabel(key: string): string {
  return ENTITLEMENT_META.find(e => e.key === key)?.label || key
}
