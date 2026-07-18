import json
import asyncio

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Header, Footer, TextArea, Log, Static


def next_effort(current: str, options: list[str]) -> str:
    """Étant donné l'effort courant et la liste réellement supportée par le modèle
    connecté, quel est le suivant (cycle circulaire) ? Fonction pure, testable sans
    TUI (voir CONTEXT.md #Testing Decisions). Si current n'est pas dans options,
    repart de options[0] ; une liste vide laisse current inchangé (rien à cycler)."""
    if not options:
        return current
    try:
        idx = (options.index(current) + 1) % len(options)
    except ValueError:
        idx = 0
    return options[idx]


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
    #status_bar, #effort_bar { height: 1; background: $panel; }
    """
    BINDINGS = [
        ("e", "cycle_effort", "Effort suivant"),
        ("r", "reconnect", "Reconnexion"),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.input_panel: TextArea | None = None
        self.output_panel: Log | None = None
        self.status_bar: Static | None = None
        self.effort_bar: Static | None = None
        self._effort_options: list[str] = []
        self.current_effort: str = "default"
        self.connected: bool = False
        self._disconnect_detail: str = ""
        # ponytail : alert() peut être appelée avant on_mount() (ex. échec de connexion à
        # llama.cpp au cold start, voir main.refresh_inference_connection) alors que
        # status_bar n'existe pas encore ; on_mount() applique ce message en attente au
        # lieu d'écraser silencieusement avec "(aucune alerte)".
        self._pending_status: str | None = None
        # ponytail : câblé par main.py après construction (voir refresh_inference_connection) ;
        # reste None si jamais utilisé hors main.py (ex. tests/pilot qui instancient LobeTUI seul).
        self.on_reconnect = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield TextArea(id="input_panel", read_only=True, language="json")
            yield Log(id="output_panel")
        yield Static(id="effort_bar")
        yield Static(id="status_bar")
        yield Footer()

    def on_mount(self) -> None:
        self.input_panel = self.query_one("#input_panel", TextArea)
        self.output_panel = self.query_one("#output_panel", Log)
        self.status_bar = self.query_one("#status_bar", Static)
        self.status_bar.update(self._pending_status or "(aucune alerte)")
        self.effort_bar = self.query_one("#effort_bar", Static)
        self._refresh_effort_bar()

    def update_messages(self, messages: list) -> None:
        # ponytail : garde-fou si un message arrive avant la fin du mount (improbable).
        if self.input_panel:
            self.input_panel.load_text(json.dumps(messages, indent=2, default=str))

    def append_output(self, text: str) -> None:
        if text and self.output_panel:
            self.output_panel.write(text)

    def alert(self, message: str, severity: str = "warning") -> None:
        """Point de passage unique pour un warning/erreur : toast transitoire + barre de statut persistante."""
        self.notify(message, severity=severity)
        icon = "✖" if severity == "error" else "⚠"
        text = f"{icon} {message}"
        if self.status_bar:
            self.status_bar.update(text)
        else:
            self._pending_status = text

    def set_effort_options(self, options: list[str], initial: str) -> None:
        """Reçoit les reasoning_efforts réels du modèle connecté (get_model_details(), qui
        garantit une liste non vide — pas de fallback local ici, cf. CONTEXT.md Out-of-Scope).
        ponytail : initial est conservé tel quel même hors-liste (ex. valeur .env qui ne
        correspond à aucune valeur réelle) pour ne pas changer le comportement du premier
        tour ; le cycle ne s'aligne sur la liste qu'au premier appui sur 'e'."""
        self._effort_options = list(options)
        self.current_effort = initial
        self.connected = True
        self._refresh_effort_bar()

    def set_disconnected(self, detail: str = "") -> None:
        """Reflète, dans la barre d'effort, l'échec du dernier appel de découverte de modèle
        (cold start avant que llama.cpp soit prêt, ou serveur tombé en cours de session).
        ponytail : pas de barre dédiée — la barre d'effort n'a pas d'autre rôle pendant une
        déconnexion ; se corrige au prochain set_effort_options() (reconnexion via 'r')."""
        self.connected = False
        self._disconnect_detail = detail
        self._refresh_effort_bar()

    def action_cycle_effort(self) -> None:
        self.current_effort = next_effort(self.current_effort, self._effort_options)
        self._refresh_effort_bar()

    def action_reconnect(self) -> None:
        if self.on_reconnect:
            asyncio.create_task(self.on_reconnect())

    def _refresh_effort_bar(self) -> None:
        if not self.effort_bar:
            return
        if not self.connected:
            detail = f" ({self._disconnect_detail})" if self._disconnect_detail else ""
            self.effort_bar.update(f"🔌 Déconnecté du serveur d'inférence{detail}")
        else:
            self.effort_bar.update(f"Effort de raisonnement : {self.current_effort}")


def _self_check():
    assert next_effort("low", ["none", "low", "medium", "high"]) == "medium"
    assert next_effort("high", ["none", "low", "medium", "high"]) == "none"  # wrap-around
    assert next_effort("default", ["none", "low", "medium", "high"]) == "none"  # valeur absente de la liste
    assert next_effort("none", []) == "none"  # liste vide -> rien à cycler
    assert next_effort("only", ["only"]) == "only"  # liste à un seul élément
    print("OK: next_effort self-check passed")


if __name__ == "__main__":
    _self_check()
