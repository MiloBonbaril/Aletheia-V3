import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import MASK, build_state, redact

# Faux secrets, de la forme des vrais. Aucun n'est valide. Les préfixes sont coupés en deux dans le
# source, pour que le scan de secrets de GitHub ne bloque pas un push.
SECRETS = {
    "openai": "s" "k-proj-4fQ9xT2mLp8vR1sK7wZ3bN6yH0cJ5dA",
    "github": "gh" "p_1a2B3c4D5e6F7g8H9i0JkLmNoPqRsTuVwXyZ",
    "github_pat": "github" "_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz",
    "aws": "AK" "IAIOSFODNN7EXAMPLE",
    "slack_bot": "xo" "xb-123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx",
    "slack_user": "xo" "xp-123456789012-1234567890123-AbCdEfGhIjKl",
    "jwt": "ey" "JhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    "base62": "Q7m2Zr9KxW4pL8vN3tB6yH1cJ5dF0gSa",
    "hex_sha1": "11f6ad8ec52a2984abaafd7c3b516503785c2072",
    "aws_secret": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "base64url": "u8Kq-3ZsT_x9VbNw2LmP4rYcHf7JdAe1",
}

ORDINARY = [
    "Blender",
    "Visual Studio Code",
    "Ajuste le rig du bras droit d'un personnage en Pose Mode.",
    "/home/milo/dev/Aletheia-V3/services/io_yeux/main.py",
    "~/.config/kwinrc",
    "ModuleNotFoundError: No module named 'service_view_desktop'",
    "gemma-4-12B-it-qat-UD-Q4_K_XL.gguf",
    "python3-dbus-next-0.2.3-x86_64",
    "very_long_identifier_name_2024_version",
    "anticonstitutionnellement",
    "550e8400-e29b-41d4-a716-446655440000",
    "task-runner --scikit-learn",
    "https://github.com/MiloBonbaril/Aletheia-V3/issues/38",
]


@pytest.mark.parametrize("name", SECRETS)
def test_each_secret_family_is_masked(name):
    secret = SECRETS[name]
    text = f"export TOKEN={secret} # {name}"
    assert secret not in redact(text)
    assert MASK in redact(text)


@pytest.mark.parametrize("text", ORDINARY)
def test_ordinary_text_passes_intact(text):
    assert redact(text) == text


def test_only_the_secret_is_masked_not_the_whole_text():
    assert redact(f"OPENAI_API_KEY={SECRETS['openai']} dans .env") == f"OPENAI_API_KEY={MASK} dans .env"


def test_build_state_masks_activity_and_each_visible_text():
    state = build_state(
        {
            "application": "Konsole",
            "activity": f"Affiche la clé {SECRETS['openai']} dans un terminal.",
            "visible_text": ["cat .env", f"GITHUB_TOKEN={SECRETS['github']}", SECRETS["jwt"]],
        },
        observed_at=1, trigger="change",
    )
    published = str(state)
    for secret in SECRETS.values():
        assert secret not in published
    assert state["visible_text"][0] == "cat .env"
    assert state["application"] == "Konsole"


def test_bounds_still_apply_after_masking():
    state = build_state(
        {"application": "A", "activity": f"{SECRETS['openai']} " + "x" * 300,
         "visible_text": [SECRETS["github"] + "y" * 200] * 9},
        observed_at=1, trigger="change",
    )
    assert state["activity"].startswith(MASK)
    assert len(state["activity"]) == 200
    assert len(state["visible_text"]) == 5
    assert all(len(text) <= 80 and text.startswith(MASK) for text in state["visible_text"])


def test_a_random_block_is_masked_even_after_a_slash():
    assert SECRETS["base62"] not in redact(f"curl https://api.example.com/{SECRETS['base62']}")
    assert SECRETS["base62"] not in redact(f"/{SECRETS['base62']}")


def test_the_password_of_a_connection_url_is_masked():
    assert redact("DATABASE_URL=postgres://admin:hunter2@db:5432/app") == f"DATABASE_URL=postgres://admin:{MASK}@db:5432/app"
