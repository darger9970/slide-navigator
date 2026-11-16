# Slide Navigator UI Design Specification

This document is the authoritative visual and interaction specification for
Slide Navigator frontend development. New screens and changes to existing
screens must follow these requirements unless a future design decision
explicitly supersedes them.

The primary visual reference is the Microsoft Dynamics 365-style screenshot
provided for Slide Navigator. Reproduce its visual language, not its company
identity or proprietary assets.

## Design principles

- Build a polished, professional enterprise application inspired by Microsoft
  Fluent UI and Dynamics 365.
- Favor clarity, compact controls, restrained borders, and useful whitespace.
- Keep the presentation workspace local-first. Do not send presentation content
  to external services.
- Avoid generic dashboard styling, decorative charts, gradients, oversized
  cards without a functional purpose, and colourful AI-themed decoration.
- Do not use Avanade branding, logos, or proprietary application assets.
- Prefer Segoe UI and locally available, inline Fluent-style SVG icons. Do not
  require a remote font or icon service.

## Colour palette and CSS tokens

Use these design tokens as the shared source of truth across application screens:

| Token | Value | Use |
| --- | --- | --- |
| `--brand-orange` | `#FF5A00` | Application header and primary actions |
| `--orange-hover` | `#E95000` | Hover state for primary orange actions |
| `--orange-tint` | `#FFF1E8` | Subtle orange highlights and selected accents |
| `--surface` | `#FFFFFF` | Main content and control surfaces |
| `--sidebar` | `#F3F3F3` | Persistent left navigation background |
| `--border` | `#E5E5E5` | Subtle separators, outlines, and card borders |
| `--text` | `#242424` | Primary text |
| `--secondary` | `#616161` | Supporting text and labels |
| `--link` | `#0078D4` | Links, selected navigation indicator, focus accents |
| `--focus` | `#0078D4` | Keyboard focus outline |

Use white for the primary workspace and light neutral surfaces for secondary
content. Apply orange as a brand accent, not as a large-area background outside
the application header. Keep contrast readable; do not use low-contrast grey
for essential content.

Define tokens in `:root` (or an equivalent shared theme layer). Add tokens
there before introducing repeated hard-coded design values. Themes and
components must consume these shared tokens consistently.

## Typography

- Use `Segoe UI` with a sans-serif fallback, for example
  `"Segoe UI", Arial, sans-serif`.
- Use regular weight (400) for body text and semibold (600) for controls,
  section labels, and emphasis. Use bold (700) sparingly for strong hierarchy.
- Body text and controls should generally be approximately 13–14 px.
- Secondary descriptions and compact metadata may use 11–12 px where they
  remain readable.
- Page headings should be approximately 20–24 px, typically weight 600.
- Section headings should be approximately 15–17 px, typically weight 600.
- Keep line height comfortable for scanning; use compact line height for
  controls and metadata.
- Do not load typography from a third-party network.

## Application shell

### Header

- Use a full-width, approximately 58 px high orange application header with
  `--brand-orange` as its background.
- Place the Slide Navigator mark and title on the left. The title must remain
  readable at narrow widths.
- Include a global slide-library search field in the header.
- Place compact settings and local user/workspace controls on the right.
- Keep header content vertically centered and controls keyboard accessible.
- On small screens, retain the brand, search, settings/user access, and a menu
  control without causing horizontal overflow.

### Navigation sidebar

- Use a persistent left sidebar beneath the header, approximately 200–230 px
  wide on desktop, with `--sidebar` as its background.
- Keep navigation labels and icons left aligned with consistent spacing.
- Required navigation destinations are:
  - Home
  - Slide Library
  - Import
  - Search
  - Deck Builder
  - Settings
- Identify the current destination with a blue selection indicator and a clear
  active state. Do not rely on colour alone; preserve readable text and
  accessible current-page semantics.
- Provide visible hover, focus, and pressed feedback.
- On small screens, the sidebar may become a dismissible off-canvas menu.

### Main workspace

- Use a white main workspace with a readable content width and consistent
  horizontal padding.
- Start each screen with a page heading. Add a breadcrumb and a short
  description when they provide context.
- Place contextual actions, such as Import, in a compact toolbar aligned with
  the page heading or content controls.
- Use 1 px neutral borders and small 4–6 px corner radii. Use shadows only
  sparingly and where they communicate elevation or layering.

## Controls and interaction states

- Buttons, inputs, selects, and toolbars should be compact and visually
  consistent. Use approximately 35–40 px control heights where practical.
- Primary actions use `--brand-orange` with white text. Hover uses
  `--orange-hover`. Secondary actions use a white or neutral surface with a
  subtle border.
- Inputs use white backgrounds, neutral 1 px borders, readable placeholder
  text, and a blue focus treatment.
- Include clear hover, keyboard focus, active/selected, loading, disabled, and
  error states. Disabled controls must look and behave disabled.
- Preserve visible keyboard focus; do not remove outlines without a clear
  replacement.
- Use accessible labels for icon-only buttons and controls. Use SVG icons with
  consistent stroke weight and size; icons must not replace clear text labels
  where a label is needed.
- Prefer inline or locally hosted Fluent-style icons. Do not depend on remote
  icon libraries or proprietary assets.

## Slide Library

The Slide Library is the primary content workspace. Its toolbar should support:

- Search across presentations and slides.
- Filtering by available import or indexing status.
- Sorting by useful library fields, such as name, date added, or slide count.
- A toggle between thumbnail grid view and compact table view.

### Thumbnail grid view

- Display presentation or slide cards in a responsive grid.
- Where thumbnail data is available, show a preview resembling a miniature
  PowerPoint slide, with a neutral preview background and restrained border.
- Include available metadata such as presentation name, slide number, source
  folder, and import status.
- Do not fabricate presentations, thumbnails, slide counts, or statuses. Show a
  clear empty state and an Import action when the library contains no data.

### Compact table view

- Use a restrained data-grid appearance with clear column headers, row
  separators, and compact spacing.
- Display presentation name, slide number or slide count as appropriate, source
  folder, and import status when available.
- Support sorting from relevant column headers where data is available.
- Provide a useful empty or no-results state when the library is empty or
  filters match no items.

The grid/table toggle must be interactive, expose its selected state
accessibly, and preserve the current search and filter context when switching
views.

## Shared page styling and behavior

All pages must use the same header, navigation, typography, tokens, control
styles, spacing, icon treatment, and interaction states.

- **Home:** Provide a concise entry point to the library and common actions,
  including Import. Avoid decorative dashboard metrics without real data.
- **Import:** Provide drag-and-drop and browse controls for files and folders.
  Clearly communicate what is supported and the current import state. Never
  imply that files have been ingested when they have only been selected.
- **Search:** Use the shared search styling and clearly distinguish available
  results from unavailable indexing functionality. Do not imply that
  presentation content is being searched before indexing exists.
- **Deck Builder:** Use the same page-heading and workspace layout. Clearly
  communicate when slide selection or export is not yet available.
- **Settings:** Use compact, aligned settings rows and cards. Display Ollama
  connectivity, model availability, loading, unavailable, and error states
  consistently with the shared status styling.

Empty, loading, error, and unavailable states must be truthful, concise, and
visually consistent. Do not present placeholder content as real user data.

## Responsive behavior

- Prioritize desktop use in a Windows browser while supporting tablet and
  mobile widths.
- Keep the desktop sidebar persistent. At narrow widths, collapse it into a
  menu that can be opened and dismissed.
- Reflow toolbars and page-heading actions when needed; controls must remain
  operable and must not overlap.
- Let library cards reduce their column count and let tables scroll
  horizontally when columns cannot fit.
- Keep search fields usable at every supported width and avoid page-level
  horizontal overflow.
- Support keyboard operation and visible focus across responsive navigation
  and controls.

## Consistency and change management

- Treat this document as the design contract for all frontend work.
- Reuse shared CSS tokens and component patterns instead of creating
  screen-specific visual systems.
- When a requested product behavior is not implemented yet, show an honest
  unavailable or empty state using these styles rather than a static mockup
  that implies functionality.
- Update this document when the user explicitly changes the visual direction
  or design rules.
