from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SUPPORTED_EMOTIONS = ("happy", "sad", "angry", "neutral")

DEFAULT_EMOTION_CONTEXTS: dict[str, str] = {
    "happy": "用更明亮、轻快、带笑意的语气朗读。",
    "sad": "用更低沉、缓慢、克制的语气朗读。",
    "angry": "用更有力度、紧张、带压迫感的语气朗读，但不要破音。",
    "neutral": "用自然、清晰、稳定的语气朗读。",
}

DEFAULT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "happy": (
        "开心",
        "高兴",
        "快乐",
        "哈哈",
        "笑死",
        "太棒",
        "成功",
        "喜欢",
        "惊喜",
        "happy",
        "great",
        "awesome",
    ),
    "sad": (
        "难过",
        "伤心",
        "想哭",
        "哭了",
        "遗憾",
        "失落",
        "委屈",
        "悲伤",
        "sad",
        "cry",
        "sorry",
    ),
    "angry": (
        "生气",
        "愤怒",
        "离谱",
        "烦死",
        "气死",
        "讨厌",
        "别吵",
        "闭嘴",
        "angry",
        "mad",
        "annoying",
    ),
}


@dataclass(slots=True)
class EmotionRouter:
    emotion_contexts: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_EMOTION_CONTEXTS))
    keywords: dict[str, tuple[str, ...]] = field(default_factory=lambda: dict(DEFAULT_KEYWORDS))

    def __init__(
        self,
        *,
        emotion_contexts: dict[str, str] | None = None,
        keywords: dict[str, Any] | None = None,
    ):
        contexts = dict(DEFAULT_EMOTION_CONTEXTS)
        if isinstance(emotion_contexts, dict):
            for emotion, context in emotion_contexts.items():
                key = normalize_emotion(emotion)
                if key:
                    contexts[key] = str(context or "").strip()
        self.emotion_contexts = contexts

        merged_keywords: dict[str, tuple[str, ...]] = dict(DEFAULT_KEYWORDS)
        if isinstance(keywords, dict):
            for emotion, values in keywords.items():
                key = normalize_emotion(emotion)
                if not key or key == "neutral":
                    continue
                if isinstance(values, str):
                    values = [values]
                if values:
                    merged_keywords[key] = tuple(str(item) for item in values if str(item).strip())
        self.keywords = merged_keywords

    def classify(self, text: str) -> str:
        content = str(text or "").lower()
        if not content.strip():
            return "neutral"

        scores = {emotion: 0 for emotion in SUPPORTED_EMOTIONS}
        for emotion, words in self.keywords.items():
            scores[emotion] = sum(1 for word in words if str(word).lower() in content)

        best = max(("happy", "sad", "angry"), key=lambda item: scores[item])
        return best if scores[best] > 0 else "neutral"

    def resolve(self, text: str, *, requested: str | None = None) -> str:
        manual = normalize_emotion(requested)
        if manual:
            return manual
        return self.classify(text)

    def build_context(
        self,
        *,
        base_context: str = "",
        emotion: str = "neutral",
        voice_context: str = "",
        command_context: str = "",
    ) -> str:
        parts = [
            str(base_context or "").strip(),
            self.emotion_contexts.get(normalize_emotion(emotion) or "neutral", "").strip(),
            str(voice_context or "").strip(),
            str(command_context or "").strip(),
        ]
        return "\n".join(part for part in parts if part)


def normalize_emotion(value: str | None) -> str | None:
    key = str(value or "").strip().lower()
    aliases = {
        "joy": "happy",
        "哈": "happy",
        "开心": "happy",
        "高兴": "happy",
        "悲伤": "sad",
        "难过": "sad",
        "伤心": "sad",
        "怒": "angry",
        "生气": "angry",
        "愤怒": "angry",
        "普通": "neutral",
        "自然": "neutral",
        "中性": "neutral",
    }
    key = aliases.get(key, key)
    return key if key in SUPPORTED_EMOTIONS else None
