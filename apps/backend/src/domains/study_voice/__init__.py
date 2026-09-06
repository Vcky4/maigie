"""Study Voice — realtime spoken tutoring over a lesson.

## Why this is its own domain

Voice study sits between two existing domains and belongs to neither.

- It teaches a **course topic**, which `knowledge` owns: the course, its modules, topics, lesson
  sections, objectives and knowledge check are all read from there to brief the tutor.
- It is a **conversation with tool use**, which `intelligence` owns: the skill registry supplies
  `study_show_visual` and `complete_topic_and_continue`, and the provider adapters live there.

Putting it inside `knowledge` would make a content domain own a websocket relay, a provider socket and a
billing loop. Putting it inside `intelligence` would make a conversation domain reach into course
structure and topic completion. Both were tried in the original implementation, which lived in a flat
`src/routes/` + `src/services/` layout and consequently belonged nowhere — and was deleted in `4953972`
without anyone noticing, because no domain claimed it.

So it is a domain of its own that depends on both, in one direction only: `study_voice` imports from
`knowledge` and `intelligence`, and neither imports from here.

## Recovered, not written

This is a port of `src/services/gemini_live_service.py` and `src/routes/gemini_live.py` as they existed at
commit `4953972^`, adapted to SQLAlchemy and the domain layout. The billing arithmetic, the bridge
protocol, barge-in forwarding and tool dispatch are the original behaviour; what changed is persistence,
imports, and where session state lives. See `docs/VOICE_STUDY_SESSION_DESIGN.md` in the client repo.

Mounted at `/api/v1/gemini-live` for now, because two shipped clients already call that path. The name
puts a vendor in a public URL and should become `/study/voice`; that is a client-coordinated rename, not a
port decision.
"""

from .routes import router

__all__ = ["router"]
