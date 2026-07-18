import json

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Header, Footer, TextArea, Log


class LobeTUI(App):
    """
    Vitrine de debug : panneau de prompt en lecture seule (gauche) + sortie
    streamée en direct (droite).

    Tourne sur la boucle asyncio de lobe_frontal elle-même (voir
    docs/adr/0001-embedded-tui-in-lobe-frontal.md). update_messages()/
    append_output() sont appelées directement depuis les callbacks NATS de
    main.py — même thread, même boucle, donc pas besoin de call_from_thread.
    """

    TITLE = "Lobe Frontal"
    CSS = """
    Horizontal { height: 1fr; }
    #input_panel, #output_panel { width: 1fr; border: solid $accent; }
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.input_panel: TextArea | None = None
        self.output_panel: Log | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield TextArea(id="input_panel", read_only=True, language="json")
            yield Log(id="output_panel")
        yield Footer()

    def on_mount(self) -> None:
        self.input_panel = self.query_one("#input_panel", TextArea)
        self.output_panel = self.query_one("#output_panel", Log)

    def update_messages(self, messages: list) -> None:
        # ponytail : garde-fou si un message arrive avant la fin du mount (improbable).
        if self.input_panel:
            self.input_panel.load_text(json.dumps(messages, indent=2, default=str))

    def append_output(self, text: str) -> None:
        if text and self.output_panel:
            self.output_panel.write(text)
