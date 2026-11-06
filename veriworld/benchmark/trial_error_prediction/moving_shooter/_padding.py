import random

_SUBJECTS = ["The committee", "The archivist", "The delegation", "The librarian",
             "The council", "The registrar", "The caretaker", "The chorus",
             "The gardener", "The steward"]

_VERBS = ["reviewed", "catalogued", "postponed", "discussed", "approved",
          "drafted", "rehearsed", "archived", "scheduled", "amended"]

_OBJECTS = ["the annual agenda", "the meeting minutes", "the seasonal program",
            "the visiting itinerary", "the membership roster", "the garden plan",
            "the lecture series", "the archive inventory", "the festival notes",
            "the travel arrangements"]

_TAILS = ["before the recess concluded", "while the hall remained quiet",
          "after a lengthy deliberation", "as the afternoon session ended",
          "and adjourned until the following week", "despite the crowded calendar",
          "once the correspondence was filed", "with little further debate",
          "and noted the matter for later review", "as was customary each term"]

def make_filler_natural(seed: int, n_words: int) -> str:
    """Deterministic coherent prose of ~n_words words."""
    rng = random.Random(0xFACE ^ seed)
    words: list[str] = []
    while len(words) < n_words:
        s = (f"{rng.choice(_SUBJECTS)} {rng.choice(_VERBS)} "
             f"{rng.choice(_OBJECTS)} {rng.choice(_TAILS)}.")
        words.extend(s.split())
    return " ".join(words[:n_words]).rstrip(".,") + "."
