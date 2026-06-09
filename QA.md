# Manual QA — SingWS desktop (real hardware / GUI)

These cover the parts that can't be verified headless. Run on **Intel and Apple
Silicon**. Location / "Accept Requests" QA lives in the SingWS-Server repo.

## Phrase-Aligned Song Start
- [ ] Right-click a queued song → **Phrase Start** submenu appears (Beginning /
      4 / 8 / 16 Bars / Custom…). Bar options compute when BPM is known.
- [ ] "Start at Beginning" clears it; the queue row shows a `START m:ss` badge.
- [ ] Custom dialog: waveform renders; marker lines are labeled
      4/8/16/Custom/Suggested; clicking a marker previews from that point.
- [ ] "Use suggestion" lands on a sensible intro-skip point.
- [ ] Play a **CDG**, an **MP4**, and an **MP3** with a phrase start set → audio
      starts at the offset, **lyrics / CDG / video stay in sync**, no gap/overlap.
- [ ] With a **Key change AND a tempo change** applied, a phrase start still
      keeps pitch/tempo correct after the offset start.
- [ ] Markers persist after re-queuing the same song.
- [ ] **Cloud sync**: set markers on machine A; start machine B on the same
      tenant → markers appear. **Export** → **Import** restores on a fresh machine.

## Rotation Lock
- [ ] Lock button is **hidden in Classic**, visible in **Rotation**.
- [ ] Toggling on turns it **yellow** ("Unlock"); the queue label shows `LOCKED`.
- [ ] While locked, add a new singer via **manual add**, a **web request**, and
      **Singer History** → each lands in the **next** rotation, not the current one.
- [ ] An existing singer adding a 2nd song **stays in their current slot**.
- [ ] Advance the rotation to the top → lock **auto-clears**.
- [ ] Switch Queue Mode to **Classic** → lock clears.
- [ ] Restart the app while locked → lock state is restored from settings.

## Phrase Start — auto-BPM & preview controls
- [ ] Open Custom Phrase Start on a song with **no BPM tag** → the BPM field
      fills in on its own (auto-detected) and the 4/8/16-bar marks compute.
- [ ] A suggested start is **pre-selected** when the dialog opens.
- [ ] **▶ Preview** plays from the chosen point; **⏸ Pause** pauses/resumes;
      **⏹ Stop** stops. Re-previewing restarts cleanly (no stacked audio).
- [ ] **Closing the dialog stops the preview** (no audio left on the main output).

## Intro Loop (between-songs beat loop)
- [ ] Settings → enable **Intro Loop**, pick **8 bars**.
- [ ] Queue 2+ songs; play one and let it **end** → the next song's intro starts
      **looping** at its phrase point; the queue label shows **`LOOPING`**.
- [ ] Listen at the wrap point — the loop repeats with **no click/gap**.
- [ ] Hit **Play/Next** → the loop releases and the track **continues past the
      loop and plays through** seamlessly (no restart).
- [ ] Works for **CDG**, **MP4**, and **MP3**.
- [ ] First time on a fresh song with no cached BPM: it may play normally once
      while it analyzes tempo in the background, then loop on the next encounter.
- [ ] Toggle Intro Loop **off** → normal between-songs behavior (BGM/idle) returns.
- [ ] CPU stays low while looping.

## Platforms
- [ ] Full pass on **Intel**.
- [ ] Full pass on **Apple Silicon**.
