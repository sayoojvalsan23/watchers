# Himalayan Early Warning — agent instructions

Read `CONSTRAINTS.md` before writing code. Do not weaken it to make a change pass.

This is a life-safety system. Two rules override any general engineering skill:

1. The "Protected invariants — DO NOT SIMPLIFY" table in `CONSTRAINTS.md` lists
   machinery that is deliberately redundant. A simplification or refactoring
   pass will flag all of it. That judgement is wrong here. Surface it, do not
   act on it.
2. The Phase 0 gate is paired: false-alarm rate AND curated-event recall on
   **real catalogue records**. Never assert recall against a hand-written
   fixture — that is how a green suite hid a missed founding event.
