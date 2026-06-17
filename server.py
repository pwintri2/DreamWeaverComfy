#!/usr/bin/env python3
"""Dreamweaver local web app with a ComfyUI bridge.

The app is intentionally local-first: it never sends the user's original
desire to ComfyUI. It derives auditable metaphor phrases and visual prompts,
then queues the visual prompt against the models already configured in the
local ComfyUI installation.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import io
import json
import mimetypes
import os
import random
import re
import bisect
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any
import xml.etree.ElementTree as ET


APP_DIR = Path(__file__).resolve().parent
APP_VERSION = "0.2.9"
DEFAULT_COMFY_PATH = Path(os.environ.get("COMFYUI_PATH", "/home/pwintri2/ComfyUI"))
DEFAULT_COMFY_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_PLANNER_TIMEOUT = float(os.environ.get("OLLAMA_PLANNER_TIMEOUT", "180"))
OLLAMA_PANEL_PROMPT_TIMEOUT = float(os.environ.get("OLLAMA_PANEL_PROMPT_TIMEOUT", "60"))
COMFY_IMAGE_TIMEOUT = float(os.environ.get("DREAMWEAVER_COMFY_IMAGE_TIMEOUT", "600"))
COMFY_VIDEO_TIMEOUT = float(os.environ.get("DREAMWEAVER_COMFY_VIDEO_TIMEOUT", "3600"))
COMFY_MISSING_HISTORY_GRACE = float(os.environ.get("DREAMWEAVER_COMFY_MISSING_HISTORY_GRACE", "20"))

LLM_ENGINE_TYPES = {"ollama", "openai", "anthropic", "google", "xai"}
DEFAULT_OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
DEFAULT_ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
DEFAULT_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
DEFAULT_GROK_MODEL = os.environ.get("GROK_MODEL", os.environ.get("XAI_MODEL", "grok-4.3"))
XAI_API_BASE_URL = os.environ.get("XAI_API_BASE_URL", "https://api.x.ai/v1").rstrip("/")
CLOUD_PLANNER_IDS = {
    "openai:env": ("openai", DEFAULT_OPENAI_MODEL),
    "anthropic:env": ("anthropic", DEFAULT_ANTHROPIC_MODEL),
    "gemini:env": ("google", DEFAULT_GEMINI_MODEL),
    "grok:env": ("xai", DEFAULT_GROK_MODEL),
}
JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()
COMFY_PROCESS: subprocess.Popen[str] | None = None
FRAME_CACHE_DIR = Path(os.environ.get("DREAMWEAVER_FRAME_CACHE", "/tmp/dreamweaver-comfy-frames"))
VIDEO_FRAME_LIMIT = 48
DATA_DIR = Path(os.environ.get("DREAMWEAVER_DATA_DIR", str(APP_DIR / "data")))
SECRETS_FILE = DATA_DIR / "secrets.json"
SECRETS_LOCK = threading.Lock()

# Provider catalog for the API-keys page. envVar is the legacy env fallback; keys saved in the
# UI are stored locally in SECRETS_FILE (gitignored, chmod 600) and take precedence over env.
PROVIDER_CATALOG: list[dict[str, str]] = [
    {"id": "openai", "label": "OpenAI", "envVar": "OPENAI_API_KEY", "hint": "sk-...", "docs": "https://platform.openai.com/api-keys"},
    {"id": "anthropic", "label": "Anthropic (Claude)", "envVar": "ANTHROPIC_API_KEY", "hint": "sk-ant-...", "docs": "https://console.anthropic.com/settings/keys"},
    {"id": "google", "label": "Google Gemini", "envVar": "GEMINI_API_KEY", "hint": "AIza...", "docs": "https://aistudio.google.com/app/apikey"},
    {"id": "xai", "label": "xAI Grok", "envVar": "XAI_API_KEY", "hint": "xai-...", "docs": "https://console.x.ai/"},
    {"id": "replicate", "label": "Replicate", "envVar": "REPLICATE_API_TOKEN", "hint": "r8_...", "docs": "https://replicate.com/account/api-tokens"},
]
PROVIDER_BY_ID = {provider["id"]: provider for provider in PROVIDER_CATALOG}
VIDEO_FRAME_MAX_EDGE = 960
COMIC_WORD_LIMIT = 50_000
COMIC_ANALYSIS_CHUNK_TARGET_WORDS = 1_600
COMIC_ANALYSIS_CHUNK_OVERLAP_SENTENCES = 2
COMIC_SCENE_TARGET_WORDS = 230
COMIC_PANEL_TARGET_WORDS = 80
COMIC_MAX_PANELS_PER_PAGE = 4
COMIC_LLM_SET_REVIEW_MAX_SETS = int(os.environ.get("DREAMWEAVER_LLM_SET_REVIEW_MAX_SETS", "24"))
DOCUMENT_UPLOAD_MAX_BYTES = 35 * 1024 * 1024
LOCAL_MODEL_EXTENSIONS = {".safetensors", ".ckpt", ".pt", ".pth", ".bin"}
COMIC_NEGATIVE_PROMPT = (
    "watermark, readable text, letters, captions, speech bubbles, subtitles, logo, "
    "low quality, blurry, distorted face, deformed hands, extra fingers, missing fingers, "
    "extra limbs, fused limbs, duplicate character, inconsistent character design, nsfw, nude, "
    "extra unnamed person, background stranger, unlisted face, stray silhouette, unwanted extra character"
)


NAME_BLOCKLIST = {
    "Aan", "Achter", "Als", "Alsof", "And", "Bij", "Boven", "Daar", "Daarna", "Dan", "Dat", "De",
    "Deze", "Die", "Dit", "Door", "Een", "En", "Er", "For", "Geen", "Het",
    "Hij", "Hoe", "Hun", "Ik", "In", "Is", "Later", "Maar", "Met", "Na",
    "Naast", "Niet", "Of", "Om", "Omdat", "Onder", "Ondertussen", "Op", "Over", "The", "Toen", "Tot",
    "Tussen", "Uit", "Van", "Voor", "Waar", "Wat", "We", "Without", "Ze", "Zij", "Zijn", "Zonder",
    # Pronouns that often appear capitalized at start of sentences or in dialogue - never character names
    "They", "You", "Me", "He", "She", "Us", "Them", "It", "My", "Your", "His", "Her", "Their", "Our", "Its", "I",
    "Jij", "Jou", "Wij", "Ons",
}

NON_CHARACTER_NAME_WORDS = {
    "about", "above", "after", "again", "against", "all", "alone", "also", "although", "always", "an", "and",
    "apartment", "around", "as", "at", "away", "back", "balcony", "bed", "before", "behind", "below",
    "between", "book", "box", "bridge", "building", "but", "camera", "car", "chair", "city", "clock",
    "corridor", "couch", "cup", "darkness", "day", "desk", "door", "down", "each", "elevator", "every",
    "everything", "floor", "for", "from", "garden", "gate", "hall", "hallway", "hand", "hands", "here", "home",
    "house", "inside", "into", "key", "kitchen", "letter", "light", "look", "looks", "looking", "moon",
    "morning", "night", "no", "nothing", "now", "of", "off", "office", "old", "once", "only", "open",
    "opens", "outside", "over", "paper", "phone", "room", "shadow", "shadows", "silence", "sky", "something", "stairs",
    "street", "table", "that", "the", "then", "there", "thing", "things", "this", "through", "to", "today",
    "tomorrow", "under", "up", "wall", "window", "with", "without",
    "are", "was", "were", "been", "being", "does", "did", "will", "would", "can", "could", "shall",
    "should", "may", "might", "must", "have", "has", "had", "why", "what", "when", "where", "who",
    "whom", "whose", "which", "how", "come", "comes", "goes", "let", "lets", "please", "yes",
    "okay", "sure", "maybe", "perhaps", "well", "hey", "hello", "wait", "stop", "really", "sorry",
    "actie", "achter", "alles", "appartement", "bank", "bed", "boek", "boven", "brief", "brug", "deur",
    "ding", "dingen", "gang", "gebouw", "gisteren", "huis", "kamer", "keuken", "kijk", "kijkt", "kijken",
    "klok", "licht", "lucht", "maan", "morgen", "muur", "nacht", "niets", "nu", "onder", "poort",
    "sleutel", "stad", "stoel", "straat", "tafel", "telefoon", "trap", "tuin", "vandaag", "vloer",
    "voor", "weg", "woning",
    "voice", "voices", "echo", "echoes", "figure", "figures", "silhouette", "silhouettes", "shade", "shades",
    "dream", "dreams", "memory", "memories", "thought", "thoughts", "idea", "ideas", "fear", "fears",
    "hope", "hopes", "soul", "spirit", "ghost", "phantom", "machine", "device", "robot", "computer",
    "time", "moment", "instant", "hour", "minute", "second", "dawn", "dusk", "evening", "noon", "midnight",
    "future", "past", "present", "fate", "destiny", "death", "life", "love", "world", "earth", "universe",
    "god", "gods", "angel", "demon", "devil", "wind", "rain", "storm", "fire", "flame", "wave", "sea",
    "sun", "moon", "star", "stars", "cloud", "clouds", "creature", "being",
    # Pronouns (lowercase) - must never become character names
    "they", "them", "their", "theirs", "you", "your", "yours", "he", "him", "his", "she", "her", "hers",
    "we", "us", "our", "ours", "me", "my", "mine", "i", "it", "its",
    "jij", "jou", "jouw", "wij", "ons", "onze", "mij", "mijn", "ik",
    # Reliable non-person brands/systems (Muzak is almost never a character name)
    "muzak", "spotify",
}

PERSON_ACTION_WORDS = {
    "answers", "answered", "asks", "asked", "breathes", "cries", "enters", "entered", "feels", "felt",
    "follows", "followed", "grabs", "grabbed", "hears", "heard", "holds", "held", "laughs", "laughed",
    "leaves", "left", "listens", "listened", "looks", "looked", "opens", "opened", "replies", "replied",
    "runs", "ran", "says", "said", "sees", "saw", "shouts", "shouted", "sits", "sat", "smiles", "smiled",
    "speaks", "spoke", "stands", "stood", "thinks", "thought", "turns", "turned", "walks", "walked",
    "watches", "watched", "waits", "waited", "whispers", "whispered",
    "antwoordt", "antwoordde", "ademt", "denkt", "dacht", "fluistert", "fluisterde", "gaat", "ging",
    "grijpt", "greep", "hoort", "hoorde", "huilt", "keert", "keek", "kijkt", "lacht", "liep", "loopt",
    "luistert", "opent", "opende", "pakt", "pakte", "rent", "rende", "roept", "riep", "staat", "stond",
    "vertrekt", "vertrok", "vraagt", "vroeg", "wacht", "wachtte", "zegt", "zei", "ziet", "zag", "zit", "zat",
    # more common actions for better character detection
    "try", "tries", "tried", "manage", "manages", "managed",
    "carry", "carried", "carries", "close", "closed", "closes", "find", "finds", "found", "lift", "lifted", "lifts",
    "read", "decide", "decides", "decided", "appear", "appears", "appeared", "warn", "warns", "warned", "keep",
    "keeps", "kept", "study", "studies", "studied", "discover", "discovers", "discovered", "meet", "meets", "met",
    "talk", "talks", "talked", "write", "writes", "wrote", "learn", "learns", "learned", "realize", "realizes",
    "realized", "choose", "chooses", "chose", "pick", "picks", "picked", "take", "takes", "took", "give", "gives",
    "gave", "show", "shows", "showed", "tell", "tells", "told", "stay", "stays", "stayed",
    "search", "searches", "searched", "hide", "hides", "hid", "fight", "fights", "fought", "jump", "jumps",
    "jumped", "climb", "climbs", "climbed", "push", "pushes", "pushed", "pull", "pulls", "pulled", "throw",
    "throws", "threw", "catch", "catches", "caught", "understand", "understands", "understood",
    "know", "knows", "knew", "believe", "believes", "believed", "hope", "hopes", "hoped", "fear", "fears",
    "feared", "love", "loves", "loved", "hate", "hates", "hated", "help", "helps", "helped", "save", "saves",
    "saved", "kill", "kills", "killed", "die", "dies", "died", "live", "lives", "lived",
    "droeg", "draagt", "tilde", "tilt", "vond", "las", "besloot", "verscheen", "waarschuwde", "bewaarde", "bestudeerde", "ontdekte", "ontmoette", "praatte",
    "opende", "schreef", "leerde", "besefte", "koos", "pakte", "nam", "gaf", "toonde", "vertelde",
}

ATTRIBUTION_WORDS = {
    "asked", "asks", "called", "calls", "replied", "replies", "said", "says", "shouted", "shouts",
    "whispered", "whispers", "antwoordde", "antwoordt", "fluisterde", "fluistert", "riep", "roept",
    "vroeg", "vraagt", "zei", "zegt",
}

HUMAN_SIGNAL_WORDS = {
    "boy", "brother", "child", "children", "daughter", "father", "friend", "girl", "he", "her", "hers",
    "him", "his", "human", "i", "man", "me", "mother", "person", "people", "she", "sister", "son",
    "they", "them", "woman",
    "broer", "dochter", "hij", "haar", "hem", "hun", "ik", "jongen", "kind", "kinderen", "man",
    "me", "meisje", "mens", "mensen", "moeder", "persoon", "vader", "vriend", "vrouw", "ze", "zij", "zoon",
}


HAIR_OPTIONS = [
    "dark wavy hair",
    "short black hair",
    "chestnut shoulder-length hair",
    "silver-gray hair",
    "deep brown tied-back hair",
    "soft blond hair",
    "auburn hair",
    "raven-black hair with a side part",
]


OUTFIT_OPTIONS = [
    "a charcoal coat with a pale shirt",
    "a navy jacket and simple dark trousers",
    "a cream sweater under a long green coat",
    "a black high-collar jacket",
    "a burgundy hoodie under a worn denim jacket",
    "a white blouse and muted brown vest",
    "a dark blue dress coat with practical boots",
    "a gray work jacket with a small brass pin",
]


MARKER_OPTIONS = [
    "calm almond-shaped eyes",
    "a small scar near the eyebrow",
    "round glasses",
    "sharp focused eyes",
    "soft tired eyes",
    "a narrow silver necklace",
    "a red scarf",
    "freckles and expressive eyebrows",
]


SHOT_SEQUENCE = [
    "wide establishing shot",
    "medium shot",
    "over-the-shoulder shot",
    "close-up on the emotional reaction",
    "low-angle action shot",
    "quiet cinematic profile shot",
    "top-down composition",
    "dramatic three-quarter view",
]


MOOD_KEYWORDS = {
    "ominous": ["fear", "afraid", "threat", "danger", "dark", "darkness", "shadow", "flee", "storm", "dread", "angst", "bang", "dreiging", "gevaar", "donker", "schaduw", "vlucht"],
    "intimate": ["whisper", "close", "love", "warm", "quiet", "touch", "tender", "trust", "embrace", "fluister", "dichtbij", "liefde", "stil", "aanraking", "vertrouwen"],
    "melancholic": ["sad", "sorrow", "grief", "loss", "mourning", "mist", "rain", "alone", "lonely", "longing", "verdriet", "verlies", "rouw", "regen", "alleen", "heimwee"],
    "energetic": ["run", "running", "leap", "jump", "action", "fight", "shout", "explosion", "rush", "chase", "rennen", "sprong", "actie", "gevecht", "schreeuw", "haast"],
    "wondrous": ["light", "wonder", "star", "magic", "discover", "glow", "shimmer", "gate", "licht", "wonder", "ster", "magie", "glans", "poort"],
    "tense": ["silence", "wait", "waiting", "doubt", "choice", "secret", "door", "step", "stilte", "wacht", "twijfel", "keuze", "geheim", "deur", "stap"],
}


EXIT_KEYWORDS = [
    "walks out", "walked out", "walks away", "walked away", "walks off", "walked off",
    "leaves", "left", "departs", "departed", "goes away", "went away",
    "runs away", "ran away", "runs off", "ran off", "steps out", "stepped out",
    "exits", "exited", "disappears", "disappeared", "vanishes", "vanished",
    "is gone", "was gone", "no longer visible", "out the door", "out of the room",
    "gaat weg", "ging weg", "weggaat", "vertrekt", "vertrok", "verlaat", "verliet",
    "verdwijnt", "verdween", "loopt weg", "liep weg", "rent weg", "rende weg",
    "stapt weg", "stapte weg", "vlucht", "vluchtte", "is weg", "was weg",
    "niet meer zichtbaar", "verdwenen",
]


ABSENCE_KEYWORDS = [
    "without", "not with", "no longer visible", "no longer present", "not visible",
    "not present", "is gone", "was gone", "gone", "absent",
    "zonder", "niet bij", "niet meer zichtbaar", "niet meer aanwezig",
    "niet zichtbaar", "niet aanwezig", "is weg", "was weg", "verdwenen",
]

ABSENCE_STATE_KEYWORDS = [
    "no longer visible", "no longer present", "not visible", "not present",
    "is gone", "was gone", "gone", "absent",
    "niet meer zichtbaar", "niet meer aanwezig", "niet zichtbaar", "niet aanwezig",
    "is weg", "was weg", "verdwenen",
]

EMPTY_SCENE_CUES = [
    "empty room", "empty street", "empty hallway", "empty space", "empty",
    "no one", "no one inside", "no people", "no person", "nobody",
    "abandoned", "deserted", "left behind", "all alone", "alone in the",
    "alleen de kamer", "alleen de straat", "geen mens", "geen mensen",
    "geen personage", "geen personages", "leeg", "niemand", "verlaten achter",
]

FIRST_PERSON_WORDS = {"i", "ik", "me", "mij", "mijn", "my", "mine", "we", "wij", "ons", "onze", "our", "ours"}
FEMALE_PRONOUN_WORDS = {"she", "her", "hers", "zij", "ze", "haar"}
MALE_PRONOUN_WORDS = {"he", "him", "his", "hij", "hem", "zijn"}
PLURAL_PRONOUN_WORDS = {"they", "them", "their", "theirs", "zij", "ze", "hun", "hen"}
PRONOUN_WORDS = FEMALE_PRONOUN_WORDS | MALE_PRONOUN_WORDS | PLURAL_PRONOUN_WORDS
FEMALE_SIGNAL_WORDS = {"she", "her", "hers", "woman", "girl", "mother", "daughter", "zij", "ze", "haar", "vrouw", "meisje", "moeder", "dochter"}
MALE_SIGNAL_WORDS = {"he", "him", "his", "man", "boy", "father", "son", "hij", "hem", "zijn", "jongen", "man", "vader", "zoon"}
RELATION_LABELS = {
    "brother": "brother",
    "sister": "sister",
    "mother": "mother",
    "father": "father",
    "daughter": "daughter",
    "son": "son",
    "parent": "parent",
    "child": "child",
    "friend": "friend",
    "partner": "partner",
    "wife": "wife",
    "husband": "husband",
    "mentor": "mentor",
    "teacher": "teacher",
    "student": "student",
    "captain": "captain",
    "doctor": "doctor",
    "broer": "brother",
    "zus": "sister",
    "moeder": "mother",
    "vader": "father",
    "dochter": "daughter",
    "zoon": "son",
    "ouder": "parent",
    "kind": "child",
    "vriend": "friend",
    "vriendin": "friend",
    "partner": "partner",
    "vrouw": "wife",
    "man": "husband",
    "mentor": "mentor",
    "leraar": "teacher",
    "lerares": "teacher",
    "student": "student",
    "kapitein": "captain",
    "dokter": "doctor",
}
FEMALE_NAME_HINTS = {
    "anna", "eva", "emma", "lisa", "mara", "maria", "olga", "sara", "sarah", "wendy",
}
MALE_NAME_HINTS = {
    "adam", "alex", "david", "jan", "leo", "lucas", "mark", "max", "noah", "peter", "sam", "tom",
}

VISUAL_OBJECT_KEYWORDS = {
    "brief": "letter",
    "boek": "book",
    "doos": "box",
    "foto": "photo",
    "glas": "glass",
    "kaart": "map/card",
    "kaars": "candle",
    "koffer": "suitcase",
    "lamp": "lamp",
    "mes": "knife",
    "notitieboek": "notebook",
    "poort": "gate",
    "ring": "ring",
    "sleutel": "key",
    "spiegel": "mirror",
    "tas": "bag",
    "telefoon": "phone",
    "trein": "train",
    "deur": "door",
    "window": "window",
    "letter": "letter",
    "book": "book",
    "box": "box",
    "candle": "candle",
    "door": "door",
    "gate": "gate",
    "glass": "glass",
    "key": "key",
    "knife": "knife",
    "map": "map",
    "mirror": "mirror",
    "notebook": "notebook",
    "phone": "phone",
    "photo": "photo",
    "ring": "ring",
    "suitcase": "suitcase",
    "train": "train",
}

LOCATION_KEYWORDS = {
    "appartement": "appartement",
    "bos": "bos",
    "brug": "brug",
    "gang": "gang",
    "huis": "huis",
    "kamer": "kamer",
    "keuken": "keuken",
    "station": "station",
    "stad": "stad",
    "straat": "straat",
    "tuin": "tuin",
    "apartment": "apartment",
    "bridge": "bridge",
    "city": "city",
    "corridor": "corridor",
    "forest": "forest",
    "garden": "garden",
    "hallway": "hallway",
    "home": "home",
    "house": "house",
    "kitchen": "kitchen",
    "room": "room",
    "station": "station",
    "street": "street",
}


class ComfyUnavailableError(RuntimeError):
    """Raised when the configured ComfyUI server is not reachable."""


STOPWORDS = {
    "aan", "als", "and", "are", "ben", "bij", "but", "can", "dat", "de",
    "een", "en", "for", "het", "hun", "i", "ich", "ik", "in", "is", "it",
    "me", "met", "mijn", "of", "om", "op", "or", "te", "the", "to", "van",
    "voor", "want", "wil", "with", "you", "your", "zijn",
}


ARCHETYPES: dict[str, dict[str, list[str]]] = {
    "confidence": {
        "keys": ["confidence", "confident", "zeker", "zelfvertrouwen", "moed", "kracht", "sterk"],
        "phrases": [
            "golden spine rises",
            "stone heart steady",
            "solar crown opens",
            "clear voice returns",
            "iron roots deepen",
            "morning tower stands",
            "bright shoulders widen",
            "quiet thunder answers",
        ],
        "scenes": [
            "a sunlit monolith above silver mist, cinematic surreal dream art",
            "golden architecture emerging from a calm black ocean, soft volumetric light",
            "a marble stair ascending through warm clouds, painterly luminous atmosphere",
            "a solitary flame reflected in polished obsidian, elegant dreamlike motion",
        ],
    },
    "peace": {
        "keys": ["peace", "calm", "rust", "kalm", "stilte", "balans", "ontspan"],
        "phrases": [
            "moon lake breathes",
            "quiet garden opens",
            "soft blue silence",
            "silver tide settles",
            "gentle dusk holds",
            "still water remembers",
            "cloud bells fade",
            "velvet horizon rests",
        ],
        "scenes": [
            "a moonlit lake with slow ripples and pale floating lanterns, surreal soft focus",
            "a quiet midnight garden under blue glass rain, beautiful cinematic dreamscape",
            "silver fog flowing through cypress trees, serene otherworldly atmosphere",
            "smooth stones beneath transparent water, luminous reflections, no readable text",
        ],
    },
    "protection": {
        "keys": ["bescherm", "bescherming", "veilig", "veiligheid", "grens", "grenzen", "warm", "warme", "omsluit", "omhult", "cocoon", "schild", "huid"],
        "phrases": [
            "soft boundary holds",
            "warm shell opens",
            "silver skin breathes",
            "safe orbit forms",
            "velvet shield listens",
            "quiet room seals",
            "gentle walls glow",
            "clear edge returns",
        ],
        "scenes": [
            "a translucent protective cocoon floating in warm dusk light, surreal cinematic dream art",
            "soft glass membranes folding around a glowing inner room, symbolic dream imagery",
            "a luminous sanctuary shell above dark water, warm reflections, no readable text",
            "silk-like protective rings orbiting a calm center, elegant otherworldly atmosphere",
        ],
    },
    "prosperity": {
        "keys": ["success", "succes", "money", "geld", "rijk", "wealth", "overvloed", "carriere", "werk"],
        "phrases": [
            "golden harvest turns",
            "open gates gleam",
            "amber river flows",
            "bright keys gather",
            "orchard lamps awaken",
            "sun coins spiral",
            "clear path widens",
            "morning market blooms",
        ],
        "scenes": [
            "a radiant orchard of glass fruit under amber sunrise, luxurious surreal artwork",
            "golden rivers crossing a dark velvet valley, cinematic elegant dream imagery",
            "open bronze gates leading to a luminous city, soft haze, no symbols or text",
            "constellations forming a bright path over a quiet harbor, high detail dream art",
        ],
    },
    "love": {
        "keys": ["love", "liefde", "relatie", "heart", "hart", "verbinding", "partner", "warmte"],
        "phrases": [
            "rose lanterns listen",
            "twin stars soften",
            "warm door opens",
            "honey light gathers",
            "gentle hands glow",
            "soft orbit returns",
            "ember garden sings",
            "kind horizon nears",
        ],
        "scenes": [
            "two warm stars reflected in rose colored water, tender surreal dreamscape",
            "a glowing doorway in a garden of dusk flowers, cinematic gentle atmosphere",
            "floating silk ribbons crossing in golden air, beautiful soft motion, no text",
            "an ember-lit conservatory with luminous petals and deep violet shadows",
        ],
    },
    "health": {
        "keys": ["health", "gezond", "genezing", "heal", "energie", "fit", "body", "lichaam"],
        "phrases": [
            "green pulse renews",
            "clear spring rises",
            "bright cells dance",
            "living moss glows",
            "fresh dawn circulates",
            "jade roots repair",
            "clean river hums",
            "vital leaves unfold",
        ],
        "scenes": [
            "bioluminescent leaves unfolding around a crystal spring, healing dreamlike art",
            "emerald light moving through transparent roots, cinematic organic abstraction",
            "a clean mountain stream under morning mist, luminous surreal atmosphere",
            "soft jade particles swirling through a dark botanical temple, no readable text",
        ],
    },
    "default": {
        "keys": [],
        "phrases": [
            "hidden dawn unfolds",
            "opal doors awaken",
            "quiet stars align",
            "velvet signal blooms",
            "mirror rain dissolves",
            "soft fire returns",
            "blue gold rises",
            "silent orbit opens",
        ],
        "scenes": [
            "surreal opal clouds moving above a black mirror lake, cinematic dream artwork",
            "floating luminous islands in violet night, soft fog, high detail, no readable text",
            "abstract silk auroras over dark water, beautiful seamless dream loop",
            "a slow spiral of starlight through translucent glass petals, elegant fantasy art",
        ],
    },
}


VISUAL_ENVIRONMENTS = [
    "a mirror lake under a moving night sky",
    "a glass greenhouse inside moonlit fog",
    "a black sand shore with luminous tide pools",
    "a floating temple of translucent stone",
    "a deep blue canyon filled with slow golden mist",
    "a quiet interior room opening into stars",
    "a rain-washed garden of glowing mineral flowers",
    "a dark ocean horizon with warm light beneath the surface",
    "a circular chamber of water, silk, and distant constellations",
    "a high mountain pass where clouds move like fabric",
]

VISUAL_MATERIALS = [
    "opal glass",
    "liquid gold",
    "smoky quartz",
    "soft bioluminescent silk",
    "polished obsidian",
    "transparent jade",
    "warm amber resin",
    "silver mist",
    "pearl-like water",
    "blue fire",
]

VISUAL_SYMBOLS = [
    "a doorway",
    "a spiral key",
    "a seed of light",
    "a floating vessel",
    "a luminous bridge",
    "a protective shell",
    "a constellation wheel",
    "a folded ribbon",
    "a quiet tower",
    "a living mirror",
    "an opening flower",
    "a ring of lanterns",
]

CATEGORY_SYMBOLS = {
    "confidence": ["a rising tower", "a golden spine", "a solar crown", "a clear flame"],
    "peace": ["a breathing lake", "a moon garden", "a silver bowl of water", "a soft blue gate"],
    "protection": ["a warm protective shell", "a translucent cocoon", "a soft boundary ring", "a luminous inner room"],
    "prosperity": ["an opening bronze gate", "a golden orchard", "a river of amber light", "a bright path of keys"],
    "love": ["two orbiting lights", "a rose doorway", "a warm conservatory", "a pair of gentle stars"],
    "health": ["a crystal spring", "a green pulse of light", "living roots", "unfolding jade leaves"],
}

VISUAL_MOTIONS = [
    "visible flowing motion from foreground to horizon",
    "the symbol slowly unfolds and reforms",
    "soft parallax layers drift in opposite directions",
    "light travels through the scene in waves",
    "the environment breathes and changes shape",
    "particles gather into the symbol and dissolve again",
    "the camera glides forward while the scene transforms",
    "rings expand outward and reveal a new inner landscape",
]

VISUAL_TRANSITIONS = [
    "from shadow to warm clarity",
    "from closed form to open space",
    "from scattered particles to one calm pattern",
    "from heavy stillness to fluid motion",
    "from dark water to luminous reflection",
    "from fog to a clear symbolic center",
    "from contraction to expansion",
    "from fragmented light to a steady path",
]

VISUAL_PALETTES = [
    "deep indigo, pearl white, and warm amber",
    "emerald green, black glass, and soft gold",
    "midnight blue, rose light, and silver mist",
    "violet dusk, cyan sparks, and obsidian shadows",
    "jade light, moonlit gray, and clean water tones",
    "warm copper, smoky blue, and soft cream highlights",
    "opal pink, dark teal, and luminous white",
    "golden sunrise, black water, and pale violet haze",
]

CATEGORY_MESSAGES = {
    "confidence": [
        "inner strength becomes visible and steady",
        "the body remembers upright calm",
        "clear action rises from quiet certainty",
    ],
    "peace": [
        "the system settles into safe rhythm",
        "calm returns through breath, space, and water",
        "noise dissolves into a quiet center",
    ],
    "protection": [
        "a gentle boundary forms without closing the heart",
        "warm safety surrounds the inner self",
        "the field learns what may enter and what may pass by",
    ],
    "prosperity": [
        "available paths open with grounded ease",
        "resources move toward the prepared center",
        "small signals gather into visible opportunity",
    ],
    "love": [
        "connection approaches without force",
        "warmth becomes safe to receive and return",
        "separate lights find a gentle shared orbit",
    ],
    "health": [
        "renewal moves through the whole field",
        "the body image turns toward repair and vitality",
        "clean energy circulates through living roots",
    ],
    "default": [
        "the wish becomes a symbolic moving landscape",
        "hidden pressure transforms into visible motion",
        "the inner image changes shape until it feels complete",
    ],
}

SYMBOLIC_THEMES = {
    "transformation": {
        "keys": ["verander", "transformatie", "worden", "groei", "nieuw", "begin", "anders", "shift", "change"],
        "symbols": ["a chrysalis of light", "a turning spiral door", "a seed splitting open", "a river changing course"],
        "motions": ["the old shape melts and reforms into a clearer one", "layers peel away and reveal a brighter center"],
        "messages": [
            ("the inner image changes shape until it feels complete", "Het innerlijke beeld verandert van vorm tot het klopt."),
            ("what was fixed becomes fluid and available", "Wat vastzat wordt vloeibaar en beschikbaar."),
        ],
    },
    "release": {
        "keys": ["los", "loslaten", "vrij", "vrijheid", "stop", "vast", "druk", "moe", "moeheid", "stress", "angst", "worry", "fear", "release"],
        "symbols": ["a knot of light untying", "a dark cloak falling into water", "a broken ring opening", "a cloud of ash becoming stars"],
        "motions": ["heavy particles lift away from the center", "constriction opens into wide breathing space"],
        "messages": [
            ("the system releases what no longer has to be carried", "Het systeem laat los wat niet meer gedragen hoeft te worden."),
            ("pressure leaves the body image and becomes open space", "Druk verlaat het lichaamsbeeld en wordt open ruimte."),
        ],
    },
    "clarity": {
        "keys": ["helder", "duidelijk", "focus", "richting", "keuze", "kiezen", "weten", "begrijp", "inzicht", "clarity", "choice"],
        "symbols": ["a clear lens", "a path of white stones", "a lantern map", "a mirror turning toward sunrise"],
        "motions": ["fog parts to reveal one clean path", "scattered lights align into a readable constellation"],
        "messages": [
            ("attention gathers into a clear direction", "Aandacht verzamelt zich tot een heldere richting."),
            ("the next step becomes visible without force", "De volgende stap wordt zichtbaar zonder druk."),
        ],
    },
    "creation": {
        "keys": ["maak", "bouwen", "creatief", "kunst", "schrijf", "muziek", "idee", "project", "droom", "create", "build"],
        "symbols": ["a glowing loom", "a brush made of light", "a workshop inside a star", "a blank page filling with moving color"],
        "motions": ["raw sparks gather into a living pattern", "materials assemble themselves into a new form"],
        "messages": [
            ("creative material finds form through playful structure", "Creatieve energie vindt vorm via speelse structuur."),
            ("the inner maker receives a clear image to build from", "De innerlijke maker ontvangt een helder beeld om vanuit te bouwen."),
        ],
    },
    "connection": {
        "keys": ["samen", "relatie", "liefde", "vriend", "familie", "contact", "verbinding", "luister", "gezien", "love", "together"],
        "symbols": ["two lights finding orbit", "a bridge woven from warm threads", "a table of glowing cups", "a pair of stars breathing together"],
        "motions": ["separate lights move into a gentle shared rhythm", "threads cross and form a stable luminous bridge"],
        "messages": [
            ("connection can approach without losing the self", "Verbinding mag naderen zonder het zelf te verliezen."),
            ("warmth becomes safe to receive and return", "Warmte wordt veilig om te ontvangen en terug te geven."),
        ],
    },
    "protection": {
        "keys": ["bescherm", "veilig", "grens", "grenzen", "warm", "omsluit", "omhult", "schild", "cocoon", "huid", "safe"],
        "symbols": ["a warm protective shell", "a translucent cocoon", "a soft boundary ring", "a luminous inner room"],
        "motions": ["a boundary forms softly while light keeps flowing", "rings expand and choose what may enter"],
        "messages": [
            ("a gentle boundary forms without closing the heart", "Een zachte grens vormt zich zonder het hart te sluiten."),
            ("warm safety surrounds the inner self", "Warme veiligheid omsluit het innerlijke zelf."),
        ],
    },
    "vitality": {
        "keys": ["energie", "gezond", "lichaam", "fit", "genezing", "rusten", "slaap", "kracht", "body", "health", "energy"],
        "symbols": ["a green pulse through clear water", "living roots around a crystal spring", "a sunlit breath in the chest", "a jade leaf unfolding"],
        "motions": ["clean light circulates through living roots", "the body image brightens in slow waves"],
        "messages": [
            ("renewal circulates through the whole field", "Vernieuwing stroomt door het hele veld."),
            ("the body image turns toward repair and vitality", "Het lichaamsbeeld keert naar herstel en levenskracht."),
        ],
    },
    "abundance": {
        "keys": ["geld", "werk", "succes", "overvloed", "kans", "carriere", "klant", "groei", "money", "success"],
        "symbols": ["an opening bronze gate", "a golden orchard", "a river of amber light", "a constellation of keys"],
        "motions": ["small signals gather into visible opportunity", "paths open one by one toward a warm horizon"],
        "messages": [
            ("available paths open with grounded ease", "Beschikbare paden openen met gegronde rust."),
            ("resources move toward the prepared center", "Hulpbronnen bewegen naar het voorbereide midden."),
        ],
    },
    "shadow": {
        "keys": ["verdriet", "rouw", "pijn", "boos", "schuld", "schaamte", "donker", "nacht", "verlies", "mis", "missen", "gemis", "grief", "sad", "pain"],
        "symbols": ["a dark mirror holding a small flame", "a cracked stone filling with gold", "a night room opening to dawn", "black water reflecting a gentle star"],
        "motions": ["shadow folds open and reveals a protected light", "dark water clears around a steady flame"],
        "messages": [
            ("the difficult feeling is held until it can soften", "Het moeilijke gevoel wordt gedragen tot het kan verzachten."),
            ("shadow becomes information, not identity", "Schaduw wordt informatie, geen identiteit."),
        ],
    },
    "grounding": {
        "keys": ["grond", "aarde", "basis", "stabiel", "thuis", "huis", "geld", "praktisch", "ground", "home"],
        "symbols": ["deep roots under a quiet room", "a stone circle with warm center light", "a mountain foundation", "a weighted bowl of clear water"],
        "motions": ["roots grow downward while light rises upward", "scattered motion settles into a stable base"],
        "messages": [
            ("the image finds a stable base before it expands", "Het beeld vindt eerst een stabiele basis en groeit daarna."),
            ("calm structure makes movement safe", "Rustige structuur maakt beweging veilig."),
        ],
    },
}


def load_module(path: Path, name: str) -> ModuleType | None:
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def helpers(comfy_path: Path) -> tuple[ModuleType | None, ModuleType | None]:
    wan = load_module(comfy_path / "wan_prompt_page.py", "dreamweaver_wan_prompt_page")
    image = load_module(comfy_path / "image_prompt_page.py", "dreamweaver_image_prompt_page")
    return wan, image


def parse_comfy_url() -> tuple[str, int]:
    parsed = urllib.parse.urlparse(DEFAULT_COMFY_URL)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8188
    return host, port


def comfy_request(path: str, payload: dict[str, Any] | None = None, method: str = "GET", timeout: float = 30) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"{DEFAULT_COMFY_URL}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read()
    except urllib.error.URLError as exc:
        raise ComfyUnavailableError(
            f"ComfyUI draait niet of is nog niet klaar op {DEFAULT_COMFY_URL}. "
            "Start ComfyUI eerst en probeer daarna opnieuw."
        ) from exc
    return json.loads(body.decode("utf-8") or "{}")


def queue_counts(queue: dict[str, Any]) -> dict[str, int]:
    return {
        "running": len(queue.get("queue_running") or []),
        "pending": len(queue.get("queue_pending") or []),
    }


def vram_summary(system_stats: dict[str, Any] | None) -> list[dict[str, Any]]:
    result = []
    for device in safe_list((system_stats or {}).get("devices")):
        if not isinstance(device, dict):
            continue
        result.append(
            {
                "name": device.get("name"),
                "type": device.get("type"),
                "vramFree": device.get("vram_free"),
                "vramTotal": device.get("vram_total"),
            }
        )
    return result


def cancel_open_jobs_for_reset() -> list[str]:
    cancelled: list[str] = []
    with JOBS_LOCK:
        for job_id, job in JOBS.items():
            if job.get("done"):
                continue
            job["cancelRequested"] = True
            job["status"] = "reset_requested"
            job["error"] = "Noodreset aangevraagd."
            cancelled.append(job_id)
    return cancelled


def reset_comfy_runtime(clear_history: bool = True) -> dict[str, Any]:
    cancelled_jobs = cancel_open_jobs_for_reset()
    before_queue = comfy_request("/queue", timeout=10)
    before_stats = None
    try:
        before_stats = comfy_request("/system_stats", timeout=5)
    except Exception:
        before_stats = None

    steps: list[dict[str, Any]] = []

    def run_step(name: str, path: str, payload: dict[str, Any]) -> None:
        try:
            comfy_request(path, payload, method="POST", timeout=10)
            steps.append({"name": name, "ok": True})
        except Exception as exc:  # noqa: BLE001
            steps.append({"name": name, "ok": False, "error": str(exc)})

    run_step("interrupt", "/interrupt", {})
    run_step("clear_queue", "/queue", {"clear": True})
    if clear_history:
        run_step("clear_history", "/history", {"clear": True})
    run_step("free_vram", "/free", {"unload_models": True, "free_memory": True})

    time.sleep(1.0)
    after_queue = comfy_request("/queue", timeout=10)
    after_stats = None
    try:
        after_stats = comfy_request("/system_stats", timeout=5)
    except Exception:
        after_stats = None

    return {
        "reset": True,
        "cancelledJobs": cancelled_jobs,
        "steps": steps,
        "queueBefore": queue_counts(before_queue),
        "queueAfter": queue_counts(after_queue),
        "vramBefore": vram_summary(before_stats),
        "vramAfter": vram_summary(after_stats),
        "clearHistory": clear_history,
    }


def user_words(text: str) -> set[str]:
    words = set()
    for word in re.findall(r"[\wÀ-ÿ']+", text.lower()):
        cleaned = word.strip("'_")
        if len(cleaned) > 2 and cleaned not in STOPWORDS:
            words.add(cleaned)
    return words


def text_tokens(text: str) -> list[str]:
    return [word.strip("'_") for word in re.findall(r"[\wÀ-ÿ']+", text.lower()) if word.strip("'_")]


def keyword_hits(text: str, keys: list[str]) -> list[str]:
    tokens = text_tokens(text)
    lower = text.lower()
    hits: list[str] = []
    for key in keys:
        needle = key.lower().strip()
        if not needle:
            continue
        if " " in needle:
            matched = needle in lower
        elif len(needle) <= 3:
            matched = needle in tokens
        else:
            matched = any(token == needle or token.startswith(needle) or needle in token for token in tokens)
        if matched:
            hits.append(key)
    return hits


def strip_user_words(text: str, forbidden: set[str]) -> str:
    if not forbidden:
        return text
    parts = []
    for token in re.split(r"(\W+)", text):
        if token.lower() in forbidden:
            continue
        parts.append(token)
    cleaned = re.sub(r"\s{2,}", " ", "".join(parts)).strip(" ,.;:-")
    return cleaned or "luminous abstract dream image"


def category_for_desire(desire: str) -> str:
    lower = desire.lower()
    for category, data in ARCHETYPES.items():
        if category == "default":
            continue
        if any(key in lower for key in data["keys"]):
            return category
    return "default"


def symbolic_profile_for_desire(desire: str, category: str) -> dict[str, Any]:
    scored: list[tuple[int, str]] = []
    for theme, data in SYMBOLIC_THEMES.items():
        hits = keyword_hits(desire, list(data["keys"]))
        if hits:
            scored.append((len(hits), theme))
    scored.sort(reverse=True)

    category_theme = {
        "confidence": "clarity",
        "peace": "grounding",
        "protection": "protection",
        "prosperity": "abundance",
        "love": "connection",
        "health": "vitality",
    }.get(category)
    if scored:
        theme = scored[0][1]
    elif category_theme:
        theme = category_theme
    elif "?" in desire:
        theme = "clarity"
    elif "!" in desire:
        theme = "release"
    else:
        theme = "transformation"

    secondary = [name for _, name in scored if name != theme][:2]
    return {
        "theme": theme,
        "secondaryThemes": secondary,
        "signals": [theme, *secondary] or ["transformation"],
    }


def pick_message(rng: random.Random, theme: str, category: str) -> tuple[str, str]:
    theme_messages = SYMBOLIC_THEMES.get(theme, {}).get("messages", [])
    if theme_messages:
        message = rng.choice(theme_messages)
        return str(message[0]), str(message[1])
    fallback = rng.choice(CATEGORY_MESSAGES.get(category, CATEGORY_MESSAGES["default"]))
    return fallback, fallback


def pick_visual_grammar(rng: random.Random, category: str, profile: dict[str, Any]) -> dict[str, str]:
    theme = str(profile.get("theme") or "transformation")
    theme_data = SYMBOLIC_THEMES.get(theme, SYMBOLIC_THEMES["transformation"])
    message, message_nl = pick_message(rng, theme, category)
    symbol_pool = list(theme_data.get("symbols", [])) + CATEGORY_SYMBOLS.get(category, []) + VISUAL_SYMBOLS
    motion_pool = list(theme_data.get("motions", [])) + VISUAL_MOTIONS
    return {
        "theme": theme,
        "environment": rng.choice(VISUAL_ENVIRONMENTS),
        "material": rng.choice(VISUAL_MATERIALS),
        "symbol": rng.choice(symbol_pool),
        "motion": rng.choice(motion_pool),
        "transition": rng.choice(VISUAL_TRANSITIONS),
        "palette": rng.choice(VISUAL_PALETTES),
        "message": message,
        "messageNl": message_nl,
    }


def dream_motion_prompt(grammar: dict[str, str]) -> str:
    return (
        f"{grammar['environment']}, {grammar['symbol']} made of {grammar['material']}, "
        f"{grammar['palette']}, symbolic subconscious dream message, {grammar['transition']}, "
        f"{grammar['motion']}, continuous visible transformation throughout the whole clip, "
        "not a still image, evolving forms, fluid parallax, living light, cinematic lighting, "
        "high detail, soft surreal atmosphere, no readable text, no letters, no captions, no watermark"
    )


def transform_desire(desire: str) -> dict[str, Any]:
    forbidden = user_words(desire)
    category = category_for_desire(desire)
    digest = hashlib.sha256(desire.encode("utf-8")).hexdigest()
    rng = random.Random(int(digest[:16], 16))
    profile = symbolic_profile_for_desire(desire, category)
    grammar = pick_visual_grammar(rng, category, profile)
    primary = list(ARCHETYPES[category]["phrases"])
    backup = list(ARCHETYPES["default"]["phrases"])
    theme_phrase = str(grammar["message"]).split(",")[0].strip(". ")
    rng.shuffle(primary)
    rng.shuffle(backup)

    phrases: list[str] = []
    for phrase in [theme_phrase] + primary + backup:
        cleaned = strip_user_words(phrase, forbidden)
        if cleaned and cleaned.lower() not in {p.lower() for p in phrases}:
            phrases.append(cleaned)
        if len(phrases) >= 8:
            break

    phrase_signal = ", ".join(phrases[:3])
    scene_core = dream_motion_prompt(grammar)
    image_prompts = [
        (
            f"{grammar['environment']}, {grammar['symbol']} appears as {grammar['material']}, "
            f"{grammar['palette']}, {phrase_signal}, symbolic dream image, no readable text"
        ),
        (
            f"{grammar['symbol']} begins to transform, {grammar['motion']}, "
            f"{grammar['transition']}, luminous particles gather into a clear center, no text"
        ),
        (
            f"{grammar['environment']} opens into a deeper inner landscape, {grammar['material']} reflections, "
            f"{grammar['palette']}, cinematic surreal dream art, no letters"
        ),
        (
            f"resolved symbolic scene, {grammar['message']}, {grammar['symbol']} glowing calmly, "
            f"soft motion feeling, high detail, no captions"
        ),
    ]
    image_prompts = [strip_user_words(prompt, forbidden) for prompt in image_prompts]

    video_prompt = (
        f"{strip_user_words(scene_core, forbidden)}, visual arc: {grammar['message']}, "
        "start with a closed symbolic form, then show it visibly opening and moving, "
        "end with a stable luminous image, smooth looping dream video"
    )
    return {
        "category": category,
        "phrases": phrases,
        "imagePrompts": image_prompts,
        "videoPrompt": video_prompt,
        "visualGrammar": grammar,
        "symbolicProfile": profile,
        "subconsciousMessage": grammar["messageNl"],
        "visualMessage": grammar["message"],
        "forbiddenWords": sorted(forbidden),
    }


def scan_models(comfy_path: Path) -> dict[str, Any]:
    models_dir = comfy_path / "models"
    files = set()
    if models_dir.exists():
        for path in models_dir.rglob("*"):
            if path.is_file() and not path.name.startswith("put_"):
                files.add(path.relative_to(models_dir).as_posix())
    return {
        "comfyPath": str(comfy_path),
        "wan22": all(
            item in files
            for item in [
                "diffusion_models/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
                "diffusion_models/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
                "loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
                "loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors",
                "text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                "vae/wan_2.1_vae.safetensors",
            ]
        ),
        "wan21": all(
            item in files
            for item in [
                "diffusion_models/Wan2.1/wan2.1_t2v_1.3B_fp16.safetensors",
                "text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                "vae/wan_2.1_vae.safetensors",
            ]
        ),
        "zimage": all(
            item in files
            for item in [
                "diffusion_models/z_image_turbo_bf16.safetensors",
                "text_encoders/qwen_3_4b.safetensors",
                "vae/ae.safetensors",
            ]
        ),
        "files": sorted(files),
    }


def word_count(text: str) -> int:
    return len(re.findall(r"[\wÀ-ÿ']+", text))


def normalize_story_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def normalize_extracted_text(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def extract_text_from_plain(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return normalize_extracted_text(data.decode(encoding))
        except UnicodeDecodeError:
            continue
    return normalize_extracted_text(data.decode("utf-8", "replace"))


def docx_xml_text(xml_data: bytes) -> str:
    root = ET.fromstring(xml_data)
    chunks: list[str] = []
    paragraph_open = False
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "p":
            if paragraph_open:
                chunks.append("\n")
            paragraph_open = True
        elif tag == "t" and element.text:
            chunks.append(element.text)
        elif tag == "tab":
            chunks.append("\t")
        elif tag in {"br", "cr"}:
            chunks.append("\n")
    return "".join(chunks)


def extract_text_from_docx(data: bytes) -> str:
    parts = [
        "word/document.xml",
        "word/footnotes.xml",
        "word/endnotes.xml",
        "word/comments.xml",
    ]
    texts: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            parts.extend(sorted(name for name in names if re.fullmatch(r"word/(?:header|footer)\d+\.xml", name)))
            for part in parts:
                if part not in names:
                    continue
                try:
                    text = docx_xml_text(archive.read(part))
                except ET.ParseError:
                    continue
                if text.strip():
                    texts.append(text)
    except zipfile.BadZipFile as exc:
        raise ValueError("Dit DOCX-bestand kon niet worden gelezen.") from exc
    text = normalize_extracted_text("\n\n".join(texts))
    if not text:
        raise ValueError("Er is geen tekst gevonden in dit DOCX-bestand.")
    return text


def extract_text_from_pdf_pypdf2(data: bytes) -> str:
    try:
        from PyPDF2 import PdfReader  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("PyPDF2 is niet beschikbaar.") from exc
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            pass
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return normalize_extracted_text("\n\n".join(pages))


def extract_text_from_pdf_pdftotext(data: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as temp:
        temp.write(data)
        temp.flush()
        result = subprocess.run(
            ["pdftotext", "-layout", temp.name, "-"],
            check=False,
            capture_output=True,
            timeout=90,
        )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace").strip()
        raise ValueError(stderr or "pdftotext kon dit PDF-bestand niet lezen.")
    return normalize_extracted_text(result.stdout.decode("utf-8", "replace"))


def extract_text_from_pdf(data: bytes) -> str:
    errors: list[str] = []
    for extractor in (extract_text_from_pdf_pypdf2, extract_text_from_pdf_pdftotext):
        try:
            text = extractor(data)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
            continue
        if text:
            return text
    detail = "; ".join(error for error in errors if error)
    if detail:
        raise ValueError(f"Er is geen tekst uit deze PDF gehaald. Mogelijk is het een scan. Details: {detail}")
    raise ValueError("Er is geen tekst uit deze PDF gehaald. Mogelijk is het een scan en is OCR nodig.")


def extract_document_text(filename: str, mime_type: str, data: bytes) -> dict[str, Any]:
    if len(data) > DOCUMENT_UPLOAD_MAX_BYTES:
        raise ValueError("Dit bestand is te groot voor direct uitlezen. Maximaal 35 MB.")
    suffix = Path(filename or "").suffix.lower()
    mime = (mime_type or "").lower()
    if suffix in {".txt", ".md", ".markdown", ".text"} or mime.startswith("text/"):
        kind = "text"
        text = extract_text_from_plain(data)
    elif suffix == ".docx" or mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        kind = "docx"
        text = extract_text_from_docx(data)
    elif suffix == ".pdf" or mime == "application/pdf":
        kind = "pdf"
        text = extract_text_from_pdf(data)
    else:
        raise ValueError("Ondersteund: .txt, .md, .docx en .pdf.")
    words = word_count(text)
    if not text:
        raise ValueError("Er is geen leesbare tekst gevonden in dit bestand.")
    return {
        "filename": filename,
        "kind": kind,
        "text": text,
        "wordCount": words,
        "truncated": False,
    }


def trim_text(text: str, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip(" ,.;:-") + "…"


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return []
    parts = re.split(r"(?<=[.!?…])\s+", text)
    sentences = [part.strip() for part in parts if part.strip()]
    if len(sentences) <= 1 and word_count(text) > 80:
        rough = re.split(r"\s*[,;]\s*", text)
        sentences = [part.strip() for part in rough if part.strip()]
    return sentences or [text]


def stable_pick(seed_text: str, items: list[str], offset: int = 0) -> str:
    digest = hashlib.sha256(f"{seed_text}|{offset}".encode("utf-8")).hexdigest()
    return items[int(digest[:8], 16) % len(items)]


def sentence_start_at(text: str, start: int) -> bool:
    before = text[:start]
    stripped = before.rstrip()
    return not stripped or stripped[-1] in ".!?\n\r…"


def lower_word_set(text: str) -> set[str]:
    return {word.strip("'_").lower() for word in re.findall(r"[\wÀ-ÿ']+", text) if word.strip("'_")}


def story_has_human_signal(text: str) -> bool:
    return bool(lower_word_set(text) & HUMAN_SIGNAL_WORDS)


def candidate_person_context(text: str, start: int, end: int, at_sentence_start: bool) -> int:
    before = text[max(0, start - 90) : start].lower()
    after = text[end : min(len(text), end + 90)].lower()
    score = 0
    action_pattern = "|".join(re.escape(word) for word in PERSON_ACTION_WORDS)
    attribution_pattern = "|".join(re.escape(word) for word in ATTRIBUTION_WORDS)
    relation_pattern = "|".join(re.escape(word) for word in RELATION_LABELS)
    if re.match(rf"\s+(?:{action_pattern})\b", after):
        score += 3
    if re.match(rf"\s*,?\s*(?:her|his|their|haar|zijn|hun)?\s*(?:{relation_pattern})\b", after):
        score += 3
    if re.search(rf"\b(?:{relation_pattern})\s*,?\s*$", before):
        score += 2
    if re.search(rf"\b(?:{attribution_pattern})\s+$", before):
        score += 3
    if re.search(r"\b(?:and|en|with|met|zonder|without)\s+$", before):
        score += 1
    if re.match(r"\s+(?:and|en)\b", after):
        score += 1
    if not at_sentence_start:
        score += 1
    return score


def name_candidate_blocked(name: str) -> bool:
    parts = [part.strip("'_").lower() for part in name.split() if part.strip("'_")]
    if not parts:
        return True
    blocklist = {word.lower() for word in NAME_BLOCKLIST} | NON_CHARACTER_NAME_WORDS
    return any(part in blocklist for part in parts)


def is_likely_real_person(name: str, story_text: str,
                            precomputed_action_pos: list[int] | None = None,
                            precomputed_neg_pos: list[int] | None = None) -> bool:
    """Return True only if this looks like a genuine acting/speaking character (person or animal being), not object/place/concept.

    If precomputed_*_pos lists are provided (from a single pass over the story), the expensive context
    scanning becomes much faster (bisect instead of repeated full-text searches + long any() on windows).
    This is the main source of added latency from "understanding contexts" for accurate character vs
    place/brand filtering.
    """
    if not name or name in {"Narrator", "Protagonist"}:
        return True
    lname = canonical_element_key(name)
    if not lname or lname in NON_CHARACTER_NAME_WORDS:
        return False
    # Hard block: never treat pure pronouns as character names (they, you, me, he, she etc. often start sentences or get actions)
    pronoun_block = {
        "they", "them", "their", "theirs", "you", "your", "yours", "he", "him", "his",
        "she", "her", "hers", "we", "us", "our", "ours", "me", "my", "mine", "i", "it", "its",
        "jij", "jou", "jouw", "wij", "ons", "onze", "mij", "mijn", "ik", "hij", "zij", "ze", "hun",
    }
    if lname in pronoun_block:
        return False
    # Reject very generic single nouns that are rarely proper character names.
    # Keep "muzak" (almost always the brand/music system) but do not hard-block generic place names
    # like "granville" here — a person can be named Granville, and context should decide.
    common_non_person = {
        "voice", "echo", "figure", "silhouette", "shadow", "shade", "dream", "memory", "thought",
        "machine", "device", "robot", "computer", "time", "moment", "fate", "death", "life", "love",
        "world", "earth", "god", "angel", "wind", "rain", "storm", "fire", "sea", "sun", "moon",
        "star", "creature", "being", "silence", "darkness", "light", "heart", "mind", "soul",
        # Very reliable non-persons
        "muzak", "spotify",
    }
    if lname in common_non_person:
        return False

    # Evidence-based check. When precomputed position lists are supplied we use fast bisect
    # lookups instead of rescanning the whole story for every candidate. This is the key
    # optimization for the "understanding contexts" logic that was making analysis slow on long stories.
    pattern = re.compile(rf"\b{re.escape(name.split()[0])}\b", re.IGNORECASE)
    strong_person_signals = 0
    has_strong_negative_cue = False

    place_cues = {"town of", "city of", "village of", "in the town", "in the city", "the town", "the city",
                  "the village", "the street", "the building", "the hotel", "the store"}
    brand_music_cues = {"the muzak", "muzak played", "muzak in the", "background music", "elevator music",
                        "piped music", "the system played"}

    # Limit to first ~8 occurrences for speed on long stories; that's enough to detect clear personhood
    matches = list(pattern.finditer(story_text))[:8]
    for match in matches:
        npos = match.start()

        # Use precomputed positions + bisect when available (much faster than building windows + any() over long verb lists)
        has_person_in_this_window = False
        if precomputed_action_pos:
            i = bisect.bisect_left(precomputed_action_pos, npos - 70)
            if i < len(precomputed_action_pos) and precomputed_action_pos[i] <= npos + 90:
                strong_person_signals += 2
                has_person_in_this_window = True
        else:
            # fallback (slow path)
            start = max(0, npos - 70)
            end = min(len(story_text), npos + 90)
            window = story_text[start:end].lower()
            if any(v in window for v in (" said", " says", " asked", " asks", " replied", " whispers", " shouted",
                                         " walked up", " ran to", " stood", " sat down", " looked at", " smiled at",
                                         " cried", " entered the room", " left the", " turned to", " felt", " knew",
                                         " saw", " heard", " 's face", " 's hand", " 's eyes", " spoke to", " turned and",
                                         " found", " read", " decided", " appeared", " warned", " kept", " talked",
                                         " smiled", " met", " opened", " closed", " studied", " wrote", " discovered",
                                         " ran", " walked", " sat", " stood", " looked", " smiled", " cried", " entered",
                                         " left", " turned", " felt", " thought", " saw", " heard")):
                strong_person_signals += 2
                has_person_in_this_window = True

        # pronoun check is cheap (small window or we can precompute too, but this is fine)
        start = max(0, npos - 70)
        end = min(len(story_text), npos + 90)
        window = story_text[start:end].lower()
        if re.search(r"\b(he|she|they|his|her|their|him|hij|zij|ze)\b", window):
            strong_person_signals += 1
            has_person_in_this_window = True

        # negative cues
        if precomputed_neg_pos:
            i = bisect.bisect_left(precomputed_neg_pos, npos - 70)
            if i < len(precomputed_neg_pos) and precomputed_neg_pos[i] <= npos + 90:
                if not has_person_in_this_window:
                    has_strong_negative_cue = True
        else:
            if (any(cue in window for cue in place_cues) or any(cue in window for cue in brand_music_cues)) and not has_person_in_this_window:
                has_strong_negative_cue = True

    is_multi_word = len(name.split()) >= 2

    # Balanced rules:
    # - A single clear person signal (e.g. "Granville said" or "Granville walked up to her") is enough for inclusion.
    # - Only reject on negative cue if there is almost no person evidence.
    if has_strong_negative_cue and strong_person_signals < 1:
        return False

    if strong_person_signals >= 1 or is_multi_word:
        return True

    # Final gate for very weak cases
    first = lname.split()[0] if lname.split() else lname
    if first in NON_CHARACTER_NAME_WORDS:
        return False
    return False


def extract_character_names(text: str) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    context_scores: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    pattern = re.compile(r"\b[A-ZÀ-Ý][a-zÀ-ÿ'-]+(?:\s+[A-ZÀ-Ý][a-zÀ-ÿ'-]+){0,2}\b")
    for match in pattern.finditer(text):
        name = re.sub(r"\s+", " ", match.group(0).strip())
        parts = name.split()
        if name_candidate_blocked(name):
            continue
        if len(name) < 3 or name.isupper():
            continue
        at_sentence_start = sentence_start_at(text, match.start())
        score = candidate_person_context(text, match.start(), match.end(), at_sentence_start)
        if at_sentence_start and score <= 0 and len(parts) == 1:
            continue
        counts[name] = counts.get(name, 0) + 1
        context_scores[name] = context_scores.get(name, 0) + score
        first_seen.setdefault(name, match.start())

    compact: dict[str, int] = {}
    compact_first_seen: dict[str, int] = {}
    for name, count in counts.items():
        shorter = name.split()[0]
        if counts.get(shorter, 0) > count and len(name.split()) > 1:
            continue
        compact[name] = count
        compact_first_seen[name] = first_seen[name]

    ordered = sorted(compact.items(), key=lambda item: (-item[1], compact_first_seen[item[0]]))
    repeated = [(name, count) for name, count in ordered if count >= 2]
    # Harden: only accept repeated if they have at least minimal context signal somewhere, or are strong singles
    repeated = [(name, count) for name, count in repeated if context_scores.get(name, 0) > 0 or count >= 3]
    if len(repeated) >= 3:
        singles = [
            (name, count)
            for name, count in ordered
            if count == 1 and context_scores.get(name, 0) >= 2
        ][: max(0, 18 - len(repeated))]
        return (repeated + singles)[:18]
    return [
        (name, count)
        for name, count in ordered
        if (count >= 2 and context_scores.get(name, 0) > 0) or context_scores.get(name, 0) >= 2
    ][:18]


def character_contexts(name: str, sentences: list[str]) -> list[str]:
    pattern = re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
    return [sentence for sentence in sentences if pattern.search(sentence)][:4]


def build_character_cards(text: str, sentences: list[str]) -> list[dict[str, Any]]:
    names = extract_character_names(text)
    if not names:
        return []

    cards: list[dict[str, Any]] = []
    for index, (name, mentions) in enumerate(names, start=1):
        contexts = character_contexts(name, sentences)
        visual_signature = (
            f"{stable_pick(name, HAIR_OPTIONS, 1)}, "
            f"{stable_pick(name, OUTFIT_OPTIONS, 2)}, "
            f"{stable_pick(name, MARKER_OPTIONS, 3)}"
        )
        role = "protagonist" if index == 1 else ("main character" if mentions >= 4 else "supporting character")
        cards.append(
            {
                "id": f"char_{index:02d}",
                "name": name,
                "role": role,
                "mentions": mentions,
                "visualSignature": visual_signature,
                "continuityPrompt": (
                    f"{name}: consistent realistic anime character, {visual_signature}, "
                    "same face, age, gender, body, hair and outfit every single time this character is visible; this character only, no other people"
                ),
                "evidence": [trim_text(context, 180) for context in contexts],
            }
        )
    return cards


def strong_scene_break(sentence: str) -> bool:
    lower = sentence.lower().strip()
    return lower.startswith(
        (
            "later ",
            "de volgende dag",
            "die avond",
            "die nacht",
            "de ochtend",
            "ondertussen",
            "intussen",
            "toen ze",
            "toen hij",
            "toen ik",
            "plotseling",
            "opeens",
        )
    )


def scene_chunks(text: str) -> list[list[str]]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
    if not paragraphs:
        paragraphs = [text]

    chunks: list[list[str]] = []
    current: list[str] = []
    current_words = 0

    def flush() -> None:
        nonlocal current, current_words
        if current:
            chunks.append(current)
            current = []
            current_words = 0

    for paragraph in paragraphs:
        for sentence in split_sentences(paragraph):
            sentence_words = word_count(sentence)
            should_break = (
                current
                and current_words >= 70
                and (current_words + sentence_words > COMIC_SCENE_TARGET_WORDS or strong_scene_break(sentence))
            )
            if should_break:
                flush()
            current.append(sentence)
            current_words += sentence_words
        if current_words >= COMIC_SCENE_TARGET_WORDS:
            flush()
    flush()
    return chunks


def detect_location(text: str) -> str:
    pattern = re.compile(
        r"\b(?:in|on|at|by|under|above|beside|near|inside|outside|into|through|across|within|"
        r"op|bij|aan|onder|boven|naast|voor|achter|door)\s+(?:the|a|an|de|het|een|den|der)?\s*"
        r"([A-Za-zÀ-ÿ][^,.!?;:\n]{2,44})",
        re.IGNORECASE,
    )
    pronouns = {
        "he", "him", "his", "she", "her", "hers", "they", "them", "their", "us", "you", "it",
        "my", "mine", "your", "yours", "our", "ours", "own",
        "haar", "hem", "hen", "hun", "mij", "me", "jou", "ons", "zijn", "ze", "hij",
        "mijn", "jouw", "eigen", "onze",
    }
    abstract_location_words = {
        "life", "heart", "mind", "head", "soul", "thought", "thoughts", "memory", "memories",
        "dream", "dreams", "fear", "fears", "pain", "hope", "voice", "voices", "silence",
        "sentence", "feeling", "feelings", "darkness", "light", "shadow", "shadows",
        "leven", "hart", "hoofd", "ziel", "gedachte", "gedachten", "herinnering", "herinneringen",
        "droom", "dromen", "angst", "pijn", "hoop", "stem", "stemmen", "stilte", "gevoel",
    }
    for match in pattern.finditer(text):
        candidate = trim_text(match.group(1), 56).lower()
        candidate = re.sub(r"^(?:my|your|his|her|our|their|own|mijn|jouw|zijn|haar|onze|hun|eigen)\s+", "", candidate).strip()
        candidate = re.sub(r"^own\s+", "", candidate).strip()
        candidate = re.split(r"\b(?:and|then|while|when|en|toen|terwijl)\b", candidate, maxsplit=1)[0].strip(" ,;:")
        words = [word for word in re.findall(r"[a-zÀ-ÿ']+", candidate.lower()) if word]
        first = words[0] if words else ""
        if not first or first in pronouns:
            continue
        if any(word.strip("'") in abstract_location_words for word in words[:4]):
            continue
        return candidate
    lower = text.lower()
    for needle, label in [
        ("forest", "forest"), ("wood", "forest"), ("city", "city"), ("town", "town"),
        ("kitchen", "kitchen"), ("hallway", "hallway"), ("corridor", "hallway"),
        ("bedroom", "bedroom"), ("room", "interior"), ("house", "house interior"),
        ("street", "street"), ("road", "road"), ("garden", "garden"), ("station", "station"),
        ("bridge", "bridge"), ("beach", "beach"), ("mountain", "mountain"),
        ("bos", "forest"), ("stad", "city"), ("kamer", "interior"), ("huis", "house interior"),
        ("straat", "street"),
    ]:
        if needle in lower:
            return label
    return "the main setting of this scene"


def detect_mood(text: str) -> str:
    lower = text.lower()
    scores: list[tuple[int, str]] = []
    for mood, keys in MOOD_KEYWORDS.items():
        score = sum(1 for key in keys if key in lower)
        if score:
            scores.append((score, mood))
    if not scores:
        return "cinematic"
    scores.sort(reverse=True)
    return scores[0][1]


def _unique_text(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        clean = str(item or "").strip()
        key = clean.lower()
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return result


def character_aliases(character: dict[str, Any], include_first_name: bool = True) -> list[str]:
    aliases = [str(character.get("name") or ""), *[str(alias or "") for alias in safe_list(character.get("aliases"))]]
    expanded: list[str] = []
    for alias in aliases:
        clean = re.sub(r"\s+", " ", alias.strip().strip(" .,:;!?\"'“”"))
        if not clean:
            continue
        expanded.append(clean)
        parts = clean.split()
        first = parts[0].strip(" .,:;!?\"'“”") if parts else ""
        first_lower = first.lower()
        if (
            include_first_name
            and len(parts) > 1
            and len(first) >= 3
            and first_lower not in NON_CHARACTER_NAME_WORDS
            and first not in NAME_BLOCKLIST
            and first.capitalize() not in NAME_BLOCKLIST
        ):
            expanded.append(first)
    return _unique_text(expanded)


def first_person_reference_position(text: str) -> int | None:
    match = re.search(r"\b(?:i|ik|me|mij|mijn|my|mine|we|wij|ons|onze|our|ours)\b", text, re.IGNORECASE)
    return match.start() if match else None


def character_mention_positions(text: str, characters: list[dict[str, Any]]) -> list[tuple[int, int, str, str]]:
    matches: list[tuple[int, int, str, str]] = []
    for character in characters:
        char_id = str(character.get("id") or "")
        if not char_id:
            continue
        for alias in character_aliases(character):
            if alias in {"Narrator", "Protagonist"}:
                continue
            for match in re.finditer(rf"\b{re.escape(alias)}\b", text, re.IGNORECASE):
                matches.append((match.start(), match.end(), char_id, alias))
    matches.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    return matches


def mentioned_character_ids(text: str, characters: list[dict[str, Any]]) -> list[str]:
    matches: list[tuple[int, str]] = []
    first_person_pos = first_person_reference_position(text)
    for character in characters:
        name = str(character.get("name") or "")
        char_id = str(character.get("id") or "")
        if not char_id:
            continue
        if name in {"Narrator", "Protagonist"} and first_person_pos is not None:
            matches.append((first_person_pos, char_id))
            continue
    for start, _, char_id, _ in character_mention_positions(text, characters):
        matches.append((start, char_id))
    matches.sort()
    return ordered_unique([char_id for _, char_id in matches])


def last_mentioned_character_id(text: str, characters: list[dict[str, Any]]) -> str | None:
    positions = character_mention_positions(text, characters)
    return positions[-1][2] if positions else None


def character_gender_by_id(characters: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(character.get("id") or ""): normalize_gender(str(character.get("gender") or ""))
        for character in characters
        if character.get("id")
    }


def _known_character_ids(characters: list[dict[str, Any]]) -> list[str]:
    return [str(character.get("id") or "") for character in characters if character.get("id")]


def _resolve_gendered_reference(
    gender: str,
    characters: list[dict[str, Any]],
    candidates: list[str] | None = None,
    active: list[str] | None = None,
    last_subject: str | None = None,
) -> list[str]:
    genders = character_gender_by_id(characters)
    all_ids = _known_character_ids(characters)
    candidate_ids = ordered_unique([char_id for char_id in (candidates or []) if char_id in all_ids])
    active_ids = ordered_unique([char_id for char_id in (active or []) if char_id in all_ids])
    search_order = ordered_unique([*( [last_subject] if last_subject else [] ), *active_ids, *candidate_ids, *all_ids])

    def has_gender(char_id: str) -> bool:
        return genders.get(char_id) == gender

    if last_subject and last_subject in search_order and has_gender(last_subject):
        return [last_subject]

    active_matches = [char_id for char_id in active_ids if has_gender(char_id)]
    if len(active_matches) == 1:
        return active_matches

    candidate_matches = [char_id for char_id in candidate_ids if has_gender(char_id)]
    if len(candidate_matches) == 1:
        return candidate_matches

    all_matches = [char_id for char_id in all_ids if has_gender(char_id)]
    if len(all_matches) == 1:
        return all_matches

    unknown_active = [char_id for char_id in active_ids if genders.get(char_id, "unknown") == "unknown"]
    if len(unknown_active) == 1:
        return unknown_active

    unknown_candidates = [char_id for char_id in candidate_ids if genders.get(char_id, "unknown") == "unknown"]
    if len(unknown_candidates) == 1:
        return unknown_candidates

    single_context = active_ids or candidate_ids
    return single_context if len(single_context) == 1 else []


def pronoun_referenced_character_ids(
    text: str,
    characters: list[dict[str, Any]],
    candidates: list[str] | None = None,
    active: list[str] | None = None,
    last_subject: str | None = None,
) -> list[str]:
    words = lower_word_set(text)
    if not words & (PRONOUN_WORDS | FIRST_PERSON_WORDS):
        return []
    result: list[str] = []
    all_ids = _known_character_ids(characters)

    if words & FIRST_PERSON_WORDS:
        for character in characters:
            if str(character.get("name") or "") in {"Narrator", "Protagonist"}:
                result.append(str(character.get("id")))
                break

    if words & FEMALE_PRONOUN_WORDS:
        result.extend(_resolve_gendered_reference("female", characters, candidates, active, last_subject))
    if words & MALE_PRONOUN_WORDS:
        result.extend(_resolve_gendered_reference("male", characters, candidates, active, last_subject))
    plural_hits = words & PLURAL_PRONOUN_WORDS
    if plural_hits and (plural_hits - {"zij", "ze"} or not result):
        context_ids = ordered_unique([char_id for char_id in [*(active or []), *(candidates or [])] if char_id in all_ids])
        if context_ids:
            result.extend(context_ids)

    return ordered_unique([char_id for char_id in result if char_id in all_ids])


def text_has_object_pronoun_reference(text: str) -> bool:
    lower = text.lower()
    relation_pattern = "|".join(re.escape(word) for word in RELATION_LABELS)
    if re.search(r"\b(?:him|hem|them|hen)\b", lower):
        return True
    if re.search(rf"\b(?:her|haar)\b(?!\s+(?:{relation_pattern})\b)", lower):
        return True
    return False


def scene_characters(text: str, characters: list[dict[str, Any]]) -> list[str]:
    present = mentioned_character_ids(text, characters)
    return present


def character_name_map(characters: list[dict[str, Any]]) -> dict[str, str]:
    return {str(character["id"]): str(character["name"]) for character in characters}


def character_names(character_ids: list[str], characters: list[dict[str, Any]], limit: int | None = None) -> list[str]:
    by_id = character_name_map(characters)
    names = [by_id[char_id] for char_id in character_ids if char_id in by_id]
    return names if limit is None else names[:limit]


def scene_has_empty_cue(text: str) -> bool:
    lower = text.lower()
    return any(cue in lower for cue in EMPTY_SCENE_CUES)


def scene_has_pronoun_reference(text: str) -> bool:
    words = lower_word_set(text)
    return bool(words & (PRONOUN_WORDS | FIRST_PERSON_WORDS))


def timeline_casts_for_scenes(scene_texts: list[str], characters: list[dict[str, Any]]) -> list[dict[str, list[str]]]:
    active: list[str] = []
    last_subject: str | None = None
    all_ids = [str(character["id"]) for character in characters]
    scene_casts: list[dict[str, list[str]]] = []

    for scene_text in scene_texts:
        mentioned = mentioned_character_ids(scene_text, characters)
        last_mention = last_mentioned_character_id(scene_text, characters)
        explicit_absent = explicit_absent_character_ids(scene_text, characters, active=active, candidates=all_ids, last_subject=last_subject)
        exits = exiting_character_ids(scene_text, characters, active=active, candidates=all_ids, last_subject=last_subject)
        pronoun_refs = pronoun_referenced_character_ids(scene_text, characters, candidates=all_ids, active=active, last_subject=last_subject)
        empty_without_people = scene_has_empty_cue(scene_text) and not (mentioned or pronoun_refs or story_has_human_signal(scene_text))

        if mentioned:
            present = [char_id for char_id in mentioned if char_id not in explicit_absent]
        elif empty_without_people:
            present = []
        elif pronoun_refs:
            present = [char_id for char_id in pronoun_refs if char_id not in explicit_absent]
        elif len(characters) == 1 and story_has_human_signal(scene_text) and not explicit_absent:
            present = [all_ids[0]]
        else:
            present = []

        present = ordered_unique([char_id for char_id in present if char_id not in explicit_absent])
        absent = ordered_unique([char_id for char_id in all_ids if char_id not in present])
        scene_casts.append(
            {
                "present": present,
                "absent": absent,
                "exiting": exits,
            }
        )

        active = [char_id for char_id in present if char_id not in exits]
        if exits:
            active = [char_id for char_id in active if char_id not in exits]
        if last_mention and last_mention in present:
            last_subject = last_mention
        elif len(present) == 1:
            last_subject = present[0]

    return scene_casts


def ordered_unique(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def explicit_absent_character_ids(
    text: str,
    characters: list[dict[str, Any]],
    active: list[str] | None = None,
    candidates: list[str] | None = None,
    last_subject: str | None = None,
) -> list[str]:
    absent: list[str] = []
    state_pattern = "|".join(re.escape(keyword) for keyword in ABSENCE_STATE_KEYWORDS)
    relation_pattern = "|".join(re.escape(word) for word in RELATION_LABELS)
    for sentence in split_sentences(text):
        lower = sentence.lower()
        for character in characters:
            char_id = str(character["id"])
            for alias in character_aliases(character):
                name = alias.lower()
                if re.search(rf"\bzonder\s+{re.escape(name)}\b", lower):
                    absent.append(char_id)
                    break
                if re.search(rf"\bniet\s+bij\s+{re.escape(name)}\b", lower):
                    absent.append(char_id)
                    break
                if re.search(rf"\bwithout\s+{re.escape(name)}\b", lower):
                    absent.append(char_id)
                    break
                if re.search(rf"\bnot\s+with\s+{re.escape(name)}\b", lower):
                    absent.append(char_id)
                    break
                if re.search(rf"\b{re.escape(name)}\b[^.!?]{{0,90}}\b(?:{state_pattern})\b", lower):
                    absent.append(char_id)
                    break
                if re.search(rf"\b(?:{state_pattern})\b[^.!?]{{0,90}}\b{re.escape(name)}\b", lower):
                    absent.append(char_id)
                    break
        if re.search(rf"\b(?:without|not with|zonder|niet bij)\s+(?:him|hem|them|hen|her|haar)\b(?!\s+(?:{relation_pattern})\b)", lower):
            absent.extend(pronoun_referenced_character_ids(sentence, characters, candidates, active, last_subject))
    return ordered_unique(absent)


def exiting_character_ids(
    text: str,
    characters: list[dict[str, Any]],
    active: list[str] | None = None,
    candidates: list[str] | None = None,
    last_subject: str | None = None,
) -> list[str]:
    lower = text.lower()
    exit_pattern = "|".join(re.escape(keyword) for keyword in EXIT_KEYWORDS)
    pronoun_exit_pattern = "|".join(re.escape(keyword) for keyword in EXIT_KEYWORDS if keyword != "left")
    subject_pronoun_pattern = "he|she|they|hij|zij|ze"
    exiting: list[str] = []
    for character in characters:
        char_id = str(character["id"])
        for alias in character_aliases(character):
            name = alias.lower()
            if re.search(rf"\b{re.escape(name)}\b[^.!?]{{0,90}}\b(?:{exit_pattern})\b", lower):
                exiting.append(char_id)
                break
            if re.search(rf"\b(?:{exit_pattern})\b[^.!?]{{0,90}}\b{re.escape(name)}\b", lower):
                exiting.append(char_id)
                break
    for sentence in split_sentences(text):
        sentence_lower = sentence.lower()
        if re.search(rf"\b(?:{subject_pronoun_pattern})\b[^.!?]{{0,70}}\b(?:{pronoun_exit_pattern})\b", sentence_lower):
            exiting.extend(pronoun_referenced_character_ids(sentence, characters, candidates, active, last_subject))
    return ordered_unique(exiting)


def panel_casts_for_scene(
    beats: list[list[str]],
    scene_cast: list[str],
    characters: list[dict[str, Any]],
) -> list[dict[str, list[str]]]:
    active: list[str] = list(scene_cast)
    last_subject: str | None = active[0] if len(active) == 1 else None
    left_scene: list[str] = []
    casts: list[dict[str, list[str]]] = []
    fallback_used = False

    for beat_sentences in beats:
        beat_text = " ".join(beat_sentences).strip()
        active_before = list(active)
        candidates = [char_id for char_id in scene_cast if char_id not in left_scene]
        mentioned = mentioned_character_ids(beat_text, characters)
        last_mention = last_mentioned_character_id(beat_text, characters)
        explicit_absent = explicit_absent_character_ids(
            beat_text,
            characters,
            active=active_before,
            candidates=candidates,
            last_subject=last_subject,
        )
        exits = exiting_character_ids(
            beat_text,
            characters,
            active=active_before,
            candidates=candidates,
            last_subject=last_subject,
        )
        pronoun_refs = pronoun_referenced_character_ids(
            beat_text,
            characters,
            candidates=candidates,
            active=active_before,
            last_subject=last_subject,
        )
        empty_without_people = scene_has_empty_cue(beat_text) and not (mentioned or pronoun_refs or story_has_human_signal(beat_text))
        visible_mentions = [char_id for char_id in mentioned if char_id not in explicit_absent]

        if visible_mentions:
            present = visible_mentions
            if text_has_object_pronoun_reference(beat_text):
                present = ordered_unique([*present, *[char_id for char_id in pronoun_refs if char_id not in explicit_absent]])
            left_scene = [char_id for char_id in left_scene if char_id not in present]
        elif empty_without_people:
            present = []
        elif pronoun_refs:
            present = [char_id for char_id in pronoun_refs if char_id not in left_scene]
        elif len(active_before) == 1 and story_has_human_signal(beat_text):
            present = [char_id for char_id in active_before if char_id not in left_scene]
        else:
            present = candidates[:1] if candidates and not fallback_used and story_has_human_signal(beat_text) else []
            fallback_used = bool(present) or fallback_used

        lower = beat_text.lower()
        if empty_without_people:
            present = []
        if "alleen" in lower and present:
            if visible_mentions:
                present = visible_mentions[:1]
            else:
                present = present[:1]

        present = ordered_unique([char_id for char_id in present if char_id not in explicit_absent])
        absent = ordered_unique(
            [
                char_id
                for char_id in [*scene_cast, *active_before, *left_scene, *explicit_absent]
                if char_id not in present
            ]
        )

        casts.append(
            {
                "present": present,
                "absent": absent,
                "exiting": exits,
            }
        )

        if visible_mentions:
            last_subject = last_mention if last_mention in present else visible_mentions[-1]
        elif len(present) == 1:
            last_subject = present[0]

        if present or empty_without_people:
            active = [char_id for char_id in present if char_id not in exits]
        else:
            active = [char_id for char_id in active_before if char_id not in exits]
        left_scene = ordered_unique([*left_scene, *exits])

    return casts


def title_from_sentences(sentences: list[str], fallback: str) -> str:
    if not sentences:
        return fallback
    words = re.findall(r"[\wÀ-ÿ']+", sentences[0])
    title = " ".join(words[:7])
    return title[:1].upper() + title[1:] if title else fallback


def estimate_panel_count(scene_text: str, sentences: list[str]) -> int:
    words = word_count(scene_text)
    dialogue_marks = scene_text.count('"') + scene_text.count("“") + scene_text.count("”") + scene_text.count("'")
    action_hits = keyword_hits(
        scene_text,
        ["rent", "rennen", "valt", "vecht", "springt", "schreeuw", "pakt", "opent", "vlucht", "barst", "draait"],
    )
    count = max(1, round(words / COMIC_PANEL_TARGET_WORDS))
    if len(sentences) >= 4:
        count = max(count, min(4, len(sentences) // 2))
    if len(sentences) > 1:
        continuity_hits = keyword_hits(
            scene_text,
            [
                "daarna",
                "later",
                "ondertussen",
                "vertrekt",
                "verlaat",
                "verdwijnt",
                "loopt weg",
                "zonder",
                "alleen",
                "leeg",
                "niemand",
                "niet meer zichtbaar",
            ],
        )
        if continuity_hits:
            count = max(count, min(5, len(sentences)))
    if dialogue_marks >= 4:
        count += 1
    if len(action_hits) >= 2:
        count += 1
    return max(1, min(5, count))


def split_evenly(items: list[str], groups: int) -> list[list[str]]:
    if not items:
        return [[] for _ in range(groups)]
    groups = max(1, min(groups, max(groups, len(items))))
    result = []
    for index in range(groups):
        start = round(index * len(items) / groups)
        end = round((index + 1) * len(items) / groups)
        result.append(items[start:end] or [items[min(index, len(items) - 1)]])
    return result


def story_analysis_chunks(text: str) -> list[dict[str, Any]]:
    sentences = split_sentences(text)
    if not sentences:
        return []
    chunks: list[dict[str, Any]] = []
    current: list[str] = []
    current_words = 0
    current_start = 0

    def flush(end_sentence: int) -> None:
        nonlocal current, current_words, current_start
        if not current:
            return
        chunk_text = " ".join(current).strip()
        chunks.append(
            {
                "chunkNumber": len(chunks) + 1,
                "startSentence": current_start,
                "endSentence": end_sentence,
                "wordCount": word_count(chunk_text),
                "text": chunk_text,
            }
        )
        overlap = current[-COMIC_ANALYSIS_CHUNK_OVERLAP_SENTENCES:]
        current = list(overlap)
        current_words = sum(word_count(sentence) for sentence in current)
        current_start = max(0, end_sentence - len(current) + 1)

    for sentence_index, sentence in enumerate(sentences):
        sentence_words = word_count(sentence)
        if (
            current
            and current_words >= int(COMIC_ANALYSIS_CHUNK_TARGET_WORDS * 0.65)
            and current_words + sentence_words > COMIC_ANALYSIS_CHUNK_TARGET_WORDS
        ):
            flush(sentence_index - 1)
        if not current:
            current_start = sentence_index
        current.append(sentence)
        current_words += sentence_words

    if current:
        chunk_text = " ".join(current).strip()
        duplicate_tail = chunks and chunk_text == str(chunks[-1].get("text") or "")
        if not duplicate_tail:
            chunks.append(
                {
                    "chunkNumber": len(chunks) + 1,
                    "startSentence": current_start,
                    "endSentence": len(sentences) - 1,
                    "wordCount": word_count(chunk_text),
                    "text": chunk_text,
                }
            )
    return chunks


def safe_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.values())
    return []


def text_field(item: Any, keys: list[str]) -> str:
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return ""
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def canonical_element_key(name: str) -> str:
    key = re.sub(r"\s+", " ", name.strip().lower())
    key = re.sub(r"^(?:de|het|een|the|a|an)\s+", "", key)
    key = re.sub(r"[^a-z0-9à-ÿ' -]+", "", key)
    return key.strip()


def evidence_sentence(text: str, needle: str) -> str:
    pattern = re.compile(rf"\b{re.escape(needle)}\b", re.IGNORECASE)
    for sentence in split_sentences(text):
        if pattern.search(sentence):
            return trim_text(sentence, 180)
    return ""


def element_keyword_present(text: str, keyword: str) -> bool:
    lower = text.lower()
    if keyword == "door":
        return bool(re.search(r"\b(?:a|an|the)\s+door\b", lower))
    return bool(re.search(rf"\b{re.escape(keyword)}\b", lower))


def extract_location_candidates(text: str, limit: int = 10) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    pattern = re.compile(
        r"\b(?:in|op|bij|aan|onder|boven|naast|voor|achter|door|inside|outside|near|at|on|under|above|behind)\s+"
        r"(?:de|het|een|den|der|the|a|an)?\s*([A-Za-zÀ-ÿ][^,.!?;:\n]{2,54})",
        re.IGNORECASE,
    )
    pronouns = {"haar", "hem", "hen", "hun", "mij", "me", "jou", "ons", "zijn", "ze", "hij", "her", "him", "them", "us"}
    for match in pattern.finditer(text):
        candidate = trim_text(match.group(1), 70)
        candidate = re.split(r"\b(?:waar|where|when|terwijl|while|omdat|because|en|and)\b", candidate, maxsplit=1, flags=re.IGNORECASE)[0]
        candidate = re.sub(r"\s+", " ", candidate).strip(" .,:;!?\"'“”").lower()
        words = candidate.split()
        if not words or words[0] in pronouns or len(words) > 6:
            continue
        key = canonical_element_key(candidate)
        if not key or key in NON_CHARACTER_NAME_WORDS:
            continue
        found.setdefault(
            key,
            {
                "name": candidate,
                "mentions": 0,
                "evidence": evidence_sentence(text, words[0]),
            },
        )
        found[key]["mentions"] = int(found[key].get("mentions") or 0) + 1

    for keyword, label in LOCATION_KEYWORDS.items():
        if not element_keyword_present(text, keyword):
            continue
        key = canonical_element_key(label)
        found.setdefault(
            key,
            {
                "name": label,
                "mentions": 0,
                "evidence": evidence_sentence(text, keyword),
            },
        )
        found[key]["mentions"] = int(found[key].get("mentions") or 0) + 1

    return sorted(found.values(), key=lambda item: (-int(item.get("mentions") or 0), str(item.get("name") or "")))[:limit]


def extract_object_candidates(text: str, limit: int = 12) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for keyword, label in VISUAL_OBJECT_KEYWORDS.items():
        if not element_keyword_present(text, keyword):
            continue
        key = canonical_element_key(label)
        found.setdefault(
            key,
            {
                "name": label,
                "mentions": 0,
                "evidence": evidence_sentence(text, keyword),
            },
        )
        found[key]["mentions"] = int(found[key].get("mentions") or 0) + 1
    return sorted(found.values(), key=lambda item: (-int(item.get("mentions") or 0), str(item.get("name") or "")))[:limit]


def important_event_sentences(sentences: list[str], limit: int = 8) -> list[str]:
    if not sentences:
        return []
    selected: list[str] = [sentences[0]]
    action_keys = [*PERSON_ACTION_WORDS, *EXIT_KEYWORDS, "plotseling", "opeens", "suddenly", "finally", "eindelijk"]
    for sentence in sentences[1:-1]:
        lower = sentence.lower()
        if any(key in lower for key in action_keys) or any(mark in sentence for mark in ['"', "“", "”"]):
            selected.append(sentence)
        if len(selected) >= limit - 1:
            break
    if len(sentences) > 1:
        selected.append(sentences[-1])
    return ordered_unique([trim_text(sentence, 220) for sentence in selected])[:limit]


def summarize_chunk_sentences(sentences: list[str]) -> str:
    events = important_event_sentences(sentences, 4)
    if not events:
        return ""
    return trim_text(" ".join(events), 520)


def normalize_gender(value: str) -> str:
    lower = value.lower().strip()
    if lower in {"female", "woman", "girl", "vrouw", "meisje", "feminine", "zij", "she"}:
        return "female"
    if lower in {"male", "man", "boy", "mannelijk", "jongen", "masculine", "hij", "he"}:
        return "male"
    if lower in {"nonbinary", "non-binary", "non binair", "non-binair"}:
        return "nonbinary"
    return "unknown"


def infer_character_gender(name: str, contexts: list[str]) -> str:
    female = 0
    male = 0
    escaped = re.escape(name)
    first_name = canonical_element_key(name).split()[0] if canonical_element_key(name).split() else canonical_element_key(name)
    if first_name in FEMALE_NAME_HINTS:
        female += 2
    if first_name in MALE_NAME_HINTS:
        male += 2
    for context in contexts:
        words = lower_word_set(context)
        female += len(words & FEMALE_SIGNAL_WORDS)
        male += len(words & MALE_SIGNAL_WORDS)
        if re.search(rf"\b{escaped}\b\s*,\s*(?:her|his|their|haar|zijn|hun)?\s*(?:sister|mother|daughter|wife|zus|moeder|dochter|vrouw)\b", context, re.IGNORECASE):
            female += 3
        if re.search(rf"\b{escaped}\b\s*,\s*(?:her|his|their|haar|zijn|hun)?\s*(?:brother|father|son|husband|broer|vader|zoon|man)\b", context, re.IGNORECASE):
            male += 3
    if female > male:
        return "female"
    if male > female:
        return "male"
    return "unknown"


def sanitize_character_candidate_name(name: str, story_text: str,
                                        precomputed_action_pos: list[int] | None = None,
                                        precomputed_neg_pos: list[int] | None = None) -> str | None:
    cleaned = re.sub(r"\s+", " ", str(name or "").strip(" \t\r\n\"'“”"))
    cleaned = re.sub(r"^(?:character|personage|naam|name)\s*[:#-]\s*", "", cleaned, flags=re.IGNORECASE).strip()
    if not cleaned:
        return None
    lower = cleaned.lower()
    # Explicit pronoun block (in addition to NAME_BLOCKLIST / NON list)
    pronoun_block = {
        "they", "them", "their", "theirs", "you", "your", "yours", "he", "him", "his",
        "she", "her", "hers", "we", "us", "our", "ours", "me", "my", "mine", "i", "it", "its",
        "jij", "jou", "jouw", "wij", "ons", "onze", "mij", "mijn", "ik", "hij", "zij", "ze", "hun",
    }
    if lower in pronoun_block:
        return None
    if lower in {"narrator", "verteller", "ik", "i", "me", "mij"}:
        return "Narrator" if lower_word_set(story_text) & FIRST_PERSON_WORDS else None
    if len(cleaned) > 70 or len(cleaned.split()) > 4:
        return None
    if not re.search(r"[A-Za-zÀ-ÿ]", cleaned):
        return None
    if name_candidate_blocked(cleaned):
        return None
    if cleaned != "Narrator" and not cleaned[:1].isupper():
        return None

    parts = [part for part in re.findall(r"[A-Za-zÀ-ÿ'-]+", cleaned) if part]
    if not parts:
        return None
    first = parts[0]
    if cleaned != "Narrator" and not re.search(rf"\b{re.escape(first)}\b", story_text, re.IGNORECASE):
        return None
    # New hard gate: must look like a real person/character
    if cleaned not in {"Narrator", "Protagonist"} and not is_likely_real_person(
            cleaned, story_text or "",
            precomputed_action_pos=precomputed_action_pos,
            precomputed_neg_pos=precomputed_neg_pos):
        return None
    return cleaned


def rule_story_chunk_analysis(chunk: dict[str, Any]) -> dict[str, Any]:
    text = str(chunk.get("text") or "")
    sentences = split_sentences(text)
    character_candidates = []
    for name, mentions in extract_character_names(text):
        contexts = character_contexts(name, sentences)
        character_candidates.append(
            {
                "name": name,
                "mentions": mentions,
                "gender": infer_character_gender(name, contexts),
                "visualClues": "",
                "evidence": [trim_text(context, 180) for context in contexts[:2]],
                "source": "rules",
            }
        )
    return {
        "chunkNumber": int(chunk.get("chunkNumber") or 0),
        "wordCount": int(chunk.get("wordCount") or word_count(text)),
        "summary": summarize_chunk_sentences(sentences),
        "characterCandidates": character_candidates,
        "locations": extract_location_candidates(text),
        "objects": extract_object_candidates(text),
        "events": [
            {
                "summary": sentence,
                "characterNames": [name for name, _ in extract_character_names(sentence)],
                "location": detect_location(sentence),
                "mood": detect_mood(sentence),
            }
            for sentence in important_event_sentences(sentences, 8)
        ],
        "humanSignal": story_has_human_signal(text),
        "source": "rules",
    }


def parse_json_object(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def ollama_generate_json(model: str, system_prompt: str, user_prompt: str, timeout: float = OLLAMA_PLANNER_TIMEOUT) -> dict[str, Any]:
    payload = {
        "model": model,
        "system": system_prompt,
        "prompt": user_prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.1,
            "top_p": 0.9,
            "num_ctx": 8192,
        },
    }
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8") or "{}")
    return parse_json_object(str(body.get("response") or ""))


def _http_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    for name, value in headers.items():
        req.add_header(name, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def openai_generate_json(key: str, model: str, system_prompt: str, user_prompt: str, timeout: float) -> dict[str, Any]:
    body = _http_json(
        "https://api.openai.com/v1/chat/completions",
        {
            "model": model or DEFAULT_OPENAI_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        },
        {"Authorization": f"Bearer {key}"},
        timeout,
    )
    choices = body.get("choices") or [{}]
    content = (choices[0].get("message") or {}).get("content") if isinstance(choices[0], dict) else ""
    return parse_json_object(str(content or ""))


def anthropic_generate_json(key: str, model: str, system_prompt: str, user_prompt: str, timeout: float) -> dict[str, Any]:
    body = _http_json(
        "https://api.anthropic.com/v1/messages",
        {
            "model": model or DEFAULT_ANTHROPIC_MODEL,
            "max_tokens": 2048,
            "temperature": 0.1,
            "system": f"{system_prompt} Respond with a single JSON object and nothing else.",
            "messages": [{"role": "user", "content": user_prompt}],
        },
        {"x-api-key": key, "anthropic-version": "2023-06-01"},
        timeout,
    )
    parts = body.get("content") or []
    text = parts[0].get("text") if parts and isinstance(parts[0], dict) else ""
    return parse_json_object(str(text or ""))


def gemini_generate_json(key: str, model: str, system_prompt: str, user_prompt: str, timeout: float) -> dict[str, Any]:
    model = model or DEFAULT_GEMINI_MODEL
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model)}:generateContent?key={urllib.parse.quote(key)}"
    body = _http_json(
        url,
        {
            "contents": [{"parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}]}],
            "generationConfig": {"temperature": 0.1, "response_mime_type": "application/json"},
        },
        {},
        timeout,
    )
    candidates = body.get("candidates") or [{}]
    parts = ((candidates[0].get("content") or {}).get("parts") or [{}]) if isinstance(candidates[0], dict) else [{}]
    text = parts[0].get("text") if parts and isinstance(parts[0], dict) else ""
    return parse_json_object(str(text or ""))


def xai_generate_json(key: str, model: str, system_prompt: str, user_prompt: str, timeout: float) -> dict[str, Any]:
    body = _http_json(
        f"{XAI_API_BASE_URL}/chat/completions",
        {
            "model": model or DEFAULT_GROK_MODEL,
            "messages": [
                {"role": "system", "content": f"{system_prompt} Return a single valid JSON object and nothing else."},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        },
        {"Authorization": f"Bearer {key}"},
        timeout,
    )
    choices = body.get("choices") or [{}]
    content = (choices[0].get("message") or {}).get("content") if isinstance(choices[0], dict) else ""
    return parse_json_object(str(content or ""))


def planner_generate_json(
    engine: dict[str, Any],
    system_prompt: str,
    user_prompt: str,
    timeout: float = OLLAMA_PLANNER_TIMEOUT,
) -> dict[str, Any]:
    engine_type = str(engine.get("type") or "")
    model = str(engine.get("model") or "")
    if engine_type == "ollama":
        return ollama_generate_json(model, system_prompt, user_prompt, timeout)
    if engine_type in {"openai", "anthropic", "google", "xai"}:
        key = get_provider_key(engine_type)
        if not key:
            raise RuntimeError(f"Geen API-key gekoppeld voor {engine_type}.")
        if engine_type == "openai":
            return openai_generate_json(key, model, system_prompt, user_prompt, timeout)
        if engine_type == "anthropic":
            return anthropic_generate_json(key, model, system_prompt, user_prompt, timeout)
        if engine_type == "xai":
            return xai_generate_json(key, model, system_prompt, user_prompt, timeout)
        return gemini_generate_json(key, model, system_prompt, user_prompt, timeout)
    raise RuntimeError(f"Onbekende planner-provider: {engine_type}")


def llm_story_chunk_analysis(engine: dict[str, Any], chunk: dict[str, Any], known_names: list[str]) -> dict[str, Any]:
    system_prompt = (
        "You are a strict comic-book story planner. Return valid JSON only. "
        "As a character, list ONLY real persons, animals or named sentient beings that speak, act or are referred to with he/she/they pronouns in this text. "
        "Use the actual proper name of the character. NEVER output a pronoun (they, you, me, he, she, we, i, it, jij, jou etc.) as a 'name'. "
        "Be conservative about objects, places, brands and concepts, but when a capitalized name clearly performs actions, speaks, or has agency, include it as a character (real people can have place-like or unusual names). "
        "Reject obvious non-persons: brand names like Muzak when used as music/system, pure place descriptions without person agency. "
        "If the text uses only pronouns for someone, try to resolve to a proper name from context or omit. Prefer including clearly named individuals over omitting. Use short, concrete visual facts and never invent new plot."
    )
    schema_hint = {
        "summary": "short summary of this passage",
        "characters": [
            {
                "name": "exact proper name only; use 'Narrator' only for a first-person narrator who is physically visible/acting; avoid pronouns, brands, places, objects, concepts and symbolic voices/shadows",
                "aliases": ["optional"],
                "gender": "female|male|nonbinary|unknown",
                "visualClues": "only if the text gives evidence",
                "role": "short role",
                "evidence": ["short text snippet"],
            }
        ],
        "locations": [{"name": "place", "evidence": "text evidence"}],
        "objects": [{"name": "important object", "evidence": "text evidence"}],
        "events": [
            {
                "summary": "visible event",
                "visibleCharacters": ["names that truly belong on screen"],
                "absentCharacters": ["names explicitly gone/not visible"],
                "location": "place",
                "mood": "mood",
            }
        ],
    }
    user_prompt = (
        f"Known names so far: {', '.join(known_names[:24]) or 'none'}\n"
        f"JSON schema example: {json.dumps(schema_hint, ensure_ascii=False)}\n\n"
        f"Analyze chunk {chunk.get('chunkNumber')} with {chunk.get('wordCount')} words:\n"
        f"{chunk.get('text')}"
    )
    return planner_generate_json(engine, system_prompt, user_prompt)


def merge_llm_chunk_analysis(rule_analysis: dict[str, Any], llm_analysis: dict[str, Any], chunk_text: str,
                               precomputed_action_pos: list[int] | None = None,
                               precomputed_neg_pos: list[int] | None = None) -> dict[str, Any]:
    merged = dict(rule_analysis)
    summary = text_field(llm_analysis, ["summary", "samenvatting"])
    if 4 <= word_count(summary) <= 90:
        merged["summary"] = trim_text(summary, 520)

    character_candidates = list(merged.get("characterCandidates") or [])
    for item in safe_list(llm_analysis.get("characters") or llm_analysis.get("characterCandidates")):
        name = sanitize_character_candidate_name(text_field(item, ["name", "naam"]), chunk_text,
                                                 precomputed_action_pos=precomputed_action_pos,
                                                 precomputed_neg_pos=precomputed_neg_pos)
        if not name:
            continue
        evidence = safe_list(item.get("evidence") if isinstance(item, dict) else None)
        aliases = safe_list(item.get("aliases") if isinstance(item, dict) else None)
        character_candidates.append(
            {
                "name": name,
                "aliases": [str(alias) for alias in aliases if isinstance(alias, str)][:4],
                "mentions": max(1, len(re.findall(rf"\b{re.escape(name.split()[0])}\b", chunk_text, re.IGNORECASE))),
                "gender": normalize_gender(text_field(item, ["gender", "geslacht"])),
                "visualClues": trim_text(text_field(item, ["visualClues", "appearance", "uiterlijk"]), 180),
                "role": trim_text(text_field(item, ["role", "rol"]), 90),
                "evidence": [trim_text(str(part), 180) for part in evidence if isinstance(part, str)][:3],
                "source": "ollama",
            }
        )
    merged["characterCandidates"] = character_candidates

    for key, output_key in [("locations", "locations"), ("objects", "objects")]:
        items = list(merged.get(output_key) or [])
        for item in safe_list(llm_analysis.get(key)):
            name = trim_text(text_field(item, ["name", "naam"]), 80).strip(" .,:;!?\"'“”").lower()
            if not name or len(name.split()) > 6:
                continue
            if output_key == "locations" and canonical_element_key(name) in NON_CHARACTER_NAME_WORDS:
                continue
            evidence = trim_text(text_field(item, ["evidence", "bewijs"]), 180)
            items.append({"name": name, "mentions": 1, "evidence": evidence, "source": "ollama"})
        merged[output_key] = items

    events = list(merged.get("events") or [])
    for item in safe_list(llm_analysis.get("events")):
        summary = trim_text(text_field(item, ["summary", "samenvatting"]), 220)
        if word_count(summary) < 3:
            continue
        events.append(
            {
                "summary": summary,
                "characterNames": [str(name) for name in safe_list(item.get("visibleCharacters") if isinstance(item, dict) else None) if isinstance(name, str)][:8],
                "absentCharacterNames": [str(name) for name in safe_list(item.get("absentCharacters") if isinstance(item, dict) else None) if isinstance(name, str)][:8],
                "location": trim_text(text_field(item, ["location", "plek"]), 80),
                "mood": trim_text(text_field(item, ["mood", "stemming"]), 40),
                "source": "ollama",
            }
        )
    merged["events"] = events[:14]
    merged["source"] = "rules+ollama"
    return merged


def available_ollama_models() -> list[str]:
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=1.2) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except Exception:
        return []
    models = payload.get("models")
    if not isinstance(models, list):
        return []
    names = [str(model.get("name") or "") for model in models if isinstance(model, dict) and model.get("name")]
    return sorted(set(names))


def planner_engine(planner_id: str) -> dict[str, Any]:
    if planner_id.startswith("ollama:"):
        model = planner_id.removeprefix("ollama:")
        return {
            "id": planner_id,
            "type": "ollama",
            "model": model,
            "label": f"Ollama: {model}",
        }
    if planner_id in CLOUD_PLANNER_IDS:
        provider, model = CLOUD_PLANNER_IDS[planner_id]
        return {
            "id": planner_id,
            "type": provider,
            "model": model,
            "label": f"{PROVIDER_BY_ID.get(provider, {}).get('label', provider)}: {model}",
        }
    return {
        "id": "local_rules",
        "type": "local_rules",
        "model": "",
        "label": "Lokale regels",
    }


def analyze_story_chunk(chunk: dict[str, Any], engine: dict[str, Any], known_names: list[str],
                          precomputed_action_pos: list[int] | None = None,
                          precomputed_neg_pos: list[int] | None = None) -> dict[str, Any]:
    rule_analysis = rule_story_chunk_analysis(chunk)
    if engine.get("type") not in LLM_ENGINE_TYPES:
        return rule_analysis
    try:
        llm_analysis = llm_story_chunk_analysis(engine, chunk, known_names)
        if not llm_analysis:
            raise ValueError("Planner gaf geen JSON-object terug.")
        return merge_llm_chunk_analysis(rule_analysis, llm_analysis, str(chunk.get("text") or ""),
                                        precomputed_action_pos=precomputed_action_pos,
                                        precomputed_neg_pos=precomputed_neg_pos)
    except Exception as exc:  # noqa: BLE001
        rule_analysis["plannerError"] = f"Planner fallback naar lokale regels: {exc}"
        return rule_analysis


def character_group_key(name: str) -> str:
    lower = canonical_element_key(name)
    if lower in {"narrator", "protagonist"}:
        return lower
    first = lower.split()[0] if lower.split() else lower
    return first or lower


def count_name_mentions(text: str, aliases: list[str]) -> int:
    total = 0
    seen = set()
    for alias in aliases:
        clean = alias.strip()
        if not clean or clean.lower() in seen or clean in {"Narrator", "Protagonist"}:
            continue
        seen.add(clean.lower())
        total += len(re.findall(rf"\b{re.escape(clean)}\b", text, re.IGNORECASE))
    if any(alias in {"Narrator", "Protagonist"} for alias in aliases):
        total += len(lower_word_set(text) & FIRST_PERSON_WORDS)
    return total


def first_name_position(text: str, aliases: list[str]) -> int:
    positions = []
    for alias in aliases:
        if alias in {"Narrator", "Protagonist"}:
            first_person_matches = [match.start() for match in re.finditer(r"\b(?:ik|i|me|mij)\b", text, re.IGNORECASE)]
            positions.extend(first_person_matches)
            continue
        match = re.search(rf"\b{re.escape(alias)}\b", text, re.IGNORECASE)
        if match:
            positions.append(match.start())
    return min(positions) if positions else 10**12


def merge_character_cards(story: str, sentences: list[str], chunk_analyses: list[dict[str, Any]],
                          precomputed_action_pos: list[int] | None = None,
                          precomputed_neg_pos: list[int] | None = None) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}

    def add_candidate(candidate: dict[str, Any], chunk_number: int | None = None) -> None:
        name = sanitize_character_candidate_name(str(candidate.get("name") or ""), story,
                                                 precomputed_action_pos=precomputed_action_pos,
                                                 precomputed_neg_pos=precomputed_neg_pos)
        if not name:
            return
        aliases = [name]
        for alias in safe_list(candidate.get("aliases")):
            clean_alias = sanitize_character_candidate_name(str(alias), story,
                                                            precomputed_action_pos=precomputed_action_pos,
                                                            precomputed_neg_pos=precomputed_neg_pos)
            if clean_alias:
                aliases.append(clean_alias)
        key = character_group_key(name)
        group = groups.setdefault(
            key,
            {
                "names": {},
                "aliases": set(),
                "mentions": 0,
                "evidence": [],
                "chunks": set(),
                "genders": [],
                "visualClues": [],
                "roles": [],
            },
        )
        for alias in aliases:
            group["aliases"].add(alias)
            group["names"][alias] = int(group["names"].get(alias, 0)) + int(candidate.get("mentions") or 1)
        group["mentions"] += int(candidate.get("mentions") or 1)
        if chunk_number:
            group["chunks"].add(chunk_number)
        gender = normalize_gender(str(candidate.get("gender") or ""))
        if gender != "unknown":
            group["genders"].append(gender)
        visual = trim_text(str(candidate.get("visualClues") or ""), 160)
        if visual:
            group["visualClues"].append(visual)
        role = trim_text(str(candidate.get("role") or ""), 80)
        if role:
            group["roles"].append(role)
        for evidence in safe_list(candidate.get("evidence")):
            if isinstance(evidence, str) and evidence.strip():
                group["evidence"].append(trim_text(evidence, 180))

    for name, mentions in extract_character_names(story):
        add_candidate({"name": name, "mentions": mentions, "evidence": character_contexts(name, sentences), "source": "rules"})

    for analysis in chunk_analyses:
        chunk_number = int(analysis.get("chunkNumber") or 0)
        for candidate in safe_list(analysis.get("characterCandidates")):
            if isinstance(candidate, dict):
                add_candidate(candidate, chunk_number)

    cards: list[dict[str, Any]] = []
    ordered_groups = sorted(
        groups.values(),
        key=lambda group: (
            first_name_position(story, sorted(group["aliases"])),
            -count_name_mentions(story, sorted(group["aliases"])),
        ),
    )
    # Final hard filter: only real persons/characters survive to the bible and thus to portraits + cast
    filtered_groups = []
    for g in ordered_groups:
        aliases = sorted(g["aliases"], key=lambda alias: (-int(g["names"].get(alias, 0)), -len(alias), alias))
        canonical = aliases[0]
        if canonical in {"Narrator", "Protagonist"} or is_likely_real_person(canonical, story):
            # also require at least some evidence of personhood for non-fallbacks
            if canonical in {"Narrator", "Protagonist"} or g.get("evidence") or count_name_mentions(story, aliases) >= 1:
                filtered_groups.append(g)
    ordered_groups = filtered_groups
    for index, group in enumerate(ordered_groups[:24], start=1):
        aliases = sorted(group["aliases"], key=lambda alias: (-int(group["names"].get(alias, 0)), -len(alias), alias))
        canonical = aliases[0]
        mentions = max(1, count_name_mentions(story, aliases) or int(group["mentions"] or 1))
        contexts = list(dict.fromkeys([str(item) for item in group["evidence"] if item]))[:5]
        gender = group["genders"][0] if group["genders"] else infer_character_gender(canonical, contexts)
        visual_hint = next((hint for hint in group["visualClues"] if hint), "")
        visual_signature = (
            visual_hint
            if visual_hint
            else (
                f"{stable_pick(canonical, HAIR_OPTIONS, 1)}, "
                f"{stable_pick(canonical, OUTFIT_OPTIONS, 2)}, "
                f"{stable_pick(canonical, MARKER_OPTIONS, 3)}"
            )
        )
        role = next((role for role in group["roles"] if role), "")
        if not role:
            role = "protagonist" if index == 1 else ("main character" if mentions >= 4 else "supporting character")
        gender_prompt = {
            "female": "female character, gender locked as female, ",
            "male": "male character, gender locked as male, ",
            "nonbinary": "nonbinary character, gender presentation locked, ",
        }.get(gender, "gender consistent with the story, ")
        cards.append(
            {
                "id": f"char_{index:02d}",
                "name": canonical,
                "aliases": aliases[1:],
                "role": role,
                "gender": gender,
                "mentions": mentions,
                "firstChunk": min(group["chunks"]) if group["chunks"] else None,
                "lastChunk": max(group["chunks"]) if group["chunks"] else None,
                "visualSignature": visual_signature,
                "continuityPrompt": (
                    f"{canonical}: consistent realistic anime character, {gender_prompt}{visual_signature}, "
                    "same face, age, gender, outfit, hair, and body proportions every single time this character is visible; this exact character only"
                ),
                "evidence": contexts,
            }
        )
    return cards


def merge_named_elements(chunk_analyses: list[dict[str, Any]], field: str, prefix: str, limit: int = 30) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for analysis in chunk_analyses:
        chunk_number = int(analysis.get("chunkNumber") or 0)
        for item in safe_list(analysis.get(field)):
            if not isinstance(item, dict):
                continue
            name = trim_text(str(item.get("name") or ""), 80).strip(" .,:;!?\"'“”").lower()
            key = canonical_element_key(name)
            if not key:
                continue
            group = groups.setdefault(
                key,
                {
                    "name": name,
                    "mentions": 0,
                    "chunks": set(),
                    "evidence": [],
                },
            )
            group["mentions"] += max(1, int(item.get("mentions") or 1))
            if chunk_number:
                group["chunks"].add(chunk_number)
            evidence = trim_text(str(item.get("evidence") or ""), 180)
            if evidence:
                group["evidence"].append(evidence)
    elements = []
    for index, group in enumerate(
        sorted(groups.values(), key=lambda item: (-int(item["mentions"]), min(item["chunks"]) if item["chunks"] else 9999, str(item["name"]))),
        start=1,
    ):
        elements.append(
            {
                "id": f"{prefix}_{index:02d}",
                "name": group["name"],
                "mentions": int(group["mentions"]),
                "firstChunk": min(group["chunks"]) if group["chunks"] else None,
                "lastChunk": max(group["chunks"]) if group["chunks"] else None,
                "evidence": list(dict.fromkeys(group["evidence"]))[:3],
            }
        )
        if len(elements) >= limit:
            break
    return elements


def extract_relationships(story: str, characters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(characters) < 2:
        return []

    by_id = {str(character.get("id") or ""): str(character.get("name") or "") for character in characters}
    relation_pattern = "|".join(re.escape(word) for word in sorted(RELATION_LABELS, key=len, reverse=True))
    relationships: dict[tuple[str, str, str], dict[str, Any]] = {}
    active: list[str] = []
    last_subject: str | None = None

    def add_relationship(source_id: str | None, target_id: str | None, relation: str, evidence: str) -> None:
        if not source_id or not target_id or source_id == target_id:
            return
        if source_id not in by_id or target_id not in by_id:
            return
        key = (source_id, target_id, relation)
        relationships.setdefault(
            key,
            {
                "sourceId": source_id,
                "source": by_id[source_id],
                "targetId": target_id,
                "target": by_id[target_id],
                "relation": relation,
                "evidence": trim_text(evidence, 180),
            },
        )

    for sentence in split_sentences(story):
        lower = sentence.lower()
        mentions = mentioned_character_ids(sentence, characters)
        last_mention = last_mentioned_character_id(sentence, characters)
        positions = character_mention_positions(sentence, characters)
        if not positions:
            if mentions:
                active = mentions
                last_subject = last_mention or mentions[-1]
            continue

        for relation_match in re.finditer(rf"\b({relation_pattern})\b", lower):
            relation_word = relation_match.group(1)
            relation = RELATION_LABELS.get(relation_word, relation_word)
            before = [position for position in positions if position[1] <= relation_match.start()]
            after = [position for position in positions if position[0] >= relation_match.end()]

            source_id: str | None = None
            target_id: str | None = None

            for _, end, char_id, _ in reversed(before):
                bridge = lower[end:relation_match.start()].strip()
                if bridge in {"'s", "’s", "haar", "zijn"}:
                    source_id = char_id
                    break

            lead = lower[max(0, relation_match.start() - 18):relation_match.start()]
            if not source_id and re.search(r"\b(?:her|haar|his|zijn)\s*$", lead):
                resolved = pronoun_referenced_character_ids(
                    sentence,
                    characters,
                    candidates=mentions or active,
                    active=active,
                    last_subject=last_subject,
                )
                source_id = resolved[0] if resolved else None
                if not source_id:
                    prior_mentions = [position for position in before if position[2] not in {source_id}]
                    if prior_mentions:
                        source_id = prior_mentions[-1][2]

            for _, _, char_id, _ in after:
                if char_id != source_id:
                    target_id = char_id
                    break
            if not target_id:
                for _, _, char_id, _ in reversed(before):
                    if char_id != source_id:
                        target_id = char_id
                        break

            add_relationship(source_id, target_id, relation, sentence)

        if mentions:
            active = mentions
            last_subject = last_mention or mentions[-1]

    return list(relationships.values())[:40]


def attach_relationships_to_characters(characters: list[dict[str, Any]], relationships: list[dict[str, Any]]) -> None:
    for character in characters:
        char_id = str(character.get("id") or "")
        character["relationships"] = [
            relationship
            for relationship in relationships
            if relationship.get("sourceId") == char_id or relationship.get("targetId") == char_id
        ][:6]


def build_world_bible(chunk_analyses: list[dict[str, Any]]) -> dict[str, Any]:
    summaries = [
        {
            "chunkNumber": int(analysis.get("chunkNumber") or 0),
            "wordCount": int(analysis.get("wordCount") or 0),
            "summary": str(analysis.get("summary") or ""),
            "plannerError": str(analysis.get("plannerError") or ""),
        }
        for analysis in chunk_analyses
    ]
    return {
        "locations": merge_named_elements(chunk_analyses, "locations", "loc"),
        "objects": merge_named_elements(chunk_analyses, "objects", "obj"),
        "relationships": [],
        "chunkSummaries": summaries,
    }


def build_story_analysis(story: str, planner_id: str, job_id: str | None = None) -> dict[str, Any]:
    engine = planner_engine(planner_id)
    chunks = story_analysis_chunks(story)
    # Precompute action and negative-cue positions once for the entire story.
    # This makes the context-understanding checks in is_likely_real_person (used for accurate
    # "only real persons get portraits" filtering) dramatically faster on long stories.
    # One O(story) pass instead of repeated full-text rescans per candidate per chunk.
    precomputed_action_pos: list[int] = []
    for word in PERSON_ACTION_WORDS:
        for m in re.finditer(rf'\b{re.escape(word)}\b', story, re.IGNORECASE):
            precomputed_action_pos.append(m.start())
    precomputed_action_pos = sorted(set(precomputed_action_pos))

    precomputed_neg_pos: list[int] = []
    place_cues = {"town of", "city of", "village of", "in the town", "in the city", "the town", "the city",
                  "the village", "the street", "the building", "the hotel", "the store"}
    brand_music_cues = {"the muzak", "muzak played", "muzak in the", "background music", "elevator music",
                        "piped music", "the system played"}
    for cue in place_cues | brand_music_cues:
        for m in re.finditer(re.escape(cue), story, re.IGNORECASE):
            precomputed_neg_pos.append(m.start())
    precomputed_neg_pos = sorted(set(precomputed_neg_pos))

    chunk_analyses: list[dict[str, Any]] = []
    known_names: list[str] = []
    total_chunks = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        if job_id:
            job_update(
                job_id,
                status="analyzing_chunk",
                analysisStage="chunks",
                currentChunk=index,
                totalChunks=total_chunks,
            )
        analysis = analyze_story_chunk(chunk, engine, known_names,
                                       precomputed_action_pos=precomputed_action_pos,
                                       precomputed_neg_pos=precomputed_neg_pos)
        chunk_analyses.append(analysis)
        for candidate in safe_list(analysis.get("characterCandidates")):
            if isinstance(candidate, dict):
                name = sanitize_character_candidate_name(str(candidate.get("name") or ""), story,
                                                         precomputed_action_pos=precomputed_action_pos,
                                                         precomputed_neg_pos=precomputed_neg_pos)
                if name and name not in known_names:
                    known_names.append(name)

    sentences = split_sentences(story)
    world = build_world_bible(chunk_analyses)

    # Separation of concerns, but not absolute: if something looks like a location/object in the world
    # bible but has clear person evidence (actions, dialogue), it can still be a character (e.g. a person
    # named after a town, or a character referred to in place-like contexts). This prevents over-filtering
    # real named persons from portraits.
    loc_names = {canonical_element_key(l.get("name", "")) for l in world.get("locations", [])}
    obj_names = {canonical_element_key(o.get("name", "")) for o in world.get("objects", [])}
    forbidden_as_characters = loc_names | obj_names

    characters = merge_character_cards(story, sentences, chunk_analyses,
                                       precomputed_action_pos=precomputed_action_pos,
                                       precomputed_neg_pos=precomputed_neg_pos)
    characters = [
        c for c in characters
        if canonical_element_key(c.get("name", "")) not in forbidden_as_characters
        or is_likely_real_person(c.get("name", ""), story,
                                  precomputed_action_pos=precomputed_action_pos,
                                  precomputed_neg_pos=precomputed_neg_pos)
    ]
    relationships = extract_relationships(story, characters)
    world["relationships"] = relationships
    attach_relationships_to_characters(characters, relationships)
    notes = []
    if engine["type"] in LLM_ENGINE_TYPES:
        errors = [str(chunk.get("plannerError")) for chunk in chunk_analyses if chunk.get("plannerError")]
        notes.append(f"Planner gebruikt: {engine.get('label') or engine['type']}.")
        if engine["type"] != "ollama":
            notes.append("Let op: verhaaltekst is naar de cloud-API van deze provider gestuurd.")
        if errors:
            notes.append(f"{len(errors)} chunk(s) vielen terug op lokale regels: {errors[0]}")
    else:
        notes.append("Lokale regelplanner gebruikt; geen verhaaltekst naar cloud of API gestuurd.")
    return {
        "pipeline": "chunked_story_bible_v2",
        "planner": engine,
        "chunkCount": total_chunks,
        "chunks": chunk_analyses,
        "world": world,
        "characters": characters,
        "notes": notes,
    }


def clarification_question(question_id: str, question: str, why: str = "", kind: str = "text") -> dict[str, str]:
    return {
        "id": question_id,
        "question": question,
        "why": why,
        "kind": kind,
    }


def build_rule_clarification_questions(
    story: str,
    characters: list[dict[str, Any]],
    world: dict[str, Any],
) -> list[dict[str, str]]:
    lower = story.lower()
    questions: list[dict[str, str]] = [
        clarification_question(
            "canon_characters",
            "Welke personages zijn echte, zichtbare personages? Noem ook wie juist geen personage is.",
            "Dit voorkomt stille extra figuren en objecten/abstracte woorden als personage.",
        ),
        clarification_question(
            "visual_style_rules",
            "Welke vaste visuele regels moeten gelden? Denk aan tijdperk, kleding, leeftijd, kleur, sfeer en realisme.",
            "De beeldmodellen veranderen figuren sneller als het uiterlijk niet expliciet vastligt.",
        ),
        clarification_question(
            "metaphors",
            "Welke woorden of gebeurtenissen zijn figuurlijk bedoeld en mogen niet letterlijk getekend worden?",
            "Bijvoorbeeld: stemmen, schaduwen, innerlijke pijn, herinneringen, dromen of symbolische zinnen.",
        ),
        clarification_question(
            "never_show",
            "Zijn er personages, silhouetten, dieren of achtergrondfiguren die nooit zomaar toegevoegd mogen worden?",
            "Dit wordt als negatieve prompt en cast-regel meegenomen.",
        ),
    ]

    uncertain = [
        str(character.get("name") or "")
        for character in characters
        if normalize_gender(str(character.get("gender") or "")) == "unknown"
    ][:6]
    if uncertain:
        questions.append(
            clarification_question(
                "character_appearance",
                f"Kun je het vaste uiterlijk van deze personages aanvullen: {', '.join(uncertain)}?",
                "Dit wordt aan de character bible toegevoegd.",
            )
        )

    metaphor_cues = [
        "shadow", "shadows", "voice", "voices", "silence", "memory", "memories", "dream", "dreams",
        "darkness", "light", "pain", "fear", "hope", "heart", "soul", "ghost", "angel", "demon",
    ]
    found_cues = [cue for cue in metaphor_cues if re.search(rf"\b{re.escape(cue)}\b", lower)]
    if found_cues:
        questions.append(
            clarification_question(
                "literal_or_symbolic",
                f"Zijn deze elementen fysiek zichtbaar of alleen figuurlijk: {', '.join(found_cues[:10])}?",
                "Dit helpt voorkomen dat symboliek als extra personage of object verschijnt.",
            )
        )

    objects = [str(item.get("name") or "") for item in safe_list(world.get("objects")) if item.get("name")][:8]
    if objects:
        questions.append(
            clarification_question(
                "object_continuity",
                f"Welke objecten moeten consequent terugkomen of juist verdwijnen? Gevonden objecten: {', '.join(objects)}.",
                "Belangrijke objecten lopen nu door in de 4-panel continuity checks.",
            )
        )

    return questions[:8]


def llm_clarification_questions(
    engine: dict[str, Any],
    story: str,
    characters: list[dict[str, Any]],
    world: dict[str, Any],
    global_summary: str,
) -> list[dict[str, str]]:
    if engine.get("type") not in LLM_ENGINE_TYPES:
        return []
    character_names_for_prompt = [str(character.get("name") or "") for character in characters if character.get("name")]
    world_digest = {
        "characters": character_names_for_prompt[:20],
        "locations": [item.get("name") for item in safe_list(world.get("locations"))[:12] if isinstance(item, dict)],
        "objects": [item.get("name") for item in safe_list(world.get("objects"))[:12] if isinstance(item, dict)],
        "relationships": safe_list(world.get("relationships"))[:12],
    }
    system_prompt = (
        "You are preparing a story-to-comic planning interview. Return JSON only. "
        "Ask short questions that help prevent wrong characters, literalized metaphors, continuity errors, and changing character designs. "
        "Do not ask about panel count; panels are grouped automatically in sets of four."
    )
    schema_hint = {
        "questions": [
            {"id": "short_snake_case", "question": "Dutch question for the user", "why": "short Dutch reason"}
        ]
    }
    user_prompt = (
        f"Story summary: {trim_text(global_summary, 900)}\n"
        f"Detected bible: {json.dumps(world_digest, ensure_ascii=False)}\n"
        f"JSON schema: {json.dumps(schema_hint, ensure_ascii=False)}\n\n"
        "Ask 4-6 useful Dutch questions. Focus on what the AI cannot safely infer."
    )
    try:
        payload = planner_generate_json(engine, system_prompt, user_prompt, timeout=OLLAMA_PANEL_PROMPT_TIMEOUT)
    except Exception:
        return []
    questions = []
    for index, item in enumerate(safe_list(payload.get("questions")), start=1):
        if not isinstance(item, dict):
            continue
        question = trim_text(str(item.get("question") or ""), 220)
        if word_count(question) < 3:
            continue
        question_id = re.sub(r"[^a-z0-9_]+", "_", str(item.get("id") or f"llm_{index}").lower()).strip("_") or f"llm_{index}"
        questions.append(
            clarification_question(
                question_id,
                question,
                trim_text(str(item.get("why") or ""), 180),
            )
        )
    return questions[:6]


def merge_questions(*question_lists: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for questions in question_lists:
        for question in questions:
            text = str(question.get("question") or "").strip()
            key = re.sub(r"\W+", " ", text.lower()).strip()
            if not text or key in seen:
                continue
            seen.add(key)
            result.append(question)
    return result[:10]


def build_story_brief(story: str, style: str, planner_id: str) -> dict[str, Any]:
    story = normalize_story_text(story)
    words = word_count(story)
    if words < 5:
        raise ValueError("Upload of plak eerst een verhaaltekst.")
    if words > COMIC_WORD_LIMIT:
        raise ValueError(f"Deze versie accepteert maximaal {COMIC_WORD_LIMIT} woorden; deze tekst heeft {words} woorden.")

    analysis = build_story_analysis(story, planner_id)
    engine = analysis.get("planner") or {"type": "local_rules"}
    characters = list(analysis.get("characters") or [])
    world = analysis.get("world") or {}
    global_summary = build_global_story_summary(engine, list(analysis.get("chunks") or []))
    rule_questions = build_rule_clarification_questions(story, characters, world)
    llm_questions = llm_clarification_questions(engine, story, characters, world, global_summary)
    questions = merge_questions(llm_questions, rule_questions)

    return {
        "briefId": hashlib.sha256(f"{story}|{planner_id}|{style}".encode("utf-8")).hexdigest()[:16],
        "title": title_from_sentences(split_sentences(story), "Story briefing"),
        "wordCount": words,
        "style": style,
        "planner": engine,
        "pipeline": "story_brief_v1",
        "globalSummary": global_summary,
        "characters": characters,
        "world": world,
        "questions": questions,
        "notes": [
            "Beantwoord vooral wat de AI niet veilig kan raden.",
            "Alles wat je hier invult wordt gebruikt als canon bij Maak strip.",
        ],
        "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def compile_user_guidance(story_brief: dict[str, Any] | None, story_answers: dict[str, Any] | None) -> str:
    if not isinstance(story_answers, dict):
        return ""
    lines: list[str] = []
    answers = story_answers.get("answers")
    if isinstance(answers, dict):
        question_map = {}
        if isinstance(story_brief, dict):
            for question in safe_list(story_brief.get("questions")):
                if isinstance(question, dict):
                    question_map[str(question.get("id") or "")] = str(question.get("question") or "")
        for question_id, answer in answers.items():
            clean = trim_text(str(answer or ""), 400)
            if not clean:
                continue
            label = question_map.get(str(question_id), str(question_id))
            lines.append(f"{label}: {clean}")

    character_notes = story_answers.get("characterNotes")
    if isinstance(character_notes, dict):
        for character_id, note in character_notes.items():
            clean = trim_text(str(note or ""), 300)
            if clean and not note_says_not_a_character(clean):
                lines.append(f"Character note {character_id}: {clean}")

    global_notes = trim_text(str(story_answers.get("globalNotes") or ""), 600)
    if global_notes:
        lines.append(f"General user canon: {global_notes}")
    return trim_text(" ".join(lines), 1800)


def note_says_not_a_character(note: str) -> bool:
    lower = note.lower()
    return any(
        phrase in lower
        for phrase in [
            "not a character", "not a person", "remove character", "geen personage",
            "geen karakter", "niet een personage", "niet als personage",
            "verwijder personage", "verwijder karakter", "haal uit cast",
        ]
    )


def compile_negative_guidance(story_answers: dict[str, Any] | None) -> str:
    if not isinstance(story_answers, dict):
        return ""
    lines: list[str] = []
    answers = story_answers.get("answers")
    if isinstance(answers, dict):
        for question_id, answer in answers.items():
            clean = trim_text(str(answer or ""), 420)
            if not clean:
                continue
            qid = str(question_id or "").lower()
            lower = clean.lower()
            if (
                any(key in qid for key in ("never", "forbid", "metaphor", "literal", "symbolic", "nooit", "verboden"))
                or re.search(r"\b(no|not|never|geen|niet|nooit|zonder|figurative|figuurlijk|metaphor|metafoor)\b", lower)
            ):
                lines.append(clean)

    character_notes = story_answers.get("characterNotes")
    if isinstance(character_notes, dict):
        for character_id, note in character_notes.items():
            clean = trim_text(str(note or ""), 180)
            if note_says_not_a_character(clean):
                lines.append(f"{character_id} as a person or visible character")

    global_notes = trim_text(str(story_answers.get("globalNotes") or ""), 420)
    if re.search(r"\b(no|not|never|geen|niet|nooit|zonder|only|alleen)\b", global_notes.lower()):
        lines.append(global_notes)
    return trim_text("; ".join(lines), 1200)


def apply_story_answers_to_characters(
    characters: list[dict[str, Any]],
    story_answers: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(story_answers, dict):
        return characters
    character_notes = story_answers.get("characterNotes")
    if not isinstance(character_notes, dict):
        return characters
    result: list[dict[str, Any]] = []
    for character in characters:
        char_id = str(character.get("id") or "")
        note = trim_text(str(character_notes.get(char_id) or ""), 320)
        if note and note_says_not_a_character(note):
            continue
        if note:
            updated = dict(character)
            updated["userNotes"] = note
            updated["visualSignature"] = trim_text(f"{character.get('visualSignature', '')}; user-approved canon: {note}", 500)
            updated["continuityPrompt"] = trim_text(
                f"{character.get('continuityPrompt', '')}; user-approved canon: {note}; keep this exact design consistent",
                850,
            )
            result.append(updated)
        else:
            result.append(character)
    return result


def filter_relationships_for_characters(world: dict[str, Any], characters: list[dict[str, Any]]) -> None:
    valid_ids = {str(character.get("id") or "") for character in characters}
    relationships = [
        relationship
        for relationship in safe_list(world.get("relationships"))
        if isinstance(relationship, dict)
        and str(relationship.get("sourceId") or "") in valid_ids
        and str(relationship.get("targetId") or "") in valid_ids
    ]
    world["relationships"] = relationships
    attach_relationships_to_characters(characters, relationships)


def character_prompt(character_ids: list[str], characters: list[dict[str, Any]]) -> str:
    by_id = {str(character["id"]): character for character in characters}
    prompts = [by_id[char_id]["continuityPrompt"] for char_id in character_ids if char_id in by_id]
    return "; ".join(str(prompt) for prompt in prompts)


def absent_character_visual_prompt(character_ids: list[str], characters: list[dict[str, Any]], limit: int = 8) -> str:
    by_id = {str(character["id"]): character for character in characters}
    parts = []
    for char_id in character_ids[:limit]:
        character = by_id.get(char_id)
        if not character:
            continue
        parts.append(f"{character['name']} ({character.get('visualSignature', '')})")
    return "; ".join(parts)


def build_panel_negative_prompt(
    absent_ids: list[str],
    characters: list[dict[str, Any]],
    present_ids: list[str] | None = None,
    negative_guidance: str = "",
) -> str:
    # Keep all negations here; diffusion models follow "do not" cues poorly in the positive prompt.
    parts = [COMIC_NEGATIVE_PROMPT]
    absent_prompt = absent_character_visual_prompt(absent_ids, characters)
    if absent_prompt:
        parts.append(f"absent or off-screen characters must not appear: {absent_prompt}")
    parts.append("extra people, background crowd, unwanted companion, duplicate named character, stray person, unnamed figure, extra face, silhouette, background human, additional character, unlisted person")
    if present_ids is not None and not present_ids:
        parts.append("people, person, human figure, silhouette, face, crowd, any living being")
    # Always reinforce: only the listed cast (when present_ids provided)
    if present_ids:
        parts.append("any human or character not explicitly listed in the positive prompt")
    if negative_guidance.strip():
        parts.append(f"user-forbidden elements and literalizations: {trim_text(negative_guidance, 420)}")
        parts.append(
            "literalized metaphor, personified emotion, symbolic extra figure, visualized thought as person, "
            "visualized voice as person, unwanted silent character, extra witness, invented bystander"
        )
    return ", ".join(parts)


def llm_panel_visual_prompt(
    engine: dict[str, Any],
    beat_text: str,
    scene: dict[str, Any],
    visible_names: list[str],
    absent_names: list[str],
    story_context: str = "",
    timeout: float = OLLAMA_PANEL_PROMPT_TIMEOUT,
) -> str:
    system_prompt = (
        "You turn one comic-book story beat into a single compact English image description "
        "for a text-to-image model. Describe ONLY what is literally visible in this beat. "
        "Use the story context and scene summary ONLY to resolve pronouns and ambiguity; "
        "never copy events from them into this panel. "
        "CRITICAL CAST FIDELITY: describe ONLY characters from the allowed visible list if any; never invent, name or describe any extra people, faces, silhouettes, figures, crowds or animals. "
        "If the visible list is empty, the scene MUST contain zero humans or animals. "
        "Never add characters, objects, places or actions that are not in this beat. "
        "No dialogue, no narration, no story explanation, no quotation marks. Return JSON only."
    )
    cast_line = ", ".join(visible_names) if visible_names else "no people, empty scene"
    absent_line = ", ".join(absent_names) if absent_names else "none"
    schema_hint = {"visual": "one English sentence describing the visible scene, 12-40 words"}
    context_lines = ""
    if story_context.strip():
        context_lines += f"Story context (do NOT draw this, only for disambiguation): {trim_text(story_context, 500)}\n"
    if str(scene.get("summary") or "").strip():
        context_lines += f"Scene summary (do NOT draw this, only for disambiguation): {trim_text(str(scene.get('summary')), 300)}\n"
    user_prompt = (
        f"{context_lines}"
        f"Characters who may be visible (EXACTLY these, or none): {cast_line}\n"
        f"Characters that must NOT appear: {absent_line}\n"
        f"Location: {scene.get('location')}\n"
        f"Mood: {scene.get('mood')}\n"
        f"JSON schema: {json.dumps(schema_hint, ensure_ascii=False)}\n\n"
        f"Story beat (draw ONLY this; obey visible cast strictly):\n{trim_text(beat_text, 600)}"
    )
    result = planner_generate_json(engine, system_prompt, user_prompt, timeout)
    visual = text_field(result, ["visual", "description", "prompt"])
    return trim_text(visual, 420)


FIGURATIVE_VISUAL_WORDS = {
    "voice", "voices", "silence", "sentence", "thought", "thoughts", "memory", "memories",
    "dream", "dreams", "heart", "mind", "soul", "life", "fear", "pain", "hope", "darkness",
    "light", "shadow", "shadows", "feeling", "feelings",
    "stem", "stemmen", "stilte", "zin", "gedachte", "gedachten", "herinnering", "herinneringen",
    "droom", "dromen", "hart", "hoofd", "ziel", "leven", "angst", "pijn", "hoop", "gevoel",
}


def remove_dialogue_text(text: str) -> str:
    text = re.sub(r'"[^"\n]{0,400}"', ". ", text)
    text = re.sub(r"“[^”\n]{0,400}”", ". ", text)
    text = re.sub(r"‘[^’\n]{0,400}’", ". ", text)
    return text


def has_figurative_visual_cue(text: str) -> bool:
    lower = text.lower()
    if any(phrase in lower for phrase in [
        "trapped thing", "own life", "in her heart", "in his heart", "in their heart",
        "in my heart", "in my own life", "felt like", "feels like", "as if", "as though",
        "like a wall", "voice was", "voice is", "silence was", "silence is",
    ]):
        return True
    words = set(re.findall(r"[a-zÀ-ÿ']+", lower))
    return bool(words & FIGURATIVE_VISUAL_WORDS and re.search(r"\b(was|were|is|are|felt|feels|seemed|became|becomes|was|voelde|leek|werd|is)\b", lower))


def strip_figurative_clause(sentence: str) -> str:
    clean = sentence.strip()
    for connector in [", but ", " but ", "; but ", ", as if ", " as if ", ", as though ", " as though ", ", like ", " like "]:
        lower = clean.lower()
        index = lower.find(connector)
        if index > 0 and has_figurative_visual_cue(clean[index + len(connector):]):
            clean = clean[:index].strip(" ,;:")
    clean = re.sub(r"\b[Ii]\s+[^.?!]{0,180}\b(?:thought|wondered|realized|knew)\b[^.?!]*", " ", clean)
    clean = re.sub(r"\b(?:he|she|they|I)\s+thought\b[^.?!]*", " ", clean, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", clean).strip(" ,;:")


def sentence_is_inner_or_symbolic(sentence: str) -> bool:
    lower = sentence.lower()
    if re.match(r"^\s*(?:i|my|mijn|ik)\b", lower) and re.search(r"\b(thought|felt|knew|wondered|dacht|voelde|wist)\b", lower):
        return True
    if re.search(r"\b(?:thought|wondered|inner|inside her|inside him|inside me|dacht|gedachte)\b", lower):
        return True
    return has_figurative_visual_cue(lower) and not re.search(
        r"\b(walk|walked|run|ran|sit|sat|stand|stood|shake|shook|cry|cried|tear|tears|open|opened|hold|held|"
        r"pick|picked|give|gave|look|looked|turn|turned|enter|entered|leave|left|smile|smiled|"
        r"loopt|liep|rent|rende|zit|zat|staat|stond|pakt|pakte|geeft|gaf|kijkt|keek)\b",
        lower,
    )


def local_visible_beat_text(beat_text: str, scene: dict[str, Any], visible_names: list[str]) -> str:
    text = remove_dialogue_text(beat_text)
    cleaned_sentences: list[str] = []
    for sentence in split_sentences(text):
        stripped = strip_figurative_clause(sentence)
        stripped = re.sub(r"\s+([,.!?;:])", r"\1", stripped).strip(" .,!?:;")
        if not stripped or sentence_is_inner_or_symbolic(stripped):
            continue
        if word_count(stripped) < 2:
            continue
        cleaned_sentences.append(stripped)
    visual = trim_text(". ".join(cleaned_sentences), 360)
    if word_count(visual) >= 4:
        return visual
    location = str(scene.get("location") or "the scene")
    if visible_names:
        return trim_text(f"{', '.join(visible_names[:3])} in a grounded visible moment at {location}", 220)
    return trim_text(f"empty grounded view of {location}, important objects only", 180)


def grounded_panel_text(
    engine: dict[str, Any],
    beat_text: str,
    scene: dict[str, Any],
    visible_names: list[str],
    absent_names: list[str],
    story_context: str = "",
) -> str:
    # When the LLM planner is active, distil the raw beat into a tight, visual-only prompt.
    # Any failure or sign of hallucination falls back to a local visual-only cleanup, never the raw beat.
    local_fallback = local_visible_beat_text(beat_text, scene, visible_names)
    if engine.get("type") not in LLM_ENGINE_TYPES:
        return local_fallback
    try:
        visual = llm_panel_visual_prompt(
            engine, beat_text, scene, visible_names, absent_names, story_context
        )
    except Exception:  # noqa: BLE001
        return local_fallback
    if word_count(visual) < 3:
        return local_fallback
    lowered = visual.lower()
    if has_figurative_visual_cue(lowered):
        return local_fallback
    for name in absent_names:
        first = name.split()[0].lower() if name.split() else ""
        if first and re.search(rf"\b{re.escape(first)}\b", lowered):
            return local_fallback
    # Fidelity gate: if the grounded visual introduces any new capitalized "person-like" name not in visible cast, reject (prevents hallucinations of extra characters)
    vis_firsts = {n.split()[0].lower() for n in visible_names if n}
    vis_full_lower = {n.lower() for n in visible_names}
    for m in re.finditer(r"\b[A-ZÀ-Ý][a-zÀ-ÿ'-]+(?:\s+[A-ZÀ-Ý][a-zÀ-ÿ'-]+){0,1}\b", visual):
        cand = m.group(0).strip()
        cand_l = cand.lower()
        first = cand.split()[0].lower() if cand.split() else cand_l
        if first in {"a", "the", "an", "this", "that"}:
            continue
        if cand_l not in vis_full_lower and first not in vis_firsts and first not in {"narrator", "protagonist"}:
            # unknown name introduced -> unsafe, fall back
            return local_fallback
    return visual


def build_panel_prompt(
    action_text: str,
    scene: dict[str, Any],
    character_ids: list[str],
    absent_ids: list[str],
    characters: list[dict[str, Any]],
    style: str,
    panel_index: int,
    continuity: dict[str, Any] | None = None,
    user_guidance: str = "",
) -> str:
    people = character_prompt(character_ids, characters)
    visible_names = character_names(character_ids, characters)
    style_prompt = style or "realistic anime"
    camera = SHOT_SEQUENCE[(panel_index - 1) % len(SHOT_SEQUENCE)]
    if visible_names:
        cast_list = ", ".join(visible_names)
        cast_rule = f"visible cast: only {cast_list}"
        character_detail = f"{people}, "
        human_detail = "expressive faces, natural body language, natural anatomy, correct hands, "
    else:
        cast_rule = "empty unoccupied environment, object-focused shot, zero people, zero humans, zero faces, zero silhouettes"
        character_detail = ""
        human_detail = ""
    focus_objects = []
    if continuity:
        focus_objects = [str(item) for item in safe_list(continuity.get("focusObjects")) if str(item).strip()]
    focus_detail = f"important visible objects: {', '.join(focus_objects[:5])}, " if focus_objects else ""
    canon_detail = ""
    if user_guidance.strip():
        canon_detail = "approved character designs, grounded literal staging, visible physical action only, "
    return (
        f"{style_prompt} comic panel, {camera}, {trim_text(action_text, 420)}, "
        f"location: {scene['location']}, mood: {scene['mood']}, {cast_rule}, "
        f"{focus_detail}{canon_detail}{character_detail}{human_detail}coherent environment continuity, "
        "cinematic lighting, detailed background, A4 graphic novel panel"
    )


def build_character_reference_prompt(character: dict[str, Any], style: str) -> str:
    style_prompt = style or "realistic anime"
    continuity = str(character.get("continuityPrompt") or character.get("visualSignature") or "")
    return (
        f"{style_prompt} character reference portrait of ONE SINGLE PERSON ONLY, {continuity}, "
        "centered, plain neutral studio background, full figure with face clearly visible, "
        "neutral expression, consistent character design, natural anatomy, correct hands, "
        "even soft lighting, sharp focus, detailed, no other people, no animals, no background figures"
    )


def build_character_reference_negative_prompt() -> str:
    return (
        f"{COMIC_NEGATIVE_PROMPT}, multiple people, two people, group, crowd, "
        "extra characters, background characters, busy background, scenery, other persons, faces in background"
    )


SAID_VERBS = (
    "said|says|asked|asks|replied|replies|answered|answers|whispered|whispers|"
    "shouted|shouts|yelled|yells|cried|cries|muttered|mutters|murmured|murmurs|"
    "exclaimed|added|continued|called|calls|responded|responds|told|tells|"
    "began|wondered|growled|snapped|sighed|laughed|hissed|smiled|grinned|chuckled|beamed"
)

DIALOGUE_QUOTE_RE = re.compile(
    r'"([^"\n]{1,300})"'
    r'|\u201c([^\u201d\n]{1,300})\u201d'
    r'|\u201e([^\u201c\u201d"\n]{1,300})[\u201c\u201d"]'
)


def _dialogue_name_map(characters: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    name_map: dict[str, tuple[str, str]] = {}
    for character in characters:
        names = [character.get("name"), *(character.get("aliases") or [])]
        for raw in names:
            token = str(raw or "").split()
            if not token:
                continue
            first = token[0].lower()
            if first and first not in name_map:
                name_map[first] = (str(character.get("id") or ""), str(character.get("name") or raw))
    return name_map


_DIALOGUE_ATTRIBUTION_PATTERNS = (
    rf"\b([a-z][\w'-]+)\s+(?:[\w'-]+\s+){{0,3}}(?:{SAID_VERBS})\b",
    rf"\b(?:{SAID_VERBS})\s+([a-z][\w'-]+)\b",
    rf"\b([a-z][\w'-]+)\s*:",
)


def _dialogue_speaker(before: str, after: str, name_map: dict[str, tuple[str, str]]) -> tuple[str, str] | None:
    # Pick the attribution physically closest to the quote, whether it sits just before
    # ("Name said, '...'") or just after ("'...,' Name said"). Distance breaks ties so a
    # neighbouring quote's tag does not steal this line.
    candidates: list[tuple[int, tuple[str, str]]] = []
    before_lower = before.lower()
    after_lower = after.lower()
    for pattern in _DIALOGUE_ATTRIBUTION_PATTERNS:
        for match in re.finditer(pattern, before_lower):
            if match.group(1) in name_map:
                candidates.append((len(before) - match.end(), name_map[match.group(1)]))
        for match in re.finditer(pattern, after_lower):
            if match.group(1) in name_map:
                candidates.append((match.start(), name_map[match.group(1)]))
    if not candidates:
        return None
    gap, speaker = min(candidates, key=lambda item: item[0])
    return speaker if gap <= 40 else None


def extract_dialogue(text: str, characters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    name_map = _dialogue_name_map(characters)
    results: list[dict[str, Any]] = []
    last_speaker: tuple[str, str] | None = None
    for match in DIALOGUE_QUOTE_RE.finditer(text):
        line = next((group for group in match.groups() if group), "").strip().rstrip(" ,;:")
        if len(line) < 2:
            continue
        before = text[max(0, match.start() - 64):match.start()]
        after = text[match.end():match.end() + 64]
        speaker = _dialogue_speaker(before, after, name_map)
        if speaker is None:
            speaker = last_speaker
        if speaker is not None:
            last_speaker = speaker
        results.append(
            {
                "speakerId": speaker[0] if speaker else "",
                "speaker": speaker[1] if speaker else "",
                "line": trim_text(line, 240),
            }
        )
    return results


def build_global_story_summary(engine: dict[str, Any], chunk_analyses: list[dict[str, Any]]) -> str:
    summaries = [str(analysis.get("summary") or "") for analysis in chunk_analyses if analysis.get("summary")]
    joined = " ".join(summary for summary in summaries if summary).strip()
    if engine.get("type") not in LLM_ENGINE_TYPES or not joined:
        return trim_text(joined, 600)
    try:
        system_prompt = (
            "You summarize a story for a comic planner. Be factual, neutral and concise. "
            "Do not invent events. Return JSON only."
        )
        schema_hint = {"summary": "3-5 sentences: who the main characters are, their relationships, and the overall arc"}
        user_prompt = (
            f"JSON schema: {json.dumps(schema_hint, ensure_ascii=False)}\n\n"
            f"Chunk summaries in order:\n{trim_text(joined, 4000)}"
        )
        result = planner_generate_json(engine, system_prompt, user_prompt)
        summary = text_field(result, ["summary", "samenvatting"])
        return trim_text(summary, 700) if word_count(summary) >= 5 else trim_text(joined, 600)
    except Exception:  # noqa: BLE001
        return trim_text(joined, 600)


def world_element_mentions(text: str, elements: list[dict[str, Any]], limit: int = 8) -> list[str]:
    lower = text.lower()
    canonical_text = canonical_element_key(text)
    matches: list[tuple[int, str]] = []
    seen: set[str] = set()
    for element in elements:
        name = str(element.get("name") or "").strip(" .,:;!?\"'“”")
        if not name:
            continue
        key = canonical_element_key(name)
        if not key or key in seen:
            continue
        name_lower = name.lower()
        hit_index = lower.find(name_lower)
        if hit_index < 0 and key:
            hit_index = canonical_text.find(key)
        if hit_index < 0:
            parts = [part for part in re.findall(r"[a-zA-ZÀ-ÿ0-9'-]+", key) if len(part) > 3]
            part_hits = [lower.find(part.lower()) for part in parts if lower.find(part.lower()) >= 0]
            hit_index = min(part_hits) if part_hits else -1
        if hit_index >= 0:
            seen.add(key)
            matches.append((hit_index, name))
    matches.sort(key=lambda item: item[0])
    return [name for _, name in matches[:limit]]


def focus_objects_for_beat(beat_text: str, world: dict[str, Any]) -> list[str]:
    from_world = world_element_mentions(beat_text, safe_list(world.get("objects")), 10)
    from_rules = [str(item.get("name") or "") for item in extract_object_candidates(beat_text, 8)]
    return _unique_text([*from_world, *from_rules])[:8]


def focus_locations_for_beat(beat_text: str, scene: dict[str, Any], world: dict[str, Any]) -> list[str]:
    from_world = world_element_mentions(beat_text, safe_list(world.get("locations")), 6)
    detected = detect_location(beat_text)
    scene_location = str(scene.get("location") or "")
    candidates = [*from_world]
    if detected and detected != "the main setting of this scene":
        candidates.append(detected)
    if scene_location:
        candidates.append(scene_location)
    return _unique_text(candidates)[:6]


def build_panel_continuity(
    beat_text: str,
    scene: dict[str, Any],
    present_ids: list[str],
    absent_ids: list[str],
    exiting_ids: list[str],
    characters: list[dict[str, Any]],
    world: dict[str, Any],
    previous_panel: dict[str, Any] | None,
    character_states: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    by_id = {str(character.get("id") or ""): character for character in characters}
    focus_objects = focus_objects_for_beat(beat_text, world)
    focus_locations = focus_locations_for_beat(beat_text, scene, world)
    scene_location = str(scene.get("location") or "").strip()
    notes: list[str] = []
    if previous_panel:
        notes.append(f"Follows panel {previous_panel.get('panelNumber')}: {trim_text(str(previous_panel.get('caption') or ''), 120)}")
    if not present_ids and scene_has_empty_cue(beat_text):
        notes.append("This panel is explicitly empty: keep all people off-screen.")
    visible_exiting_ids = [char_id for char_id in exiting_ids if char_id in present_ids]
    offscreen_exiting_ids = [char_id for char_id in exiting_ids if char_id not in present_ids]
    if visible_exiting_ids:
        notes.append("Leaving now: " + ", ".join(character_names(visible_exiting_ids, characters, 5)))
    if offscreen_exiting_ids:
        notes.append("Recently left / keep off-screen: " + ", ".join(character_names(offscreen_exiting_ids, characters, 5)))
    if focus_objects:
        notes.append("Important object continuity: " + ", ".join(focus_objects[:5]))

    relevant_ids = ordered_unique([*present_ids, *exiting_ids, *[char_id for char_id in absent_ids if character_states.get(char_id)]])
    state_rows: list[dict[str, Any]] = []
    for char_id in relevant_ids[:12]:
        character = by_id.get(char_id)
        if not character:
            continue
        previous_state = character_states.get(char_id, {})
        if char_id in present_ids:
            status = "exiting after this panel" if char_id in exiting_ids else "visible"
            location = scene_location
        elif char_id in exiting_ids:
            status = "recently left / off-screen"
            location = str(previous_state.get("location") or scene_location)
        else:
            status = "off-screen"
            location = str(previous_state.get("location") or "")
        row = {
            "id": char_id,
            "name": str(character.get("name") or ""),
            "status": status,
            "location": location,
            "lastSeenPanel": previous_state.get("lastSeenPanel"),
            "lastAction": str(previous_state.get("lastAction") or ""),
        }
        state_rows.append(row)

    previous = None
    if previous_panel:
        previous = {
            "panelNumber": previous_panel.get("panelNumber"),
            "caption": trim_text(str(previous_panel.get("caption") or ""), 160),
            "visibleCast": character_names(list(previous_panel.get("characterIds") or []), characters, 8),
        }

    return {
        "previousPanel": previous,
        "sceneLocation": scene_location,
        "focusObjects": focus_objects,
        "focusLocations": focus_locations,
        "characterStates": state_rows,
        "notes": _unique_text(notes)[:8],
    }


def update_character_states_after_panel(
    character_states: dict[str, dict[str, Any]],
    panel: dict[str, Any],
    scene: dict[str, Any],
    characters: list[dict[str, Any]],
) -> None:
    by_id = {str(character.get("id") or ""): character for character in characters}
    scene_location = str(scene.get("location") or "").strip()
    caption = trim_text(str(panel.get("caption") or ""), 140)
    for char_id in list(panel.get("characterIds") or []):
        if char_id not in by_id:
            continue
        character_states[char_id] = {
            "status": "visible",
            "location": scene_location,
            "lastSeenPanel": panel.get("panelNumber"),
            "lastAction": caption,
        }
    for char_id in list(panel.get("exitingCharacterIds") or []):
        if char_id not in by_id:
            continue
        previous = character_states.get(char_id, {})
        character_states[char_id] = {
            **previous,
            "status": "exited",
            "location": f"off-screen after {scene_location}" if scene_location else "off-screen",
            "lastSeenPanel": panel.get("panelNumber"),
            "lastAction": caption,
        }


def continuity_context_text(continuity: dict[str, Any]) -> str:
    parts: list[str] = []
    previous = continuity.get("previousPanel")
    if isinstance(previous, dict) and previous.get("caption"):
        parts.append(f"Previous panel: {previous.get('caption')}")
    focus_objects = [str(item) for item in safe_list(continuity.get("focusObjects")) if str(item).strip()]
    if focus_objects:
        parts.append("Focus objects: " + ", ".join(focus_objects[:6]))
    focus_locations = [str(item) for item in safe_list(continuity.get("focusLocations")) if str(item).strip()]
    if focus_locations:
        parts.append("Location continuity: " + ", ".join(focus_locations[:4]))
    state_lines = []
    for row in safe_list(continuity.get("characterStates"))[:8]:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        status = str(row.get("status") or "")
        location = str(row.get("location") or "")
        last_seen = row.get("lastSeenPanel")
        if not name or not status:
            continue
        detail = f"{name} is {status}"
        if location:
            detail += f" at {location}"
        if last_seen and status == "off-screen":
            detail += f", last seen in panel {last_seen}"
        state_lines.append(detail)
    if state_lines:
        parts.append("Character state: " + "; ".join(state_lines))
    notes = [str(item) for item in safe_list(continuity.get("notes")) if str(item).strip()]
    if notes:
        parts.append("Continuity notes: " + "; ".join(notes[:5]))
    return trim_text(" ".join(parts), 900)


def panel_story_context(
    global_summary: str,
    world: dict[str, Any],
    present_ids: list[str],
    absent_ids: list[str],
    continuity: dict[str, Any] | None = None,
) -> str:
    parts = [global_summary.strip()] if global_summary.strip() else []
    relevant_ids = set(present_ids) | set(absent_ids)
    relationship_lines = []
    for relationship in safe_list(world.get("relationships")):
        if not isinstance(relationship, dict):
            continue
        source_id = str(relationship.get("sourceId") or "")
        target_id = str(relationship.get("targetId") or "")
        if source_id not in relevant_ids and target_id not in relevant_ids:
            continue
        source = str(relationship.get("source") or "").strip()
        target = str(relationship.get("target") or "").strip()
        relation = str(relationship.get("relation") or "").strip()
        if source and target and relation:
            relationship_lines.append(f"{target} is {source}'s {relation}")
    if relationship_lines:
        parts.append("Relationships: " + "; ".join(_unique_text(relationship_lines)[:6]))
    if continuity:
        context = continuity_context_text(continuity)
        if context:
            parts.append(context)
    return trim_text(" ".join(parts), 1200)


def panel_set_caption_text(panel_set: dict[str, Any]) -> str:
    return " ".join(str(panel.get("caption") or "") for panel in safe_list(panel_set.get("panels")))


def panel_set_digest(panel_set: dict[str, Any], characters: list[dict[str, Any]]) -> dict[str, Any]:
    panels = [panel for panel in safe_list(panel_set.get("panels")) if isinstance(panel, dict)]
    return {
        "setNumber": panel_set.get("setNumber"),
        "panelIds": [panel.get("id") for panel in panels],
        "captions": [trim_text(str(panel.get("caption") or ""), 160) for panel in panels],
        "visibleCharacters": character_names(list(panel_set.get("visibleCharacterIds") or []), characters, 12),
        "exitingCharacters": character_names(list(panel_set.get("exitingCharacterIds") or []), characters, 12),
        "focusObjects": safe_list(panel_set.get("focusObjects"))[:10],
        "summary": panel_set.get("summary"),
    }


def has_return_cue(text: str) -> bool:
    return bool(
        re.search(
            r"\b("
            r"return|returns|returned|reappear|reappears|reappeared|back|came back|comes back|"
            r"enter|enters|entered|arrive|arrives|arrived|appear|appears|appeared|"
            r"keert terug|komt terug|kwam terug|verschijnt|verscheen|arriveert|arriveerde|"
            r"binnenkomt|binnenkwam|terug"
            r")\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def local_panel_set_review(
    previous_set: dict[str, Any] | None,
    current_set: dict[str, Any],
    characters: list[dict[str, Any]],
) -> dict[str, Any]:
    notes: list[str] = []
    fixes: list[str] = []
    ok = True
    if not previous_set:
        notes.append("Eerste set: gebruikt als visuele en verhaalmatige basis voor de volgende set.")
    else:
        notes.append(
            f"Set {current_set.get('setNumber')} is gecontroleerd tegen set {previous_set.get('setNumber')}."
        )
        current_text = panel_set_caption_text(current_set)
        previous_exiting = set(str(char_id) for char_id in safe_list(previous_set.get("exitingCharacterIds")))
        current_visible = set(str(char_id) for char_id in safe_list(current_set.get("visibleCharacterIds")))
        unexpected_returns = sorted(previous_exiting & current_visible)
        if unexpected_returns and not has_return_cue(current_text):
            ok = False
            names = character_names(unexpected_returns, characters, 8)
            fixes.append(
                f"Controleer terugkeer van {names}: vorige set liet deze cast vertrekken, maar deze set toont ze zonder duidelijke terugkeer."
            )
        previous_objects = set(str(item).lower() for item in safe_list(previous_set.get("focusObjects")) if str(item).strip())
        current_objects = set(str(item).lower() for item in safe_list(current_set.get("focusObjects")) if str(item).strip())
        carried_objects = sorted(previous_objects & current_objects)
        if carried_objects:
            notes.append("Objectcontinuiteit: " + ", ".join(carried_objects[:5]))

    if not fixes:
        fixes.append("Geen automatische correcties nodig; gebruik de panelprompts om details handmatig aan te scherpen.")
    return {
        "ok": ok,
        "method": "local_continuity_rules",
        "notes": notes[:5],
        "fixes": fixes[:5],
    }


def llm_panel_set_review(
    engine: dict[str, Any],
    previous_set: dict[str, Any] | None,
    current_set: dict[str, Any],
    characters: list[dict[str, Any]],
    user_guidance: str,
) -> dict[str, Any] | None:
    if engine.get("type") not in LLM_ENGINE_TYPES or not previous_set:
        return None
    system_prompt = (
        "You are a comic continuity supervisor. Return JSON only. "
        "Compare the current 4-panel set with the previous set. "
        "Check cast continuity, character exits/returns, object continuity, literalized metaphors, and invented silent characters. "
        "Do not rewrite the story; only report issues and concise fixes."
    )
    schema_hint = {"ok": True, "notes": ["Dutch note"], "fixes": ["Dutch fix"]}
    user_prompt = (
        f"User-approved canon and metaphor rules: {trim_text(user_guidance, 900) or 'none'}\n"
        f"Previous set: {json.dumps(panel_set_digest(previous_set, characters), ensure_ascii=False)}\n"
        f"Current set: {json.dumps(panel_set_digest(current_set, characters), ensure_ascii=False)}\n"
        f"JSON schema: {json.dumps(schema_hint, ensure_ascii=False)}"
    )
    try:
        payload = planner_generate_json(engine, system_prompt, user_prompt, timeout=OLLAMA_PANEL_PROMPT_TIMEOUT)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(payload, dict):
        return None
    raw_ok = payload.get("ok", True)
    if isinstance(raw_ok, str):
        ok = raw_ok.strip().lower() not in {"false", "nee", "no", "0", "niet ok"}
    else:
        ok = bool(raw_ok)
    notes = [trim_text(str(item), 220) for item in safe_list(payload.get("notes")) if str(item).strip()]
    fixes = [trim_text(str(item), 220) for item in safe_list(payload.get("fixes")) if str(item).strip()]
    if not notes and not fixes:
        return None
    return {
        "ok": ok,
        "method": f"{engine.get('type')}_continuity_review",
        "notes": notes[:5],
        "fixes": fixes[:5] or ["Geen concrete fixes teruggegeven."],
    }


def build_panel_set_reviews(
    panels: list[dict[str, Any]],
    characters: list[dict[str, Any]],
    world: dict[str, Any],
    engine: dict[str, Any],
    user_guidance: str = "",
) -> list[dict[str, Any]]:
    panel_sets: list[dict[str, Any]] = []
    previous_set: dict[str, Any] | None = None
    for set_index, start in enumerate(range(0, len(panels), COMIC_MAX_PANELS_PER_PAGE), start=1):
        set_panels = panels[start:start + COMIC_MAX_PANELS_PER_PAGE]
        for slot, panel in enumerate(set_panels, start=1):
            panel["setNumber"] = set_index
            panel["setSlot"] = slot
        visible_ids = ordered_unique(
            [str(char_id) for panel in set_panels for char_id in safe_list(panel.get("characterIds"))]
        )
        absent_ids = ordered_unique(
            [str(char_id) for panel in set_panels for char_id in safe_list(panel.get("absentCharacterIds"))]
        )
        exiting_ids = ordered_unique(
            [str(char_id) for panel in set_panels for char_id in safe_list(panel.get("exitingCharacterIds"))]
        )
        focus_objects = _unique_text(
            [
                str(item)
                for panel in set_panels
                for item in safe_list((panel.get("continuity") or {}).get("focusObjects"))
                if str(item).strip()
            ]
        )[:10]
        focus_locations = _unique_text(
            [
                str(item)
                for panel in set_panels
                for item in safe_list((panel.get("continuity") or {}).get("focusLocations"))
                if str(item).strip()
            ]
        )[:8]
        summary = trim_text(" ".join(str(panel.get("caption") or "") for panel in set_panels), 420)
        panel_set = {
            "setNumber": set_index,
            "panelIds": [panel.get("id") for panel in set_panels],
            "panels": set_panels,
            "summary": summary,
            "visibleCharacterIds": visible_ids,
            "visibleCharacters": character_names(visible_ids, characters, 12),
            "absentCharacterIds": absent_ids,
            "exitingCharacterIds": exiting_ids,
            "focusObjects": focus_objects,
            "focusLocations": focus_locations,
        }
        review = local_panel_set_review(previous_set, panel_set, characters)
        if set_index <= COMIC_LLM_SET_REVIEW_MAX_SETS:
            llm_review = llm_panel_set_review(engine, previous_set, panel_set, characters, user_guidance)
            if llm_review:
                review = llm_review
        panel_set["review"] = review
        for panel in set_panels:
            panel["setReview"] = {
                "ok": review.get("ok", True),
                "method": review.get("method"),
                "notes": safe_list(review.get("notes"))[:3],
                "fixes": safe_list(review.get("fixes"))[:3],
            }
        panel_sets.append(panel_set)
        previous_set = panel_set

    # Keep returned set payload compact; pages/panels already carry the full panel objects.
    compact_sets = []
    for panel_set in panel_sets:
        compact = {key: value for key, value in panel_set.items() if key != "panels"}
        compact_sets.append(compact)
    return compact_sets


def build_comic_plan(
    story: str,
    style: str,
    planner_id: str,
    job_id: str | None = None,
    story_brief: dict[str, Any] | None = None,
    story_answers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    story = normalize_story_text(story)
    words = word_count(story)
    if words < 5:
        raise ValueError("Upload of plak eerst een verhaaltekst.")
    if words > COMIC_WORD_LIMIT:
        raise ValueError(f"Deze versie accepteert maximaal {COMIC_WORD_LIMIT} woorden; deze tekst heeft {words} woorden.")

    if job_id:
        job_update(job_id, status="analyzing_story", analysisStage="chunking")
    sentences = split_sentences(story)
    analysis = build_story_analysis(story, planner_id, job_id)
    story_brief = story_brief if isinstance(story_brief, dict) else {}
    story_answers = story_answers if isinstance(story_answers, dict) else {}
    user_guidance = compile_user_guidance(story_brief, story_answers)
    negative_guidance = compile_negative_guidance(story_answers)
    characters = apply_story_answers_to_characters(list(analysis.get("characters") or []), story_answers)
    engine = analysis.get("planner") or {"type": "local_rules"}
    world = analysis.get("world") or {}
    filter_relationships_for_characters(world, characters)
    if user_guidance:
        world["userGuidance"] = user_guidance
    if job_id and engine.get("type") == "ollama":
        job_update(job_id, status="analyzing_story", analysisStage="synthesis")
    global_summary = build_global_story_summary(engine, list(analysis.get("chunks") or []))
    if user_guidance:
        global_summary = trim_text(
            f"{global_summary} User-approved canon and metaphor rules: {user_guidance}",
            1400,
        )
    chunks = scene_chunks(story)
    notes = list(analysis.get("notes") or [])
    if user_guidance:
        notes.append("Gebruikersbriefing toegepast op cast, metaforen en continuity prompts.")

    scenes: list[dict[str, Any]] = []
    panels: list[dict[str, Any]] = []
    panel_number = 1
    scene_texts = [" ".join(scene_sentences) for scene_sentences in chunks]
    scene_cast_plan = timeline_casts_for_scenes(scene_texts, characters)
    all_character_ids = [str(character["id"]) for character in characters]
    character_states: dict[str, dict[str, Any]] = {}
    previous_panel: dict[str, Any] | None = None
    for scene_index, scene_sentences in enumerate(chunks, start=1):
        scene_text = " ".join(scene_sentences)
        scene_cast = scene_cast_plan[scene_index - 1] if scene_index - 1 < len(scene_cast_plan) else {"present": [], "absent": [], "exiting": []}
        scene = {
            "id": f"scene_{scene_index:03d}",
            "sceneNumber": scene_index,
            "title": title_from_sentences(scene_sentences, f"Scene {scene_index}"),
            "summary": trim_text(scene_text, 260),
            "location": detect_location(scene_text),
            "mood": detect_mood(scene_text),
            "wordCount": word_count(scene_text),
            "characterIds": list(scene_cast["present"]),
            "absentCharacterIds": list(scene_cast["absent"]),
            "exitingCharacterIds": list(scene_cast["exiting"]),
        }
        scene_panels = estimate_panel_count(scene_text, scene_sentences)
        beats = split_evenly(scene_sentences, scene_panels)
        cast_plan = panel_casts_for_scene(beats, list(scene["characterIds"]), characters)
        scene_panel_ids = []
        for beat_index, beat_sentences in enumerate(beats, start=1):
            beat_text = " ".join(beat_sentences).strip() or scene_text
            cast = cast_plan[beat_index - 1] if beat_index - 1 < len(cast_plan) else {"present": [], "absent": [], "exiting": []}
            present = list(cast["present"])
            absent = ordered_unique([*cast["absent"], *scene["absentCharacterIds"], *[char_id for char_id in all_character_ids if char_id not in present]])
            absent = [char_id for char_id in absent if char_id not in present]
            exiting = list(cast["exiting"])
            visible_names = character_names(present, characters)
            absent_names = character_names(absent, characters, 8)
            if job_id and engine.get("type") == "ollama":
                job_update(job_id, status="writing_panel_prompts", analysisStage="panel_prompts", currentPanel=panel_number)
            continuity = build_panel_continuity(
                beat_text,
                scene,
                present,
                absent,
                exiting,
                characters,
                world,
                previous_panel,
                character_states,
            )
            story_context = panel_story_context(global_summary, world, present, absent, continuity)
            action_text = grounded_panel_text(engine, beat_text, scene, visible_names, absent_names, story_context)
            panel = {
                "id": f"panel_{panel_number:04d}",
                "panelNumber": panel_number,
                "sceneId": scene["id"],
                "beatNumber": beat_index,
                "caption": trim_text(beat_text, 190),
                "visualDescription": action_text if action_text != beat_text else "",
                "dialogue": extract_dialogue(beat_text, characters),
                "continuity": continuity,
                "shot": SHOT_SEQUENCE[(panel_number - 1) % len(SHOT_SEQUENCE)],
                "characterIds": present,
                "absentCharacterIds": absent,
                "exitingCharacterIds": exiting,
                "status": "planned",
                "prompt": build_panel_prompt(
                    action_text,
                    scene,
                    present,
                    absent,
                    characters,
                    style,
                    panel_number,
                    continuity,
                    user_guidance,
                ),
                "negativePrompt": build_panel_negative_prompt(absent, characters, present, negative_guidance),
            }
            panels.append(panel)
            scene_panel_ids.append(panel["id"])
            update_character_states_after_panel(character_states, panel, scene, characters)
            previous_panel = panel
            panel_number += 1
        scene["panelIds"] = scene_panel_ids
        scenes.append(scene)

    panel_sets = build_panel_set_reviews(panels, characters, world, engine, user_guidance)
    pages = paginate_comic_panels(panels, panel_sets)
    title = title_from_sentences(sentences, "Nieuw stripverhaal")
    return {
        "title": title,
        "style": style,
        "planner": planner_id,
        "analysis": {
            "pipeline": analysis.get("pipeline"),
            "planner": analysis.get("planner"),
            "chunkCount": analysis.get("chunkCount"),
            "world": world,
            "notes": notes,
            "globalSummary": global_summary,
            "userGuidance": user_guidance,
        },
        "wordCount": words,
        "sceneCount": len(scenes),
        "panelCount": len(panels),
        "pageCount": len(pages),
        "characters": characters,
        "world": world,
        "scenes": scenes,
        "panels": panels,
        "panelSets": panel_sets,
        "pages": pages,
        "notes": notes,
        "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def paginate_comic_panels(
    panels: list[dict[str, Any]],
    panel_sets: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    set_map = {
        int(panel_set.get("setNumber")): panel_set
        for panel_set in safe_list(panel_sets)
        if isinstance(panel_set, dict) and str(panel_set.get("setNumber") or "").isdigit()
    }
    index = 0
    page_number = 1
    while index < len(panels):
        remaining = len(panels) - index
        take = min(COMIC_MAX_PANELS_PER_PAGE, remaining)
        page_panels = panels[index : index + take]
        for slot, panel in enumerate(page_panels, start=1):
            panel["pageNumber"] = page_number
            panel["slot"] = slot
        page_set = set_map.get(page_number) or {}
        pages.append(
            {
                "pageNumber": page_number,
                "setNumber": page_set.get("setNumber", page_number),
                "layout": f"layout-{len(page_panels)}",
                "setSummary": page_set.get("summary", ""),
                "setReview": page_set.get("review", {}),
                "panels": page_panels,
            }
        )
        index += take
        page_number += 1
    return pages


def load_secrets() -> dict[str, str]:
    with SECRETS_LOCK:
        try:
            data = json.loads(SECRETS_FILE.read_text("utf-8") or "{}")
        except FileNotFoundError:
            return {}
        except Exception:  # noqa: BLE001
            return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items() if isinstance(value, str) and value.strip()}


def save_secret(provider_id: str, key: str) -> None:
    if provider_id not in PROVIDER_BY_ID:
        raise ValueError("Onbekende provider.")
    with SECRETS_LOCK:
        try:
            data = json.loads(SECRETS_FILE.read_text("utf-8") or "{}")
            if not isinstance(data, dict):
                data = {}
        except Exception:  # noqa: BLE001
            data = {}
        cleaned = key.strip()
        if cleaned:
            data[provider_id] = cleaned
        else:
            data.pop(provider_id, None)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SECRETS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        try:
            SECRETS_FILE.chmod(0o600)
        except OSError:
            pass


def get_provider_key(provider_id: str) -> str:
    secret = load_secrets().get(provider_id, "").strip()
    if secret:
        return secret
    provider = PROVIDER_BY_ID.get(provider_id)
    if provider:
        return os.environ.get(provider["envVar"], "").strip()
    return ""


def mask_key(key: str) -> str:
    key = key.strip()
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return f"{key[:3]}…{key[-4:]}"


def provider_status() -> list[dict[str, Any]]:
    secrets = load_secrets()
    result: list[dict[str, Any]] = []
    for provider in PROVIDER_CATALOG:
        pid = provider["id"]
        secret = secrets.get(pid, "").strip()
        env_key = os.environ.get(provider["envVar"], "").strip()
        active = secret or env_key
        result.append(
            {
                "id": pid,
                "label": provider["label"],
                "hint": provider["hint"],
                "docs": provider["docs"],
                "envVar": provider["envVar"],
                "configured": bool(active),
                "source": "saved" if secret else ("env" if env_key else "none"),
                "masked": mask_key(active),
            }
        )
    return result


def local_planner_choices() -> list[dict[str, Any]]:
    ollama_models = available_ollama_models()
    choices: list[dict[str, Any]] = [
        {
            "id": "local_rules",
            "label": "Lokale regels",
            "provider": "local",
            "configured": True,
            "description": "Geen model, internet of API-key nodig.",
        }
    ]
    preferred = ["qwen2.5:latest", "mistral:latest", "llama3:latest", "llama3:8b"]
    for model in preferred:
        if model not in ollama_models:
            continue
        choices.append(
            {
                "id": f"ollama:{model}",
                "label": f"Ollama: {model}" + (" (aanbevolen)" if model == "qwen2.5:latest" else ""),
                "provider": "ollama",
                "configured": True,
                "recommended": model == "qwen2.5:latest",
                "description": "Lokale chunkplanner via Ollama; verhaaltekst blijft lokaal.",
            }
        )
    for model in ollama_models:
        lower_model = model.lower()
        if (
            model in preferred
            or "embed" in lower_model
            or "coder" in lower_model
            or "code" in lower_model
            or "cloud" in lower_model
        ):
            continue
        choices.append(
            {
                "id": f"ollama:{model}",
                "label": f"Ollama: {model}",
                "provider": "ollama",
                "configured": True,
                "description": "Lokale chunkplanner via Ollama; verhaaltekst blijft lokaal.",
            }
        )
    return choices


def api_planner_choices() -> list[dict[str, Any]]:
    return [
        {
            "id": "openai:env",
            "label": f"OpenAI API ({DEFAULT_OPENAI_MODEL})",
            "provider": "openai",
            "configured": bool(get_provider_key("openai")),
            "description": "Cloudplanner-adapter; API-key via de API-keys pagina of env.",
        },
        {
            "id": "anthropic:env",
            "label": f"Anthropic API ({DEFAULT_ANTHROPIC_MODEL})",
            "provider": "anthropic",
            "configured": bool(get_provider_key("anthropic")),
            "description": "Cloudplanner-adapter; API-key via de API-keys pagina of env.",
        },
        {
            "id": "gemini:env",
            "label": f"Gemini API ({DEFAULT_GEMINI_MODEL})",
            "provider": "google",
            "configured": bool(get_provider_key("google")),
            "description": "Cloudplanner-adapter; API-key via de API-keys pagina of env.",
        },
        {
            "id": "grok:env",
            "label": f"Grok API ({DEFAULT_GROK_MODEL})",
            "provider": "xai",
            "configured": bool(get_provider_key("xai")),
            "description": "Cloudplanner-adapter via xAI; API-key via de API-keys pagina of env.",
        },
    ]


def cloud_model_choices() -> list[dict[str, Any]]:
    return local_planner_choices() + api_planner_choices()


def local_model_choices(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    files = list(inventory.get("files") or [])
    choices: list[dict[str, Any]] = [
        {
            "id": "auto",
            "label": "Auto: beste lokale panelmodel",
            "provider": "local",
            "kind": "auto",
            "supported": True,
        }
    ]
    if inventory.get("zimage"):
        choices.append(
            {
                "id": "zimage_turbo",
                "label": "Z-Image Turbo - strip panel",
                "provider": "local",
                "kind": "image",
                "supported": True,
                "file": "diffusion_models/z_image_turbo_bf16.safetensors",
            }
        )
    if inventory.get("wan22"):
        choices.append(
            {
                "id": "wan22_14b_still",
                "label": "Wan 2.2 14B - video still per panel",
                "provider": "local",
                "kind": "video_still",
                "supported": True,
                "file": "diffusion_models/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
            }
        )
    if inventory.get("wan21"):
        choices.append(
            {
                "id": "wan21_1_3b_still",
                "label": "Wan 2.1 1.3B - video still per panel",
                "provider": "local",
                "kind": "video_still",
                "supported": True,
                "file": "diffusion_models/Wan2.1/wan2.1_t2v_1.3B_fp16.safetensors",
            }
        )

    for filename in files:
        suffix = Path(filename).suffix.lower()
        if suffix not in LOCAL_MODEL_EXTENSIONS:
            continue
        if filename.startswith("checkpoints/"):
            ckpt_name = filename.removeprefix("checkpoints/")
            choices.append(
                {
                    "id": f"checkpoint:{ckpt_name}",
                    "label": f"Checkpoint: {ckpt_name}",
                    "provider": "local",
                    "kind": "checkpoint",
                    "supported": True,
                    "file": filename,
                }
            )
        elif filename not in {
            "diffusion_models/z_image_turbo_bf16.safetensors",
            "diffusion_models/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
            "diffusion_models/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
            "diffusion_models/Wan2.1/wan2.1_t2v_1.3B_fp16.safetensors",
        }:
            choices.append(
                {
                    "id": f"raw:{filename}",
                    "label": f"Nog geen workflow: {filename}",
                    "provider": "local",
                    "kind": "raw",
                    "supported": False,
                    "file": filename,
                }
            )
    return choices


def select_video_model(requested: str, inventory: dict[str, Any]) -> str | None:
    if requested == "wan22_14b" and inventory["wan22"]:
        return "wan22_14b"
    if requested == "wan21_1_3b" and inventory["wan21"]:
        return "wan21_1_3b"
    if inventory["wan22"]:
        return "wan22_14b"
    if inventory["wan21"]:
        return "wan21_1_3b"
    return None


def job_update(job_id: str, **values: Any) -> None:
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(values)


def job_get(job_id: str) -> dict[str, Any] | None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return None if job is None else dict(job)


def entry_url(entry: dict[str, Any]) -> str:
    params = urllib.parse.urlencode(
        {
            "filename": entry.get("filename", ""),
            "subfolder": entry.get("subfolder", ""),
            "type": entry.get("type", "output"),
        }
    )
    return f"/api/comfy-view?{params}"


def entry_from_query(query: str) -> dict[str, Any]:
    params = urllib.parse.parse_qs(query)
    return {
        "filename": params.get("filename", [""])[0],
        "subfolder": params.get("subfolder", [""])[0],
        "type": params.get("type", ["output"])[0] or "output",
    }


def output_path_for_entry(entry: dict[str, Any], comfy_path: Path = DEFAULT_COMFY_PATH) -> Path:
    entry_type = str(entry.get("type") or "output")
    if entry_type != "output":
        raise ValueError("Alleen ComfyUI output-bestanden kunnen als video-preview worden gebruikt.")
    filename = str(entry.get("filename") or "")
    if not filename:
        raise ValueError("ComfyUI output mist een bestandsnaam.")
    subfolder = str(entry.get("subfolder") or "")
    output_root = (comfy_path / "output").resolve()
    target = (output_root / subfolder / filename).resolve()
    if not target.is_relative_to(output_root) or not target.exists() or target.is_dir():
        raise FileNotFoundError("ComfyUI output-bestand is niet gevonden.")
    if target.suffix.lower() not in {".mp4", ".webm", ".gif", ".mov"}:
        raise ValueError("ComfyUI output is geen ondersteund videobestand.")
    return target


def frame_cache_url(cache_key: str, filename: str) -> str:
    params = urllib.parse.urlencode({"cache": cache_key, "name": filename})
    return f"/api/frame?{params}"


def extract_preview_frames(
    video_path: Path,
    max_frames: int = VIDEO_FRAME_LIMIT,
    comfy_path: Path = DEFAULT_COMFY_PATH,
) -> list[str]:
    python = comfy_path / ".venv" / "bin" / "python"
    if not python.exists():
        python = Path("python3")
    stat = video_path.stat()
    cache_seed = f"{video_path.resolve()}|{stat.st_mtime_ns}|{stat.st_size}|{max_frames}|{VIDEO_FRAME_MAX_EDGE}"
    cache_key = hashlib.sha256(cache_seed.encode("utf-8")).hexdigest()[:28]
    target_dir = (FRAME_CACHE_DIR / cache_key).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    cached = sorted(target_dir.glob("frame_*.jpg"))
    if cached:
        return [frame_cache_url(cache_key, path.name) for path in cached]

    script = r"""
import json
import pathlib
import sys

import av
from PIL import Image

video = pathlib.Path(sys.argv[1])
out_dir = pathlib.Path(sys.argv[2])
max_frames = int(sys.argv[3])
max_edge = int(sys.argv[4])

container = av.open(str(video))
stream = next((item for item in container.streams if item.type == "video"), None)
if stream is None:
    raise SystemExit("no video stream")

images = [frame.to_image().convert("RGB") for frame in container.decode(stream)]
if not images:
    raise SystemExit("no frames decoded")

if len(images) <= max_frames:
    indices = list(range(len(images)))
elif max_frames <= 1:
    indices = [0]
else:
    indices = sorted({round(index * (len(images) - 1) / (max_frames - 1)) for index in range(max_frames)})

try:
    resample = Image.Resampling.LANCZOS
except AttributeError:
    resample = Image.LANCZOS

written = []
for output_index, frame_index in enumerate(indices):
    image = images[frame_index]
    image.thumbnail((max_edge, max_edge), resample)
    name = f"frame_{output_index:03d}.jpg"
    image.save(out_dir / name, "JPEG", quality=84, optimize=True)
    written.append(name)

print(json.dumps(written))
"""
    result = subprocess.run(
        [str(python), "-c", script, str(video_path), str(target_dir), str(max_frames), str(VIDEO_FRAME_MAX_EDGE)],
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    try:
        names = json.loads(result.stdout.strip() or "[]")
    except json.JSONDecodeError:
        names = []
    frames = [target_dir / str(name) for name in names if (target_dir / str(name)).exists()]
    if not frames:
        frames = sorted(target_dir.glob("frame_*.jpg"))
    return [frame_cache_url(cache_key, path.name) for path in frames]


def safe_preview_frames(entry: dict[str, Any], comfy_path: Path) -> list[str]:
    try:
        return extract_preview_frames(output_path_for_entry(entry, comfy_path), comfy_path=comfy_path)
    except Exception as exc:  # noqa: BLE001
        print(f"Video preview frames konden niet worden gemaakt: {exc}")
        return []


def extract_entries(history_item: dict[str, Any], media: str) -> list[dict[str, Any]]:
    outputs = history_item.get("outputs", {})
    if not isinstance(outputs, dict):
        return []
    keys = ("videos", "gifs", "files", "images") if media == "video" else ("images",)
    entries: list[dict[str, Any]] = []
    for output in outputs.values():
        if not isinstance(output, dict):
            continue
        for key in keys:
            values = output.get(key)
            if not isinstance(values, list):
                continue
            for entry in values:
                if not isinstance(entry, dict) or not entry.get("filename"):
                    continue
                filename = str(entry["filename"]).lower()
                is_video = filename.endswith((".mp4", ".webm", ".gif", ".mov"))
                if media == "video" and (key != "images" or is_video):
                    entries.append(dict(entry))
                if media == "image" and not is_video:
                    entries.append(dict(entry))
    return entries


def queue_contains_prompt(queue: dict[str, Any], prompt_id: str) -> bool:
    for key in ("queue_running", "queue_pending"):
        entries = queue.get(key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, list) and len(entry) > 1 and entry[1] == prompt_id:
                return True
    return False


def wait_for_history(prompt_id: str, timeout: float = COMFY_IMAGE_TIMEOUT) -> dict[str, Any]:
    deadline = time.time() + timeout
    missing_since: float | None = None
    while time.time() < deadline:
        history = comfy_request(f"/history/{urllib.parse.quote(prompt_id)}", timeout=30)
        item = history.get(prompt_id)
        if isinstance(item, dict):
            return item
        queue = comfy_request("/queue", timeout=10)
        if queue_contains_prompt(queue, prompt_id):
            missing_since = None
        else:
            if missing_since is None:
                missing_since = time.time()
            elif time.time() - missing_since >= COMFY_MISSING_HISTORY_GRACE:
                raise RuntimeError(f"ComfyUI prompt verdween uit de queue zonder history-resultaat: {prompt_id}")
        time.sleep(2)
    try:
        comfy_request("/interrupt", {}, method="POST", timeout=5)
    except Exception:
        pass
    raise TimeoutError(f"ComfyUI job timed out: {prompt_id}")


def queue_comfy_prompt(graph: dict[str, Any], client_id: str) -> str:
    prompt_id = f"dream-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    response = comfy_request(
        "/prompt",
        {"prompt": graph, "prompt_id": prompt_id, "client_id": client_id},
        method="POST",
        timeout=30,
    )
    if response.get("node_errors"):
        raise RuntimeError(json.dumps(response["node_errors"], ensure_ascii=False))
    return prompt_id


def bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def bounded_float(value: object, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def comfy_filename_prefix(folder: str, panel_id: str) -> str:
    suffix = time.strftime("%Y%m%d_%H%M%S")
    safe_panel = re.sub(r"[^a-zA-Z0-9_-]+", "_", panel_id).strip("_") or "panel"
    return f"{folder}/{suffix}_{safe_panel}"


def build_checkpoint_image_prompt(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    prompt = str(data.get("prompt", "")).strip()
    negative = str(data.get("negative_prompt", COMIC_NEGATIVE_PROMPT)).strip()
    ckpt_name = str(data.get("ckpt_name", "")).strip()
    if not ckpt_name:
        raise ValueError("Checkpoint-model ontbreekt.")
    width = bounded_int(data.get("width"), 768, 256, 2048)
    height = bounded_int(data.get("height"), 1088, 256, 2048)
    width -= width % 8
    height -= height % 8
    seed = bounded_int(data.get("seed"), random.randint(0, 2**32 - 1), 0, 2**63 - 1)
    steps = bounded_int(data.get("steps"), 24, 1, 80)
    cfg = bounded_float(data.get("cfg"), 7.0, 0.0, 30.0)
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": ckpt_name},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["1", 1], "text": prompt},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["1", 1], "text": negative},
        },
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
            },
        },
        "6": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
        },
        "7": {
            "class_type": "SaveImage",
            "inputs": {"images": ["6", 0], "filename_prefix": comfy_filename_prefix("comic", str(data.get("panel_id") or "panel"))},
        },
    }


def extract_comfy_error(history_item: dict[str, Any]) -> Any:
    status = history_item.get("status")
    if isinstance(status, dict) and status.get("status_str") == "error":
        return status
    if isinstance(status, dict):
        messages = status.get("messages")
        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, list) and message and message[0] == "execution_error":
                    return message[1] if len(message) > 1 else message
    return None


def select_comic_model(model_id: str, inventory: dict[str, Any]) -> dict[str, Any]:
    choices = local_model_choices(inventory)
    by_id = {str(choice["id"]): choice for choice in choices}
    if model_id == "auto":
        for preferred in ["zimage_turbo"]:
            choice = by_id.get(preferred)
            if choice and choice.get("supported"):
                return choice
        for choice in choices:
            if choice.get("kind") == "checkpoint" and choice.get("supported"):
                return choice
        for preferred in ["wan22_14b_still", "wan21_1_3b_still"]:
            choice = by_id.get(preferred)
            if choice and choice.get("supported"):
                return choice
    choice = by_id.get(model_id)
    if not choice:
        raise RuntimeError(f"Lokaal model '{model_id}' is niet gevonden.")
    if not choice.get("supported"):
        raise RuntimeError(f"Voor '{choice.get('label')}' is nog geen ComfyUI workflow-adapter beschikbaar.")
    return choice


def cast_seed_offset(character_ids: list[str]) -> int:
    # Same visible cast -> same seed offset, so recurring characters render consistently.
    if not character_ids:
        return 0
    key = "|".join(sorted(str(char_id) for char_id in character_ids))
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def render_comic_panel(
    panel: dict[str, Any],
    payload: dict[str, Any],
    model_choice: dict[str, Any],
    comfy_path: Path,
    image: ModuleType | None,
    wan: ModuleType | None,
    seed: int,
) -> dict[str, Any]:
    width = bounded_int(payload.get("width"), 768, 256, 2048)
    height = bounded_int(payload.get("height"), 1088, 256, 2048)
    prompt = str(panel.get("prompt") or "")
    negative = str(panel.get("negativePrompt") or COMIC_NEGATIVE_PROMPT)
    kind = str(model_choice.get("kind") or "")
    model_id = str(model_choice.get("id") or "auto")
    cast_ids = [str(char_id) for char_id in (panel.get("characterIds") or [])]
    panel_seed = (seed + cast_seed_offset(cast_ids)) % (2**63 - 1)

    if model_id == "zimage_turbo":
        if image is None:
            raise RuntimeError("image_prompt_page.py is niet gevonden in de ComfyUI-map.")
        graph = image.build_image_prompt(
            {
                "prompt": prompt,
                "negative_prompt": negative,
                "width": width,
                "height": height,
                "steps": bounded_int(payload.get("steps"), 8, 1, 40),
                "cfg": bounded_float(payload.get("cfg"), 1.0, 0.0, 20.0),
                "batch_size": 1,
                "seed": panel_seed,
            }
        )
        prompt_id = queue_comfy_prompt(graph, "dreamweaver-comic-zimage")
        history = wait_for_history(prompt_id, timeout=COMFY_IMAGE_TIMEOUT)
        error = image.extract_error(history)
        if error:
            raise RuntimeError(json.dumps(error, ensure_ascii=False))
        entries = extract_entries(history, "image")
        if not entries:
            raise RuntimeError(f"ComfyUI gaf geen image-output terug voor {panel['id']}.")
        return {"imageUrl": entry_url(entries[0]), "promptId": prompt_id, "mediaType": "image"}

    if kind == "checkpoint":
        ckpt_name = str(model_choice.get("id", "")).removeprefix("checkpoint:")
        graph = build_checkpoint_image_prompt(
            {
                "ckpt_name": ckpt_name,
                "prompt": prompt,
                "negative_prompt": negative,
                "width": width,
                "height": height,
                "steps": bounded_int(payload.get("steps"), 24, 1, 80),
                "cfg": bounded_float(payload.get("cfg"), 7.0, 0.0, 30.0),
                "seed": panel_seed,
                "panel_id": panel["id"],
            }
        )
        prompt_id = queue_comfy_prompt(graph, "dreamweaver-comic-checkpoint")
        history = wait_for_history(prompt_id, timeout=COMFY_IMAGE_TIMEOUT)
        error = extract_comfy_error(history)
        if error:
            raise RuntimeError(json.dumps(error, ensure_ascii=False))
        entries = extract_entries(history, "image")
        if not entries:
            raise RuntimeError(f"ComfyUI gaf geen image-output terug voor {panel['id']}.")
        return {"imageUrl": entry_url(entries[0]), "promptId": prompt_id, "mediaType": "image"}

    if model_id in {"wan22_14b_still", "wan21_1_3b_still"}:
        if wan is None:
            raise RuntimeError("wan_prompt_page.py is niet gevonden in de ComfyUI-map.")
        wan_model = "wan22_14b" if model_id == "wan22_14b_still" else "wan21_1_3b"
        graph = wan.build_wan_prompt(
            {
                "model": wan_model,
                "prompt": prompt,
                "negative_prompt": wan.DEFAULT_NEGATIVE_PROMPT,
                "seconds": 1,
                "fps": 4,
                "width": min(width, 768),
                "height": min(height, 768),
                "steps": bounded_int(payload.get("steps"), 4 if wan_model == "wan22_14b" else 8, 1, 40),
                "cfg": bounded_float(payload.get("cfg"), 1.0 if wan_model == "wan22_14b" else 5.0, 0.0, 20.0),
                "seed": panel_seed,
            }
        )
        prompt_id = queue_comfy_prompt(graph, "dreamweaver-comic-wan")
        history = wait_for_history(prompt_id, timeout=COMFY_VIDEO_TIMEOUT)
        error = wan.extract_error(history)
        if error:
            raise RuntimeError(json.dumps(error, ensure_ascii=False))
        entries = extract_entries(history, "video")
        if not entries:
            raise RuntimeError(f"ComfyUI gaf geen Wan-video-output terug voor {panel['id']}.")
        frames = safe_preview_frames(entries[0], comfy_path)
        if not frames:
            raise RuntimeError("Wan-output is gemaakt, maar er kon geen previewframe voor het panel worden uitgelezen.")
        return {"imageUrl": frames[0], "videoUrl": entry_url(entries[0]), "promptId": prompt_id, "mediaType": "video_still"}

    raise RuntimeError(f"Model '{model_choice.get('label')}' kan nog geen strippanel renderen.")


def run_comic_job(job_id: str, payload: dict[str, Any]) -> None:
    comfy_path = Path(payload.get("comfyPath") or DEFAULT_COMFY_PATH)
    wan, image = helpers(comfy_path)
    try:
        story = str(payload.get("story") or "")
        style = str(payload.get("style") or "realistic anime").strip() or "realistic anime"
        planner_id = str(payload.get("cloudModel") or "local_rules")
        render_mode = str(payload.get("renderMode") or "render")
        story_brief = payload.get("storyBrief") if isinstance(payload.get("storyBrief"), dict) else {}
        story_answers = payload.get("storyAnswers") if isinstance(payload.get("storyAnswers"), dict) else {}
        job_update(job_id, status="analyzing")
        comic = build_comic_plan(story, style, planner_id, job_id, story_brief, story_answers)
        inventory = scan_models(comfy_path)
        seed = bounded_int(payload.get("seed"), random.randint(1, 2**32 - 1), 1, 2**63 - 1)
        model_choice = select_comic_model(str(payload.get("localModel") or "auto"), inventory)
        render_config = {
            "comfyPath": str(comfy_path),
            "width": payload.get("width"),
            "height": payload.get("height"),
            "steps": payload.get("steps"),
            "cfg": payload.get("cfg"),
        }
        job_update(
            job_id,
            status="planned",
            comic=comic,
            inventory=inventory,
            model=model_choice,
            seed=seed,
            renderConfig=render_config,
            totalPanels=comic["panelCount"],
            renderedPanels=0,
        )

        if render_mode == "plan":
            job_update(job_id, done=True, status="success", resultType="comic_plan", comic=comic)
            return

        comfy_request("/system_stats", timeout=3)
        for index, panel in enumerate(comic["panels"], start=1):
            current = job_get(job_id) or {}
            if current.get("cancelRequested"):
                panel["status"] = "cancelled"
                job_update(job_id, done=True, status="cancelled", comic=comic, renderedPanels=index - 1)
                return
            panel["status"] = "rendering"
            job_update(
                job_id,
                status="rendering_comic_panel",
                comic=comic,
                currentPanel=index,
                renderedPanels=index - 1,
            )
            result = render_comic_panel(panel, payload, model_choice, comfy_path, image, wan, seed)
            panel.update(result)
            panel["status"] = "success"
            job_update(
                job_id,
                status="rendering_comic_panel",
                comic=comic,
                currentPanel=index,
                renderedPanels=index,
            )

        job_update(
            job_id,
            done=True,
            status="success",
            resultType="comic",
            comic=comic,
            renderedPanels=comic["panelCount"],
        )
    except Exception as exc:  # noqa: BLE001
        job_update(job_id, done=True, status="error", error=str(exc))


def find_job_panel(job: dict[str, Any], panel_id: str) -> dict[str, Any] | None:
    comic = job.get("comic") or {}
    for panel in comic.get("panels") or []:
        if str(panel.get("id")) == panel_id:
            return panel
    return None


def update_comic_panel(job_id: str, panel_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    job = job_get(job_id)
    if not job:
        raise ValueError("Job niet gevonden.")
    panel = find_job_panel(job, panel_id)
    if panel is None:
        raise ValueError(f"Panel {panel_id} niet gevonden.")
    if "prompt" in fields:
        panel["prompt"] = trim_text(str(fields.get("prompt") or ""), 2000)
    if "negativePrompt" in fields:
        panel["negativePrompt"] = trim_text(str(fields.get("negativePrompt") or ""), 2000)
    if "caption" in fields:
        panel["caption"] = trim_text(str(fields.get("caption") or ""), 400)
    panel["edited"] = True
    job_update(job_id, comic=job.get("comic"))
    return panel


def regenerate_panel_job(job_id: str, panel_id: str, base_seed: int | None = None) -> None:
    job = job_get(job_id)
    if not job:
        return
    panel = find_job_panel(job, panel_id)
    if panel is None:
        job_update(job_id, panelError=f"Panel {panel_id} niet gevonden.", panelBusy="")
        return
    model_choice = job.get("model") or {}
    if not model_choice:
        panel["status"] = "error"
        job_update(job_id, comic=job.get("comic"), panelBusy="", panelError="Geen rendermodel bekend voor deze job.")
        return
    render_config = job.get("renderConfig") or {}
    comfy_path = Path(render_config.get("comfyPath") or DEFAULT_COMFY_PATH)
    wan, image = helpers(comfy_path)
    seed = base_seed if base_seed is not None else random.randint(1, 2**32 - 1)
    prev_status = job.get("status") or "success"
    try:
        comfy_request("/system_stats", timeout=3)
        panel["status"] = "rendering"
        job_update(job_id, comic=job.get("comic"), panelBusy=panel_id, panelError="")
        result = render_comic_panel(panel, render_config, model_choice, comfy_path, image, wan, seed)
        panel.update(result)
        panel["status"] = "success"
        panel["seedBase"] = seed
        job_update(job_id, comic=job.get("comic"), panelBusy="", status=prev_status)
    except Exception as exc:  # noqa: BLE001
        panel["status"] = "error"
        job_update(job_id, comic=job.get("comic"), panelBusy="", panelError=str(exc), status=prev_status)


def find_job_character(job: dict[str, Any], character_id: str) -> dict[str, Any] | None:
    comic = job.get("comic") or {}
    for character in comic.get("characters") or []:
        if str(character.get("id")) == character_id:
            return character
    return None


def generate_character_reference_job(job_id: str, character_id: str, base_seed: int | None = None) -> None:
    job = job_get(job_id)
    if not job:
        return
    character = find_job_character(job, character_id)
    if character is None:
        job_update(job_id, characterBusy="", characterError=f"Personage {character_id} niet gevonden.")
        return
    model_choice = job.get("model") or {}
    if not model_choice:
        character["referenceStatus"] = "error"
        job_update(job_id, comic=job.get("comic"), characterBusy="", characterError="Geen rendermodel bekend voor deze job.")
        return
    render_config = job.get("renderConfig") or {}
    comfy_path = Path(render_config.get("comfyPath") or DEFAULT_COMFY_PATH)
    wan, image = helpers(comfy_path)
    style = str((job.get("comic") or {}).get("style") or "realistic anime")
    seed = base_seed if base_seed is not None else random.randint(1, 2**32 - 1)
    ref_panel = {
        "id": f"ref_{character_id}",
        "panelNumber": 0,
        "characterIds": [character_id],
        "prompt": build_character_reference_prompt(character, style),
        "negativePrompt": build_character_reference_negative_prompt(),
    }
    try:
        comfy_request("/system_stats", timeout=3)
        character["referenceStatus"] = "rendering"
        job_update(job_id, comic=job.get("comic"), characterBusy=character_id, characterError="")
        result = render_comic_panel(ref_panel, render_config, model_choice, comfy_path, image, wan, seed)
        character["referenceImageUrl"] = result.get("imageUrl")
        character["referencePrompt"] = ref_panel["prompt"]
        character["referenceSeed"] = seed
        character["referenceStatus"] = "ready"
        job_update(job_id, comic=job.get("comic"), characterBusy="")
    except Exception as exc:  # noqa: BLE001
        character["referenceStatus"] = "error"
        job_update(job_id, comic=job.get("comic"), characterBusy="", characterError=str(exc))


def run_dream_job(job_id: str, payload: dict[str, Any]) -> None:
    comfy_path = Path(payload.get("comfyPath") or DEFAULT_COMFY_PATH)
    wan, image = helpers(comfy_path)
    try:
        desire = str(payload.get("desire", "")).strip()
        if not re.search(r"[\wÀ-ÿ]", desire):
            raise ValueError("Typ eerst iets dat verbeeld mag worden.")
        comfy_request("/system_stats", timeout=3)
        transformed = transform_desire(desire)
        inventory = scan_models(comfy_path)
        mode = str(payload.get("mode") or "video")
        width = int(payload.get("width") or 512)
        height = int(payload.get("height") or 512)
        seed = int(payload.get("seed") or 0)
        if seed <= 0:
            seed = random.randint(1, 2**32 - 1)

        job_update(job_id, status="prepared", transformed=transformed, inventory=inventory, seed=seed)

        if mode == "video":
            if wan is None:
                raise RuntimeError("wan_prompt_page.py is niet gevonden in de ComfyUI-map.")
            model = select_video_model(str(payload.get("model") or "auto"), inventory)
            if model is None:
                mode = "images"
            else:
                job_update(job_id, status="queued_video", model=model)
                graph = wan.build_wan_prompt(
                    {
                        "model": model,
                        "prompt": transformed["videoPrompt"],
                        "negative_prompt": wan.DEFAULT_NEGATIVE_PROMPT,
                        "seconds": float(payload.get("seconds") or 6),
                        "fps": float(payload.get("fps") or (16 if model == "wan22_14b" else 8)),
                        "width": width,
                        "height": height,
                        "steps": int(payload.get("steps") or (4 if model == "wan22_14b" else 8)),
                        "cfg": float(payload.get("cfg") or (1.0 if model == "wan22_14b" else 5.0)),
                        "seed": seed,
                    }
                )
                prompt_id = queue_comfy_prompt(graph, "dreamweaver-video")
                job_update(job_id, status="rendering_video", prompt_id=prompt_id)
                history = wait_for_history(prompt_id, timeout=COMFY_VIDEO_TIMEOUT)
                error = wan.extract_error(history)
                if error:
                    raise RuntimeError(json.dumps(error, ensure_ascii=False))
                entries = extract_entries(history, "video")
                if not entries:
                    raise RuntimeError("ComfyUI gaf geen video-output terug.")
                job_update(
                    job_id,
                    done=True,
                    status="success",
                    resultType="video",
                    mediaUrls=[entry_url(entries[0])],
                    frameUrls=safe_preview_frames(entries[0], comfy_path),
                    prompt_id=prompt_id,
                )
                return

        if image is None:
            raise RuntimeError("image_prompt_page.py is niet gevonden in de ComfyUI-map.")
        if not inventory["zimage"]:
            raise RuntimeError("Z-Image Turbo modelset is niet compleet in ComfyUI/models.")
        urls: list[str] = []
        prompt_ids: list[str] = []
        for index, prompt in enumerate(transformed["imagePrompts"], start=1):
            job_update(job_id, status=f"rendering_image_{index}", currentImage=index, imageUrls=urls)
            graph = image.build_image_prompt(
                {
                    "prompt": prompt,
                    "negative_prompt": "",
                    "width": width,
                    "height": height,
                    "steps": int(payload.get("imageSteps") or 8),
                    "cfg": float(payload.get("imageCfg") or 1.0),
                    "batch_size": 1,
                    "seed": seed + index,
                }
            )
            prompt_id = queue_comfy_prompt(graph, "dreamweaver-image")
            prompt_ids.append(prompt_id)
            history = wait_for_history(prompt_id, timeout=COMFY_IMAGE_TIMEOUT)
            error = image.extract_error(history)
            if error:
                raise RuntimeError(json.dumps(error, ensure_ascii=False))
            entries = extract_entries(history, "image")
            if not entries:
                raise RuntimeError(f"ComfyUI gaf geen image-output terug voor beeld {index}.")
            urls.append(entry_url(entries[0]))

        job_update(
            job_id,
            done=True,
            status="success",
            resultType="images",
            mediaUrls=urls,
            prompt_ids=prompt_ids,
        )
    except Exception as exc:  # noqa: BLE001
        job_update(job_id, done=True, status="error", error=str(exc))


def start_comfy(comfy_path: Path) -> dict[str, Any]:
    global COMFY_PROCESS
    try:
        comfy_request("/system_stats", timeout=2)
        return {"started": False, "running": True, "message": "ComfyUI draait al."}
    except Exception:
        pass

    python = comfy_path / ".venv" / "bin" / "python"
    if not python.exists():
        python = Path("python3")
    host, port = parse_comfy_url()
    command = [str(python), "main.py", "--listen", host, "--port", str(port)]
    log_path = Path(os.environ.get("DREAMWEAVER_COMFY_LOG", "/tmp/dreamweaver-comfy-comfy.log"))
    log = log_path.open("a", encoding="utf-8")
    COMFY_PROCESS = subprocess.Popen(
        command,
        cwd=comfy_path,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return {"started": True, "running": False, "pid": COMFY_PROCESS.pid, "log": str(log_path)}


class Handler(BaseHTTPRequestHandler):
    server_version = "DreamweaverComfy/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8") or "{}")

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/api/status":
                self.handle_status()
            elif parsed.path == "/api/models":
                inventory = scan_models(DEFAULT_COMFY_PATH)
                self.send_json(
                    {
                        "localModels": local_model_choices(inventory),
                        "localPlannerModels": local_planner_choices(),
                        "apiPlannerModels": api_planner_choices(),
                        "cloudModels": cloud_model_choices(),
                        "inventory": inventory,
                    }
                )
            elif parsed.path.startswith("/api/jobs/"):
                job_id = parsed.path.rsplit("/", 1)[-1]
                job = job_get(job_id)
                if not job:
                    self.send_json({"error": "Job niet gevonden."}, 404)
                    return
                self.send_json(job)
            elif parsed.path == "/api/secrets":
                self.send_json({"providers": provider_status()})
            elif parsed.path == "/api/comfy-view":
                self.proxy_comfy_view(parsed.query)
            elif parsed.path == "/api/video-frames":
                self.handle_video_frames(parsed.query)
            elif parsed.path == "/api/frame":
                self.serve_cached_frame(parsed.query)
            else:
                self.serve_static(parsed.path)
        except Exception as exc:  # noqa: BLE001
            self.send_json({"error": str(exc)}, 500)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/api/transform":
                data = self.read_json()
                self.send_json(transform_desire(str(data.get("desire", ""))))
            elif parsed.path == "/api/extract-text":
                data = self.read_json()
                encoded = str(data.get("dataBase64") or "")
                try:
                    raw = base64.b64decode(encoded, validate=True)
                except Exception as exc:  # noqa: BLE001
                    raise ValueError("Het bestand kon niet worden gedecodeerd.") from exc
                self.send_json(
                    extract_document_text(
                        str(data.get("filename") or ""),
                        str(data.get("mimeType") or ""),
                        raw,
                    )
                )
            elif parsed.path == "/api/dream":
                data = self.read_json()
                job_id = f"job-{int(time.time())}-{uuid.uuid4().hex[:8]}"
                with JOBS_LOCK:
                    JOBS[job_id] = {
                        "jobId": job_id,
                        "done": False,
                        "status": "queued",
                        "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "mediaUrls": [],
                    }
                thread = threading.Thread(target=run_dream_job, args=(job_id, data), daemon=True)
                thread.start()
                self.send_json({"jobId": job_id})
            elif parsed.path == "/api/comic/brief":
                data = self.read_json()
                story = str(data.get("story") or "")
                style = str(data.get("style") or "realistic anime").strip() or "realistic anime"
                planner_id = str(data.get("cloudModel") or "local_rules")
                self.send_json({"brief": build_story_brief(story, style, planner_id)})
            elif parsed.path == "/api/comic":
                data = self.read_json()
                job_id = f"comic-{int(time.time())}-{uuid.uuid4().hex[:8]}"
                with JOBS_LOCK:
                    JOBS[job_id] = {
                        "jobId": job_id,
                        "done": False,
                        "status": "queued",
                        "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "resultType": "comic",
                    }
                thread = threading.Thread(target=run_comic_job, args=(job_id, data), daemon=True)
                thread.start()
                self.send_json({"jobId": job_id})
            elif parsed.path == "/api/comic/update-panel":
                data = self.read_json()
                job_id = str(data.get("jobId") or "")
                panel_id = str(data.get("panelId") or "")
                fields = {key: data[key] for key in ("prompt", "negativePrompt", "caption") if key in data}
                panel = update_comic_panel(job_id, panel_id, fields)
                self.send_json({"panel": panel})
            elif parsed.path == "/api/comic/regenerate-panel":
                data = self.read_json()
                job_id = str(data.get("jobId") or "")
                panel_id = str(data.get("panelId") or "")
                if not job_get(job_id):
                    self.send_json({"error": "Job niet gevonden."}, 404)
                    return
                raw_seed = data.get("seed")
                base_seed: int | None = None
                if raw_seed not in (None, ""):
                    try:
                        base_seed = max(1, min(2**63 - 1, int(raw_seed)))
                    except (TypeError, ValueError):
                        base_seed = None
                thread = threading.Thread(target=regenerate_panel_job, args=(job_id, panel_id, base_seed), daemon=True)
                thread.start()
                self.send_json({"started": True, "jobId": job_id, "panelId": panel_id})
            elif parsed.path == "/api/comic/character-reference":
                data = self.read_json()
                job_id = str(data.get("jobId") or "")
                character_id = str(data.get("characterId") or "")
                if not job_get(job_id):
                    self.send_json({"error": "Job niet gevonden."}, 404)
                    return
                raw_seed = data.get("seed")
                base_seed = None
                if raw_seed not in (None, ""):
                    try:
                        base_seed = max(1, min(2**63 - 1, int(raw_seed)))
                    except (TypeError, ValueError):
                        base_seed = None
                thread = threading.Thread(target=generate_character_reference_job, args=(job_id, character_id, base_seed), daemon=True)
                thread.start()
                self.send_json({"started": True, "jobId": job_id, "characterId": character_id})
            elif parsed.path == "/api/secrets":
                data = self.read_json()
                provider_id = str(data.get("provider") or "")
                if provider_id not in PROVIDER_BY_ID:
                    self.send_json({"error": "Onbekende provider."}, 400)
                    return
                save_secret(provider_id, str(data.get("key") or ""))
                self.send_json({"saved": True, "providers": provider_status()})
            elif parsed.path == "/api/cancel-job":
                data = self.read_json()
                job_id = str(data.get("jobId") or "")
                if not job_get(job_id):
                    self.send_json({"error": "Job niet gevonden."}, 404)
                    return
                job_update(job_id, cancelRequested=True)
                self.send_json({"cancelRequested": True, "jobId": job_id})
            elif parsed.path == "/api/reset-comfy":
                data = self.read_json()
                clear_history = bool(data.get("clearHistory", True))
                self.send_json(reset_comfy_runtime(clear_history=clear_history))
            elif parsed.path == "/api/start-comfy":
                data = self.read_json()
                comfy_path = Path(data.get("comfyPath") or DEFAULT_COMFY_PATH)
                self.send_json(start_comfy(comfy_path))
            else:
                self.send_error(404)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            self.send_json({"error": raw}, exc.code)
        except Exception as exc:  # noqa: BLE001
            self.send_json({"error": str(exc)}, 500)

    def handle_status(self) -> None:
        comfy_path = DEFAULT_COMFY_PATH
        wan, image = helpers(comfy_path)
        inventory = scan_models(comfy_path)
        comfy_running = False
        system_stats: dict[str, Any] | None = None
        try:
            system_stats = comfy_request("/system_stats", timeout=2)
            comfy_running = True
        except Exception:
            pass
        self.send_json(
            {
                "app": "Dreamweaver Comfy",
                "version": APP_VERSION,
                "comfyUrl": DEFAULT_COMFY_URL,
                "comfyPath": str(comfy_path),
                "comfyRunning": comfy_running,
                "helpers": {"wan": wan is not None, "zimage": image is not None},
                "inventory": inventory,
                "localModels": local_model_choices(inventory),
                "localPlannerModels": local_planner_choices(),
                "apiPlannerModels": api_planner_choices(),
                "cloudModels": cloud_model_choices(),
                "systemStats": system_stats,
            }
        )

    def proxy_comfy_view(self, query: str) -> None:
        with urllib.request.urlopen(f"{DEFAULT_COMFY_URL}/view?{query}", timeout=60) as response:
            body = response.read()
            content_type = response.headers.get("Content-Type")
        if not content_type:
            filename = urllib.parse.parse_qs(query).get("filename", [""])[0]
            content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    def handle_video_frames(self, query: str) -> None:
        params = urllib.parse.parse_qs(query)
        limit = int(params.get("max_frames", [str(VIDEO_FRAME_LIMIT)])[0] or VIDEO_FRAME_LIMIT)
        limit = max(1, min(64, limit))
        entry = entry_from_query(query)
        video_path = output_path_for_entry(entry)
        self.send_json({"frameUrls": extract_preview_frames(video_path, limit)})

    def serve_cached_frame(self, query: str) -> None:
        params = urllib.parse.parse_qs(query)
        cache_key = params.get("cache", [""])[0]
        name = params.get("name", [""])[0]
        if not re.fullmatch(r"[a-f0-9]{28}", cache_key) or not re.fullmatch(r"frame_\d{3}\.jpg", name):
            self.send_error(404)
            return
        root = FRAME_CACHE_DIR.resolve()
        target = (root / cache_key / name).resolve()
        if not target.is_relative_to(root) or not target.exists() or target.is_dir():
            self.send_error(404)
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=604800, immutable")
        self.end_headers()
        self.wfile.write(body)

    def serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        target = (APP_DIR / relative).resolve()
        if not target.is_relative_to(APP_DIR.resolve()) or not target.exists() or target.is_dir():
            self.send_error(404)
            return
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".js":
            content_type = "text/javascript; charset=utf-8"
        elif target.suffix in {".html", ".css", ".svg"}:
            content_type = f"{content_type}; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("DREAMWEAVER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DREAMWEAVER_PORT", "8788")))
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"Dreamweaver Comfy: {url}")
    if args.open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
