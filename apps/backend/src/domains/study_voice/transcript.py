"""What was said, held only for as long as the session is open.

**Nothing here is ever written to storage.** This is a bounded buffer in the memory of the process running
one relay. When the socket closes the object is dropped and the conversation is gone. There is no table, no
cache key, no expiry job, and no retention policy to write, because there is nothing retained.

That is a deliberate decision and it is the opposite of what the deleted implementation did. That version
accumulated every turn and, every six of them, sent the recent transcript to a model and appended the result
to a `Note` — automatically, with no learner action and no way to decline. A learner thinking aloud and
getting it wrong had their wrong reasoning written into their own notes as a side effect of speaking. The
recovery port left that out, and this file exists so the *useful* half — turning a conversation into a note —
can happen when the learner asks for it, on material we were already holding to run the session.

## Why fragments are coalesced

Provider transcription arrives in pieces: "I think", "the answer is", "reciprocal". The original appended
each piece as its own turn, which is why its note trigger fired "every six turns" when six turns might be
one sentence. Consecutive pieces from the same speaker are joined here, so a turn means a speaker change.
That makes the length gate on note generation mean what it says, and it stops the prompt reading as though
the learner interrupted themselves nine times.

## Why it is bounded

An hour of talking is a few tens of kilobytes, which is not a memory problem in itself — but an unbounded
buffer in a long-lived connection is a leak waiting for one unusual session. The oldest turns are dropped
first, so what survives is the part of the conversation a note would be written from anyway.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Turns kept. A speaker change per turn, so this is a long conversation, not a long-winded one.
MAX_TURNS = 400

#: Total characters kept across all turns. Trimmed from the oldest end.
MAX_CHARS = 40_000

#: Below this, there is not enough conversation to write anything worth keeping. Two exchanges.
MIN_TURNS_FOR_NOTE = 4


@dataclass(slots=True)
class Turn:
    role: str  # "user" | "assistant"
    text: str


@dataclass(slots=True)
class SessionTranscript:
    """The live conversation buffer for one session. In memory, for the session's lifetime, only."""

    turns: list[Turn] = field(default_factory=list)

    def add(self, role: str, text: str) -> None:
        """Record a fragment, joining it to the previous one when the same speaker is still talking."""
        cleaned = text.strip()
        if not cleaned:
            return

        if self.turns and self.turns[-1].role == role:
            previous = self.turns[-1]
            # Identical repeats happen: some providers resend the tail of a transcription as it settles.
            if previous.text.endswith(cleaned):
                return
            previous.text = f"{previous.text} {cleaned}".strip()
        else:
            self.turns.append(Turn(role=role, text=cleaned))

        self._trim()

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    def has_enough_for_a_note(self) -> bool:
        return len(self.turns) >= MIN_TURNS_FOR_NOTE

    def render(self, limit: int = MAX_CHARS) -> str:
        """The conversation as text, most recent kept when it has to be cut."""
        rendered = "\n".join(f"{turn.role.upper()}: {turn.text}" for turn in self.turns)
        return rendered[-limit:] if len(rendered) > limit else rendered

    def clear(self) -> None:
        self.turns.clear()

    def snapshot(self) -> SessionTranscript:
        """An independent copy, for a reader that outlives the socket.

        The note written at the end of a session is a detached task doing a model call, and the socket
        handler wipes this buffer in its own `finally` — so the two overlap. Without a copy the task checked
        the length against a full buffer, awaited a credit check, and then rendered an emptied one, producing
        a note whose entire content was the model observing that it had been given no transcript.

        The `Turn` objects are rebuilt rather than shared, because `add` mutates the last turn's text in place
        when the same speaker continues. Sharing them would leave a copy that a still-running relay could
        still change underneath its reader.
        """
        return SessionTranscript(
            turns=[Turn(role=turn.role, text=turn.text) for turn in self.turns]
        )

    def _trim(self) -> None:
        if len(self.turns) > MAX_TURNS:
            del self.turns[: len(self.turns) - MAX_TURNS]
        total = sum(len(turn.text) for turn in self.turns)
        while total > MAX_CHARS and len(self.turns) > 1:
            total -= len(self.turns[0].text)
            del self.turns[0]
