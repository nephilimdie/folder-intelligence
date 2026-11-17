---
target: interfaccia della chat (TUI)
total_score: 24
max_score: 40
na_heuristics: 
p0_count: 1
p1_count: 2
target_identity: "file:/Users/stefanobassetto/Progetti/folder-intelligence/src/folder_intelligence/tui.py"
target_fingerprint: "sha256:c16f0aaef5892c58f1c4d1a088e00e15587869309b0068dfcda15b616dbdf1fc"
target_path: /Users/stefanobassetto/Progetti/folder-intelligence/src/folder_intelligence/tui.py
timestamp: 2026-11-17T12-14-11Z
slug: src-folder-intelligence-tui-py
---
# Critique: TUI chat (src/folder_intelligence/tui.py)

Method: dual-agent (A: design review · B: detector + pty/pyte render evidence)

| # | Heuristic | Score | Key issue |
|---|---|---|---|
| 1 | Visibility of status | 3 | "Attività (0)" counts only running jobs; pending confirmation listed among "Recenti" |
| 2 | Match real world | 3 | jargon: audit hex id, AES-256-GCM, Memoria MCP |
| 3 | User control | 3 | undo/Esc/ferma exist; one key commits Trash |
| 4 | Consistency | 2 | "#" = job/agenda/confirm; "annulla" = undo/cancel/No |
| 5 | Error prevention | 2 | letter shortcut commits destructive action; past dates silently become today |
| 6 | Recognition | 2 | Ctrl+T/Ctrl+Y only in a status bar that never shows them |
| 7 | Flexibility | 3 | history, Tab, chaining, scroll keys |
| 8 | Minimalist | 2 | 6-line technical banner, path repeated 3x, empty panel |
| 9 | Error recovery | 2 | no "did you mean" |
| 10 | Help | 2 | ~70 unranked lines, no hierarchy |
| Total | | 24/40 | Acceptable |

Design specificity: core flows authored (confirmations, agenda, background jobs); shell generic (system-log banner, dashboard side column). Detector: [] on file and folder — not applicable to Python/terminal UIs (no rules run). Browser overlay: n/a (no DOM).

## Priority issues
- [P0] Single letter in confirm selector commits Trash move (s/y) while "Tu ›" input visible. Fix: no letter shortcuts for destructive kinds; letter selects, Enter confirms; hide/dim input during confirm. (harden)
- [P1] Emoji width: 🗑️⚙️⏹️🖼️✂️ (VS16) measured 1 col, drawn 2 → duplicate list rows lose labels/leave stale chars, help heading "⚙" without label, border shift. Fix: single-width structural symbols, strip U+FE0F, width-safe helper. (harden)
- [P1] Status bar hints (107/102 cols) never fit at any tested width; path first. Fix: shortcuts first, short path, contextual hints ≤3. (clarify)
- [P2] First screen noise: 6-line banner, empty Activity panel takes most height, banner mentions hidden box at 90 cols. Fix: one-line header, 3 starter prompts, collapse idle panel. (distill/onboard)
- [P2] Side column: agenda truncation fixed at 38 cols regardless of width; narrow layout puts agenda under the input. Fix: real-width truncation, input always last. (layout)

## Personas
- Alex: letter-commit while typing; Ctrl+C stops active[0]; no one-key undo; "domani 10:00" overflows 10-col date column.
- Sam: brightblack for borders/banner/title/hints/done items (~3:1 on dark themes); overdue vs today mostly color, ⚠ itself dim; spinner redraw every 0.15s floods screen readers.
- Jordan: AES/MCP before any example; cryptic agenda empty state; 70-line help.

## Minor
Mid-word wrapping; help without hierarchy and no scroll hint; path shown 3x; instant commands logged as jobs; "ferma" vs "interrompe"; unselected option unstyled; Ctrl+Y copies "Non ho capito".

## Questions
Side column only when needed? Why does Trash share key handling with typing? Should "#" be user-facing at all?
