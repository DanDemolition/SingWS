SingWS Intel test kit
=====================

After installing an update (and after every show), on the Intel Mac:

  1. bash run_intel_checks.sh 1.0.4.5        (use the version you just installed)
     It checks the installed app, runs its bundled Rust programs on small known cases
     (it does NOT play anything, sign in to KaraFun or touch your queue), reads the
     saved settings and the newest log, and prints PASS / WARN / FAIL lines and a
     RESULT line. A copy is saved on the Desktop.
       - FAIL  = something is wrong: send me the saved report.
       - WARN  = look at it; often "open SingWS once on the new version, then run this again".
       - First run right after installing, BEFORE opening SingWS, the settings and log
         parts will WARN: that is normal.

  2. Open SingWS, run bash run_intel_checks.sh 1.0.4.5 again. Section 4 and 5 should be green.

  3. During the show, do the hand checks printed at the end of the report (section 6).

  4. After the show, quit SingWS and run:  bash tools/after_show.sh
     It makes one zip on the Desktop (show logs, stall stacks, the KaraFun display log).
     Send me the zip. Run run_intel_checks.sh once more: section 5 summarises the show.

If KaraFun fails mid-show: set  karafun_web_engine  to  python  in Settings (the old player).

Other tools in tools/ (optional, on the logs):
  karafun_rehearsal.sh   steps for rehearsing KaraFun
  karafun_web_preshow_check.sh   a quick pre-show check of the KaraFun player
  karafun_rust_report.py / shadow_report.py / stall_report.py   readable reports from a log
