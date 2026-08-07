# Priority is a positional rank, not a fixed scale

A Task's `priority` is its **position in the user's ordered task list**: 1 at the top (most urgent), maximum = the current total number of the user's Tasks, positions contiguous with no gaps and no ties. Inserting, moving, or deleting a Task shifts the others. The Agent reorders Tasks to express urgency.

This collapses "urgency" and "order" into one field — there is no separate priority scale and no separate sort order, so the list *is* the priority. A fixed scale (e.g. P1–P5) with a separate order was rejected as two sources of truth that can disagree. The cost is that reordering is a shifting operation over contiguous positions rather than an attribute assignment.

Consequences: adding a Task appends it to the end; expressing urgency means moving it up. The frontend renders the list in priority order, optionally grouped by Category (grouping preserves relative order within each group).
