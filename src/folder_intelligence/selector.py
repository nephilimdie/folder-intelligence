"""Selettore interattivo nel terminale (frecce + Invio) per le conferme sì/no."""

from __future__ import annotations

from typing import Sequence, TypeVar

T = TypeVar("T")


async def select(message: str, options: Sequence[tuple[T, str]], default: int = 0,
                 input=None, output=None) -> T | None:
    """Mostra le opzioni in orizzontale; ←/→ o ↑/↓ per scegliere, Invio per confermare.

    Esc o Ctrl+C restituiscono None. Le lettere iniziali delle opzioni (es. 's', 'n')
    selezionano e confermano subito.
    """
    from prompt_toolkit.application import Application
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style

    state = {"i": max(0, min(default, len(options) - 1)), "done": False}

    def render() -> FormattedText:
        parts: list[tuple[str, str]] = [("class:msg", message + "\n")] if message else []
        parts.append(("", "  "))
        for i, (_, label) in enumerate(options):
            if i == state["i"]:
                parts.append(("class:sel", f" ❯ {label} "))
            else:
                parts.append(("class:opt", f"   {label} "))
            parts.append(("", " "))
        if not state["done"]:
            parts.append(("class:hint", "  ←/→ scegli · Invio conferma · Esc annulla"))
        return FormattedText(parts)

    kb = KeyBindings()

    def move(step: int) -> None:
        state["i"] = (state["i"] + step) % len(options)

    @kb.add("left")
    @kb.add("up")
    @kb.add("s-tab")
    def _(event) -> None:
        move(-1)

    @kb.add("right")
    @kb.add("down")
    @kb.add("tab")
    def _(event) -> None:
        move(1)

    @kb.add("enter")
    def _(event) -> None:
        state["done"] = True
        event.app.exit(result=options[state["i"]][0])

    @kb.add("escape", eager=True)
    @kb.add("c-c")
    @kb.add("c-d")
    def _(event) -> None:
        state["done"] = True
        event.app.exit(result=None)

    # scorciatoie: prima lettera di ogni opzione (s = sì, n = no…)
    shortcuts: dict[str, int] = {}
    for i, (_, label) in enumerate(options):
        key = label.strip()[:1].lower()
        if key.isalpha() and key not in shortcuts:
            shortcuts[key] = i
    for key, idx in shortcuts.items():
        @kb.add(key)
        def _(event, idx=idx) -> None:
            state["i"] = idx
            state["done"] = True
            event.app.exit(result=options[idx][0])

    style = Style.from_dict({
        "msg": "bold",
        "sel": "reverse bold ansicyan",
        "opt": "",
        "hint": "ansibrightblack",
    })
    app: Application = Application(
        layout=Layout(Window(FormattedTextControl(render, show_cursor=False),
                             dont_extend_height=True, wrap_lines=True)),
        key_bindings=kb, style=style, full_screen=False, erase_when_done=False,
        input=input, output=output,
    )
    return await app.run_async()
