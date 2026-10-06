# Wearables — Color Standards

This isn't just a palette — color is part of how this app communicates. The rules below matter more than the hex codes; the hex codes just make the rules consistent to implement.

## The core rule: color = meaning, always

**Red means bad. Amber means caution. Green means good. Gray means "nothing to report."** That's the entire vocabulary, and it holds everywhere in the app — Blood Pressure, Pulse Pressure, Heart Rate alerts, device battery, anywhere else it shows up next. A user should never have to re-learn what a color means on a different tab.

Because that vocabulary is reserved, a few things follow:

- **Never borrow red/amber/green for something that isn't actually a status judgment.** A chart series that's just "Diastolic" or "Intraday HR" — with no good/bad direction — gets its own distinct color (blue, teal, violet, ...), never a severity color, even if it would otherwise look nice. Reusing a severity color for a non-severity purpose creates a false signal.
- **Related tiles must agree.** If a BP reading is "Stage 1," the Systolic tile, the Diastolic tile, *and* the Category tile all compute their color from the **same** severity logic (same `case()` thresholds), so they're never inconsistent with each other. Color is derived from one shared judgment of the underlying value, not decided independently per panel.
- **A severity color must come from a real, cited reference range, not a made-up threshold.** Blood pressure staging uses the AHA's own bands; Pulse Pressure's thresholds came from actual research (Cleveland Clinic / clinical literature), not round numbers that felt right. If there's no recognized standard for a metric, don't invent a red/amber/green judgment for it — leave it uncolored rather than imply a verdict the app can't back up.
- **Color should encode a signal, not decorate a label.** In the one table in the app with cell coloring (Device battery), only the Battery column — the thing that actually varies in "goodness" — is colored. The Device name column stays plain white even though it's technically possible to color it; coloring a value that doesn't carry a verdict would just be noise.
- **Gray is a real, deliberate state, not a placeholder.** When there's no data for a metric, the panel shows a titled, gray, explicit message ("Needs Withings") — never a blank or vanished panel. Silence reads as broken; an explicit gray message reads as "nothing here yet, by design."
- **Every severity `case()` resolves to a color, with no undefined state.** The last branch is always a plain `true()` fallback to the "normal" color — a metric should never render with no color at all, which would just look like a bug.
- **Restraint over decoration.** We explicitly declined adding ✓/⚠/✗ glyphs on top of color+text in the one table that has conditional coloring — redundant once color and the raw value are already both visible. Don't stack a second coding system on top of color unless color alone genuinely isn't scannable.

## The palette

| Color | Hex | Meaning |
|---|---|---|
| 🟢 Green | `#4CAF50` | Normal / good |
| 🟠 Amber | `#F4A422` | Caution / borderline |
| 🔴 Red | `#E8503A` | Bad / needs attention |
| ⚪ Gray | `#8a93a0` | Neutral / no data |

Examples: AHA BP categories (Normal/Elevated/Stage 1+) on the Blood Pressure tab; Pulse Pressure's researched bands; High HR alert annotations (red) vs. Low (blue, see below — a direction, not a severity) vs. Irregular Rhythm; device battery level.

## Panel, background & text

| Color / value | Use |
|---|---|
| `rgba(15,17,23,0.50)` | Standard panel background, on effectively every panel in every dashboard. It's semi-transparent *on purpose* — the point is to let each dashboard's background art show through, so panels read as frosted glass over art rather than flat gray boxes. |
| `#FFFFFF` | Titles and primary single-value text, used sparingly against the dark theme |
| `rgba(244,164,34,0.14)` + `#F4A422` | Background + text/icon for a standalone "important notice" callout panel (e.g. the Blood Pressure tab's medical disclaimer) — amber-tinted so it visually reads as "pay attention," not as a status judgment on data |

Every dashboard in the app is built dark-theme-first; colors are chosen to read clearly against a near-black background, not adapted from a light-theme palette.

## Non-severity chart series

When a chart needs multiple series with no good/bad direction (e.g. Systolic vs. Diastolic vs. Pulse Pressure on one trend line), each gets its own distinct hue, chosen specifically to avoid colliding with the severity palette's meaning:

| Color | Hex | Example use |
|---|---|---|
| Blue | `#4e9cf5` | Diastolic trend line; Low HR alert annotations |
| Teal | `#4E9EAF` | Intraday Heart Rate line |
| Violet | `#9B6BCE` | Pulse Pressure trend line; Irregular Rhythm (AFib) alert annotations |

Systolic reuses severity red (`#E8503A`) deliberately — a high systolic reading genuinely *is* the "bad" signal, so it's the one series in that chart allowed to carry severity meaning.

## Specialized visualizations

A few dashboards (Sleep stages, Wellness body-composition/bio-age gauges) use their own small, self-contained color sets for things like sleep-stage bands or composition segments. Those are intentionally scoped to that one visualization — don't treat them as app-wide standards, and don't reuse them elsewhere.

## How color gets computed (implementation)

Color is always **derived from the real value in SPL**, never hardcoded per panel:

- **Single-value tiles:** compute a `sev_color` field in the data source's SPL (a `case()` over the real thresholds, ending in a `true()` fallback), then bind `majorColor` to `"> primary | seriesByName('sev_color') | lastPoint()"`.
- **Table cells:** compute a *hidden* helper field (leading underscore, e.g. `_sev_color`), set the table's top-level `"showInternalFields": false` to keep it from rendering as its own column, then bind the target column via `columnFormat.<Column>.rowColors` (text) or `rowBackgroundColors` (cell background) to `"> table | seriesByName('_sev_color')"`. One hidden field can drive multiple columns.
- **Chart annotations:** the annotation data source's SPL emits an `annotation_color` field per event, bound via the chart's `annotationColor` option.

Tables stay plain text by default across the app — conditional coloring is the deliberate exception, used only where the column genuinely carries a severity signal.

## Adding a new color

Before introducing a new hex value, ask: does an existing color already mean this? If you need a genuinely new series/category color, pick something visually distinct from the severity palette (so it's never mistaken for a status signal) and from any other series already sharing that panel.
