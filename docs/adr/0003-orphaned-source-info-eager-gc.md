# Orphaned SourceInfos are garbage-collected eagerly

When a SourceInfo no longer has any Task referencing its SourceItems, it has no further purpose and is removed together with its SourceItems. Rather than a lazy sweep or manual cleanup, this happens **eagerly and transactionally**: the operation that deletes the last referencing Task deletes the orphaned SourceInfo (and its SourceItems) in the same transaction.

This keeps a strong invariant — a SourceInfo with no referencing Tasks does not exist — so the UI never shows empty shells. A lazy periodic sweep was rejected: it leaves ghost SourceInfos briefly visible and needs a scheduler. The cost is one extra query on every Task deletion, negligible on SQLite.

Consequences: deleting the last Task of a SourceInfo removes the SourceInfo too (a single mistake loses both). Accepted; the frontend is informed when a deletion also removed a parent SourceInfo. This is a *cleanup* path distinct from `DELETE /source-infos/{id}`, which cascades to all of the SourceInfo's Tasks by explicit user action.
