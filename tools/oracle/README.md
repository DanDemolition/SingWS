# Frozen Python KaraFun reference (2026-11-22)

Copies of `karafun_web_host.py` and `karafun_web.py` as they were when the Rust driver became the default. Nothing imports them yet: the parity tools
(`tools/karafun_*_parity.py`, `mutate_karafun_*.py`) still use the live files. When the Python KaraFun rules are removed from the app (see
`docs/design/karafun-python-rules-removal.md`, step 4), point those tools here so the Rust code keeps a reference to compare with.

The Rust driver has two rules the Python host never had (a song clock stuck at 0 restarts the player; a player that ignores 3 stops is closed).
The parity tools switch them off with `SINGWS_KF_OLD_RULES=1`.
