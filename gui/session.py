"""Conversation history and QSettings-backed persistence for the GUI."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4

from PySide6.QtCore import QSettings


@dataclass(slots=True)
class Conversation:
    """One independently switchable GUI conversation."""

    id: str
    title: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    logs: list[dict[str, Any]] = field(default_factory=list)
    process: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""

        return asdict(self)

    @classmethod
    def from_dict(cls, value: object) -> Conversation | None:
        """Validate and load one persisted conversation."""

        if not isinstance(value, dict):
            return None
        identifier = value.get("id")
        title = value.get("title")
        messages = value.get("messages", [])
        logs = value.get("logs", [])
        process = value.get("process", [])
        if not isinstance(identifier, str) or not identifier:
            return None
        if not isinstance(title, str) or not title:
            return None
        if (
            not isinstance(messages, list)
            or not isinstance(logs, list)
            or not isinstance(process, list)
        ):
            return None
        clean_messages = [dict(item) for item in messages if isinstance(item, dict)]
        clean_logs = [dict(item) for item in logs if isinstance(item, dict)]
        clean_process: list[dict[str, Any]] = []
        for item in process:
            if isinstance(item, str):
                clean_process.append({"level": 0, "text": item})
            elif isinstance(item, dict):
                text = item.get("text")
                level = item.get("level", 0)
                if isinstance(text, str) and isinstance(level, int):
                    clean_process.append(
                        {"level": max(0, min(level, 3)), "text": text}
                    )
        return cls(identifier, title, clean_messages, clean_logs, clean_process)


class ConversationStore:
    """Own conversations and persist them as JSON through QSettings."""

    SETTINGS_KEY = "conversations/data"
    ACTIVE_KEY = "conversations/active_id"

    def __init__(self, settings: QSettings) -> None:
        """Load persisted data or create one empty conversation."""

        self.settings = settings
        self.conversations: list[Conversation] = self._load()
        self.active_id = str(settings.value(self.ACTIVE_KEY, ""))
        if not self.conversations:
            conversation = self._make_conversation()
            self.conversations.append(conversation)
        if self.get(self.active_id) is None:
            self.active_id = self.conversations[0].id
        self.save()

    @staticmethod
    def _make_conversation(title: str = "新会话") -> Conversation:
        """Create one conversation with a collision-resistant identifier."""

        return Conversation(id=uuid4().hex, title=title)

    def _load(self) -> list[Conversation]:
        """Decode the persisted conversation list, ignoring corrupt entries."""

        raw = self.settings.value(self.SETTINGS_KEY, "[]")
        if not isinstance(raw, str):
            return []
        try:
            values = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(values, list):
            return []
        loaded: list[Conversation] = []
        for value in values:
            conversation = Conversation.from_dict(value)
            if conversation is not None:
                loaded.append(conversation)
        return loaded

    def save(self) -> None:
        """Persist all conversations and the active identifier."""

        encoded = json.dumps(
            [conversation.to_dict() for conversation in self.conversations],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self.settings.setValue(self.SETTINGS_KEY, encoded)
        self.settings.setValue(self.ACTIVE_KEY, self.active_id)
        self.settings.sync()

    def get(self, conversation_id: str) -> Conversation | None:
        """Return one conversation by identifier."""

        return next(
            (
                conversation
                for conversation in self.conversations
                if conversation.id == conversation_id
            ),
            None,
        )

    @property
    def active(self) -> Conversation:
        """Return the active conversation, repairing stale state if needed."""

        conversation = self.get(self.active_id)
        if conversation is not None:
            return conversation
        conversation = self._make_conversation()
        self.conversations.append(conversation)
        self.active_id = conversation.id
        self.save()
        return conversation

    def create(self) -> Conversation:
        """Create and activate a new empty conversation."""

        conversation = self._make_conversation()
        self.conversations.append(conversation)
        self.active_id = conversation.id
        self.save()
        return conversation

    def reset(self) -> Conversation:
        """Discard persisted conversations and start one empty session."""

        conversation = self._make_conversation()
        self.conversations = [conversation]
        self.active_id = conversation.id
        self.save()
        return conversation

    def activate(self, conversation_id: str) -> bool:
        """Activate an existing conversation."""

        if self.get(conversation_id) is None:
            return False
        self.active_id = conversation_id
        self.save()
        return True

    def delete_conversation(self, conversation_id: str) -> bool:
        """Delete an entire historical conversation and select a safe neighbor."""

        index = next(
            (
                item_index
                for item_index, conversation in enumerate(self.conversations)
                if conversation.id == conversation_id
            ),
            -1,
        )
        if index < 0:
            return False
        del self.conversations[index]
        if not self.conversations:
            self.conversations.append(self._make_conversation())
        next_index = min(index, len(self.conversations) - 1)
        self.active_id = self.conversations[next_index].id
        self.save()
        return True

    def add_message(
        self,
        role: str,
        content: str,
        *,
        conversation_id: str | None = None,
        waiting: bool = False,
    ) -> None:
        """Append one message and derive a title from the first user prompt."""

        conversation = self.get(conversation_id or self.active_id)
        if conversation is None:
            return
        conversation.messages.append(
            {"role": role, "content": content, "waiting": waiting}
        )
        if role == "user" and not any(
            message.get("role") == "user"
            for message in conversation.messages[:-1]
        ):
            conversation.title = content.strip()[:20] or "新会话"
        self.save()

    def update_waiting_message(
        self,
        content: str,
        *,
        conversation_id: str | None = None,
    ) -> None:
        """Replace the latest pending system prompt with its resolved state."""

        conversation = self.get(conversation_id or self.active_id)
        if conversation is None:
            return
        for message in reversed(conversation.messages):
            if message.get("waiting") is True:
                message["content"] = content
                message["waiting"] = False
                self.save()
                return

    def delete_message(self, conversation_id: str, index: int) -> bool:
        """Delete one message permanently and recalculate the session title."""

        conversation = self.get(conversation_id)
        if conversation is None or not 0 <= index < len(conversation.messages):
            return False
        del conversation.messages[index]
        first_user = next(
            (
                str(message.get("content", "")).strip()
                for message in conversation.messages
                if message.get("role") == "user"
            ),
            "",
        )
        conversation.title = first_user[:20] or "新会话"
        self.save()
        return True

    def add_log(
        self,
        log: dict[str, Any],
        *,
        conversation_id: str | None = None,
    ) -> None:
        """Append one compact log record without resetting earlier records."""

        conversation = self.get(conversation_id or self.active_id)
        if conversation is None:
            return
        conversation.logs.append(dict(log))
        self.save()

    def add_process(
        self,
        level: int,
        summary: str,
        *,
        conversation_id: str | None = None,
    ) -> None:
        """Append one safe high-level work-process summary."""

        conversation = self.get(conversation_id or self.active_id)
        if conversation is None or not summary.strip():
            return
        conversation.process.append(
            {"level": max(0, min(level, 3)), "text": summary.strip()}
        )
        self.save()
