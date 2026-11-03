//! Hand-written spot checks of the policy rules (the full proof is tools/bgm_policy_parity.py against the real Python player).
use serde_json::{json, Value};
use singws_bgm_policy::Policy;

fn snap(primary: Option<i64>, pos: f64, dur: f64) -> Value {
    json!({"primary": primary, "pos": pos, "dur": dur, "source_ended": false, "xf_finished": false, "lead": 0.0, "meter": 0.5,
           "engine_playing": true, "engine_paused": false, "master": 0.8, "settle_ms": 0,
           "settings": {"simple_audio_mode": true, "bg_volume": 0.8}, "live_volume": null, "karaoke": false,
           "files": {"/a": [true, 1, 1], "/b": [true, 1, 1]}, "records": {"/a": null, "/b": null}, "loud": {}, "responses": {}})
}

fn policy() -> Policy {
    Policy::new(&json!({"paths": ["/a", "/b"], "start_index": 0, "crossfade_ms": 5000, "volume": 0.8}))
}

fn names(cmds: &[Value]) -> Vec<String> {
    cmds.iter().map(|c| c["c"].as_str().unwrap().to_string()).collect()
}

#[test]
fn play_loads_then_plays() {
    let mut p = policy();
    let (cmds, _) = p.event(1000, "play", &json!({}), &snap(None, 0.0, 0.0));
    let n = names(&cmds);
    assert!(n.contains(&"load".to_string()) && n.contains(&"play".to_string()));
    assert_eq!(p.state()["is_playing"], json!(true));
}

#[test]
fn karaoke_blocks_play_but_not_allowed_fade_in() {
    let mut p = policy();
    let mut s = snap(None, 0.0, 0.0);
    s["karaoke"] = json!(true);
    assert!(p.event(1000, "play", &json!({}), &s).0.is_empty());
    assert!(p.event(1001, "fade_in", &json!({"ms": 1000}), &s).0.is_empty());
    let (cmds, _) = p.event(1002, "fade_in", &json!({"ms": 1000, "allow": true}), &s);
    assert_eq!(cmds.last().unwrap()["c"], json!("slide_master_volume"));
}

#[test]
fn tick_starts_crossfade_inside_the_window_only() {
    let mut p = policy();
    p.event(0, "play", &json!({}), &snap(None, 0.0, 0.0));
    let (early, _) = p.event(500, "tick", &json!({}), &snap(Some(1), 10.0, 20.0));
    assert!(!names(&early).contains(&"start_crossfade".to_string()));
    let (late, _) = p.event(1000, "tick", &json!({}), &snap(Some(1), 15.0, 20.0));
    let xf = late.iter().find(|c| c["c"] == "start_crossfade").expect("crossfade");
    assert_eq!(xf["a"][1], json!(5000));
    assert_eq!(p.state()["crossfade_active"], json!(true));
}

#[test]
fn stop_fade_timer_is_ignored_when_stale() {
    let mut p = policy();
    p.event(0, "play", &json!({}), &snap(None, 0.0, 0.0));
    let (c, _) = p.event(10, "stop_with_fade", &json!({"ms": 1000}), &snap(Some(1), 1.0, 20.0));
    assert_eq!(c.last().unwrap()["a"][2], json!([1]));
    p.event(20, "play", &json!({}), &snap(Some(1), 1.0, 20.0)); // bumps nothing, then a pause bumps the generation
    p.event(30, "pause", &json!({}), &snap(Some(1), 1.0, 20.0));
    let (late, _) = p.event(1040, "timer", &json!({"kind": "_stop_after_stop_fade", "args": [1]}), &snap(Some(1), 1.0, 20.0));
    assert!(late.is_empty());
}
