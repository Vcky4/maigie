# Offline-first & Sync (Mobile)

* Local store: Expo SQLite (with WatermelonDB or direct sqlite) to persist notes and schedule.
* Sync strategy: optimistic writes, background sync when online, conflict resolution by timestamp and last-writer-wins or user merge UI for complex conflicts.
* Queue outbound actions (create note, schedule) to be retried until acknowledged.

