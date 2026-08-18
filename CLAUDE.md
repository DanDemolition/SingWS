# CLAUDE.md

`AGENTS.md` is the single source of truth for how to work in this repo. Codex
reads it directly; this file imports it so Claude Code reads the same rules
rather than a second copy that drifts.

@AGENTS.md
@HANDOFF.md

Put new durable guidance in `AGENTS.md`, never here — a rule that lives only in
this file is invisible to Codex, and a rule that lives in only one agent's
memory is invisible to both the other agent and to the operator.

`HANDOFF.md` carries the uncommitted state of the tree when work moves between
agents or machines. It is imported above so the current state is loaded at the
start of a session; update or delete it when the work it describes is committed.
