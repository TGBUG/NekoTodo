# Re-decomposition is additive-only

Re-running decomposition on an SourceInfo never deletes or modifies existing Tasks. The agent only creates Tasks for SourceItems that have no Tasks yet, and only creates new SourceItems for content that is not yet itemized. The invariant: **the agent never destroys the user's manual work** — edits, progress, and completions always survive a re-run.

The instinctive implementation is full replacement (delete the SourceInfo's generated Tasks, regenerate), which silently destroys progress and manual edits. Smart merging (update when the source changed, preserve manual edits) was considered and rejected as too complex for the value. Full replacement is rejected because it violates the invariant. Additive-only is simple, predictable, and safe; if the user wants a SourceItem regenerated, they delete its Tasks and re-run.

Consequences: re-decomposition only *adds*. New content (e.g. the teacher adds items) reaches the user through new SourceItems; changed content reaches them as new SourceItems too — old Tasks are not retroactively edited.
