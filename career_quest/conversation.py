"""Local conversational turns and conservative language selection, without API calls."""

import re
from typing import Literal

from career_quest.models import Language

SocialIntent = Literal["greeting", "thanks", "language", "help"]
PHRASES: dict[SocialIntent, set[str]] = {
    "greeting": {
        "hi",
        "hello",
        "hey",
        "good morning",
        "привет",
        "здравствуйте",
        "добрый день",
        "сәлем",
        "салем",
        "сәлеметсіз бе",
    },
    "thanks": {"thanks", "thank you", "спасибо", "благодарю", "рақмет", "рахмет"},
    "help": {"help", "what can you do", "кто ты", "что ты умеешь", "помоги", "сен не істей аласың"},
    "language": {
        "in english",
        "english please",
        "answer in english",
        "на русском",
        "по русски",
        "ответь по русски",
        "қазақша",
        "қазақша жауап бер",
        "по казахски",
        "ответь по казахски",
        "на казахском",
    },
}
TEXTS: dict[Language, dict[SocialIntent, str]] = {
    "en": {
        "greeting": (
            "Hi! I can help you understand this activity. Would you like to know why it suits you, "
            "what you can learn, or which alternatives are available?"
        ),
        "thanks": "You're welcome! Is there anything else you'd like to know about this activity?",
        "language": "Sure, I'll reply in English. What would you like to know about this activity?",
        "help": (
            "I can explain why this activity was recommended, what skills it develops, and which "
            "alternatives fit your goal. What would you like to explore?"
        ),
    },
    "ru": {
        "greeting": (
            "Здравствуйте! Помогу разобраться с этим занятием. Хотите узнать, почему оно вам подходит, "
            "чему поможет научиться или какие есть альтернативы?"
        ),
        "thanks": "Пожалуйста! Что ещё хотите узнать об этом занятии?",
        "language": "Хорошо, отвечу по-русски. Что хотите узнать об этом занятии?",
        "help": (
            "Я могу объяснить, зачем рекомендовано это занятие, какие навыки оно развивает и какие "
            "есть альтернативы для вашей цели. Что разберём?"
        ),
    },
    "kk": {
        "greeting": (
            "Сәлеметсіз бе! Осы сабақ туралы сұрақтарыңызға көмектесемін. Неліктен ұсынылғанын, қандай "
            "дағдыларды дамытатынын немесе басқа нұсқаларды білгіңіз келе ме?"
        ),
        "thanks": "Оқасы жоқ! Осы сабақ туралы тағы не білгіңіз келеді?",
        "language": "Жақсы, қазақша жауап беремін. Осы сабақ туралы не білгіңіз келеді?",
        "help": (
            "Сабақтың неліктен ұсынылғанын, қандай дағдыларды дамытатынын және мақсатыңызға сәйкес "
            "басқа нұсқаларды түсіндіре аламын. Неден бастаймыз?"
        ),
    },
}


def normalize(text: str) -> str:
    """Normalize short social messages without matching a prefix of a real question."""
    return " ".join(re.findall(r"\w+", text.casefold()))


def social_intent(text: str) -> SocialIntent | None:
    """Recognize complete social turns; greetings plus questions go to the model."""
    value = normalize(text)
    return next((intent for intent, phrases in PHRASES.items() if value in phrases), None)


def detect_language(text: str, fallback: Language = "ru") -> Language:
    """Prefer explicit requests, then script cues, preserving the last language for ambiguous text."""
    value = normalize(text)
    if any(term in value for term in ("in english", "english please")):
        return "en"
    if any(term in value for term in ("қазақша", "на казахском", "по казахски")):
        return "kk"
    if any(term in value for term in ("по русски", "на русском")):
        return "ru"
    if re.search(r"[әғқңөұүһі]", value) or value in {"салем", "рахмет"}:
        return "kk"
    if re.search(r"[а-яё]", value):
        return "ru"
    if re.search(r"[a-z]", value):
        return "en"
    return fallback
