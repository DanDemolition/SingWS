//! Reference-signal and robustness tests. Media is generated on the fly; nothing outside the temp dir is touched.
use std::f64::consts::PI;
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};

use singws_analysis::{AnalysisError, AnalysisOptions, analyze_path};

static COUNTER: AtomicU32 = AtomicU32::new(0);

fn temp_path(ext: &str) -> PathBuf {
    let n = COUNTER.fetch_add(1, Ordering::SeqCst);
    std::env::temp_dir().join(format!("singws-analysis-test-{}-{n}.{ext}", std::process::id()))
}

/// 16-bit PCM WAV, interleaved.
fn wav_bytes(rate: u32, channels: u16, samples: &[f32]) -> Vec<u8> {
    let data_len = (samples.len() * 2) as u32;
    let mut b = Vec::new();
    b.extend_from_slice(b"RIFF");
    b.extend_from_slice(&(36 + data_len).to_le_bytes());
    b.extend_from_slice(b"WAVEfmt ");
    b.extend_from_slice(&16u32.to_le_bytes());
    b.extend_from_slice(&1u16.to_le_bytes());
    b.extend_from_slice(&channels.to_le_bytes());
    b.extend_from_slice(&rate.to_le_bytes());
    b.extend_from_slice(&(rate * channels as u32 * 2).to_le_bytes());
    b.extend_from_slice(&(channels * 2).to_le_bytes());
    b.extend_from_slice(&16u16.to_le_bytes());
    b.extend_from_slice(b"data");
    b.extend_from_slice(&data_len.to_le_bytes());
    for s in samples {
        b.extend_from_slice(&((s.clamp(-1.0, 1.0) * 32767.0).round() as i16).to_le_bytes());
    }
    b
}

/// `segments`: (seconds, amplitude) pairs; stereo, in-phase sine at 997 Hz.
fn stereo_signal(rate: u32, segments: &[(f64, f64)]) -> Vec<f32> {
    let mut out = Vec::new();
    let mut n: u64 = 0;
    for &(secs, amp) in segments {
        for _ in 0..(secs * rate as f64) as u64 {
            let v = (amp * (2.0 * PI * 997.0 * n as f64 / rate as f64).sin()) as f32;
            out.push(v);
            out.push(v);
            n += 1;
        }
    }
    out
}

fn db_to_amp(db: f64) -> f64 {
    10f64.powf(db / 20.0)
}

/// 32-bit float WAV (can hold values above full scale).
fn wav_f32_bytes(rate: u32, channels: u16, samples: &[f32]) -> Vec<u8> {
    let data_len = (samples.len() * 4) as u32;
    let mut b = Vec::new();
    b.extend_from_slice(b"RIFF");
    b.extend_from_slice(&(36 + data_len).to_le_bytes());
    b.extend_from_slice(b"WAVEfmt ");
    b.extend_from_slice(&16u32.to_le_bytes());
    b.extend_from_slice(&3u16.to_le_bytes());
    b.extend_from_slice(&channels.to_le_bytes());
    b.extend_from_slice(&rate.to_le_bytes());
    b.extend_from_slice(&(rate * channels as u32 * 4).to_le_bytes());
    b.extend_from_slice(&(channels * 4).to_le_bytes());
    b.extend_from_slice(&32u16.to_le_bytes());
    b.extend_from_slice(b"data");
    b.extend_from_slice(&data_len.to_le_bytes());
    for s in samples {
        b.extend_from_slice(&s.to_le_bytes());
    }
    b
}

fn write(ext: &str, bytes: &[u8]) -> PathBuf {
    let p = temp_path(ext);
    std::fs::write(&p, bytes).unwrap();
    p
}

fn run(p: &PathBuf) -> Result<singws_analysis::AnalysisResult, AnalysisError> {
    let r = analyze_path(p, &AnalysisOptions::default(), &AtomicBool::new(false));
    let _ = std::fs::remove_file(p);
    r
}

#[test]
fn ebu_reference_tone_measures_minus_23_lufs() {
    // EBU Tech 3341: a 997 Hz stereo sine at -23 dBFS per channel reads -23.0 LUFS.
    for rate in [44_100u32, 48_000] {
        let p = write("wav", &wav_bytes(rate, 2, &stereo_signal(rate, &[(12.0, db_to_amp(-23.0))])));
        let r = run(&p).unwrap();
        let lufs = r.integrated_lufs.expect("measurable");
        assert!((lufs - -23.0).abs() <= 0.15, "rate {rate}: got {lufs}");
        assert!((r.sample_peak_dbfs - -23.0).abs() <= 0.15, "peak {}", r.sample_peak_dbfs);
        assert!((r.duration_s - 12.0).abs() < 0.01);
    }
}

#[test]
fn sample_peak_of_half_scale_is_minus_six_db() {
    let p = write("wav", &wav_bytes(44_100, 2, &stereo_signal(44_100, &[(5.0, 0.5)])));
    let r = run(&p).unwrap();
    assert!((r.sample_peak_dbfs - -6.0).abs() <= 0.1, "{}", r.sample_peak_dbfs);
}

#[test]
fn peak_above_full_scale_is_reported_positive() {
    // The libmpv cache stores positive peaks for hot files too (up to +1.6 dBFS in the local cache); do not clamp.
    let rate = 44_100;
    let p = write("wav", &wav_f32_bytes(rate, 2, &stereo_signal(rate, &[(4.0, 1.5)])));
    let r = run(&p).unwrap();
    assert!((r.sample_peak_dbfs - 3.5).abs() < 0.1, "{}", r.sample_peak_dbfs);
}

#[test]
fn leading_and_trailing_silence_define_the_audible_span() {
    let rate = 44_100;
    let sig = stereo_signal(rate, &[(1.0, 0.0), (3.0, 0.3), (1.0, 0.0)]);
    let r = run(&write("wav", &wav_bytes(rate, 2, &sig))).unwrap();
    assert!((r.audio_start_s.unwrap() - 1.0).abs() < 0.05, "{:?}", r.audio_start_s);
    assert!((r.audio_end_s.unwrap() - 4.0).abs() < 0.05, "{:?}", r.audio_end_s);
    assert!((r.duration_s - 5.0).abs() < 0.01);
}

#[test]
fn no_silence_means_whole_file_is_audible() {
    let rate = 44_100;
    let r = run(&write("wav", &wav_bytes(rate, 2, &stereo_signal(rate, &[(4.0, 0.3)])))).unwrap();
    assert_eq!(r.audio_start_s, Some(0.0));
    assert!((r.audio_end_s.unwrap() - 4.0).abs() < 0.01);
}

#[test]
fn digital_silence_has_no_loudness_and_no_audible_span() {
    let rate = 44_100;
    let r = run(&write("wav", &wav_bytes(rate, 2, &stereo_signal(rate, &[(6.0, 0.0)])))).unwrap();
    assert_eq!(r.integrated_lufs, None);
    assert_eq!((r.audio_start_s, r.audio_end_s), (None, None));
}

#[test]
fn short_gap_below_threshold_is_not_silence() {
    // A 0.2 s dip is shorter than the 0.3 s minimum, so it must not move the boundaries.
    let rate = 44_100;
    let sig = stereo_signal(rate, &[(2.0, 0.3), (0.2, 0.0), (2.0, 0.3)]);
    let r = run(&write("wav", &wav_bytes(rate, 2, &sig))).unwrap();
    assert_eq!(r.audio_start_s, Some(0.0));
    assert!((r.audio_end_s.unwrap() - 4.2).abs() < 0.02);
}

#[test]
fn mono_files_work() {
    let rate = 48_000;
    let mono: Vec<f32> = stereo_signal(rate, &[(8.0, db_to_amp(-20.0))]).chunks(2).map(|f| f[0]).collect();
    let r = run(&write("wav", &wav_bytes(rate, 1, &mono))).unwrap();
    assert_eq!(r.channels, 1);
    assert!(r.integrated_lufs.is_some());
}

#[test]
fn envelope_has_one_window_per_100ms() {
    let rate = 44_100;
    let p = write("wav", &wav_bytes(rate, 2, &stereo_signal(rate, &[(3.0, 0.5)])));
    let opts = AnalysisOptions { want_envelope: true, ..Default::default() };
    let r = analyze_path(&p, &opts, &AtomicBool::new(false)).unwrap();
    let _ = std::fs::remove_file(&p);
    let env = r.envelope_100ms_db.unwrap();
    assert_eq!(env.len(), 30);
    // Sine amplitude 0.5 -> RMS 0.354 -> -9.0 dB.
    assert!((env[10] - -9.0).abs() < 0.2, "{}", env[10]);
}

// ---- robustness ----

#[test]
fn empty_file_is_an_error_not_a_panic() {
    assert!(run(&write("mp3", b"")).is_err());
}

#[test]
fn garbage_with_audio_extension_is_an_error() {
    let junk: Vec<u8> = (0..50_000u32).map(|i| (i.wrapping_mul(2_654_435_761) >> 24) as u8).collect();
    for ext in ["mp3", "flac", "wav", "m4a"] {
        assert!(run(&write(ext, &junk)).is_err(), "{ext}");
    }
}

#[test]
fn truncated_wav_keeps_what_decoded() {
    let rate = 44_100;
    let bytes = wav_bytes(rate, 2, &stereo_signal(rate, &[(6.0, 0.3)]));
    let cut = &bytes[..bytes.len() / 2];
    match run(&write("wav", cut)) {
        Ok(r) => assert!(r.duration_s > 1.0 && r.duration_s < 6.0, "{}", r.duration_s),
        Err(e) => panic!("a truncated file with usable audio should still analyse, got {e}"),
    }
}

#[test]
fn missing_file_is_unreadable() {
    let r = analyze_path(&PathBuf::from("/nonexistent/none.mp3"), &AnalysisOptions::default(), &AtomicBool::new(false));
    assert!(matches!(r, Err(AnalysisError::Unreadable(_))));
}

#[test]
fn cancel_flag_stops_analysis() {
    let rate = 44_100;
    let p = write("wav", &wav_bytes(rate, 2, &stereo_signal(rate, &[(5.0, 0.3)])));
    let r = analyze_path(&p, &AnalysisOptions::default(), &AtomicBool::new(true));
    let _ = std::fs::remove_file(&p);
    assert!(matches!(r, Err(AnalysisError::Cancelled)));
}

#[test]
fn too_long_audio_is_refused() {
    let rate = 8_000;
    let p = write("wav", &wav_bytes(rate, 1, &vec![0.1; rate as usize * 10]));
    let opts = AnalysisOptions { max_seconds: 3.0, ..Default::default() };
    let r = analyze_path(&p, &opts, &AtomicBool::new(false));
    let _ = std::fs::remove_file(&p);
    assert!(matches!(r, Err(AnalysisError::TooLong)));
}

// ---- MP3+G archives ----

fn zip_with(entries: &[(&str, &[u8])], deflate: bool) -> Vec<u8> {
    let mut buf = std::io::Cursor::new(Vec::new());
    {
        let mut w = zip::ZipWriter::new(&mut buf);
        let method = if deflate { zip::CompressionMethod::Deflated } else { zip::CompressionMethod::Stored };
        let opts = zip::write::SimpleFileOptions::default().compression_method(method);
        for (name, data) in entries {
            w.start_file(*name, opts).unwrap();
            w.write_all(data).unwrap();
        }
        w.finish().unwrap();
    }
    buf.into_inner()
}

#[test]
fn audio_member_inside_a_zip_is_analysed_and_junk_is_ignored() {
    let rate = 44_100;
    let wav = wav_bytes(rate, 2, &stereo_signal(rate, &[(8.0, db_to_amp(-23.0))]));
    for deflate in [false, true] {
        let z = zip_with(
            &[("__MACOSX/._song.wav", b"junk-resource-fork"), ("song.cdg", &[0u8; 4096]), ("song.wav", &wav)],
            deflate,
        );
        let r = run(&write("zip", &z)).unwrap();
        assert!((r.integrated_lufs.unwrap() - -23.0).abs() <= 0.15);
    }
}

#[test]
fn zip_without_audio_reports_no_audio() {
    let z = zip_with(&[("song.cdg", &[0u8; 1024])], true);
    assert!(matches!(run(&write("zip", &z)), Err(AnalysisError::NoAudio)));
}

#[test]
fn damaged_zip_is_corrupt() {
    let mut z = zip_with(&[("song.wav", &[1u8; 2048])], false);
    z.truncate(z.len() / 2);
    assert!(matches!(run(&write("zip", &z)), Err(AnalysisError::Corrupt(_)) | Err(AnalysisError::Unreadable(_))));
}
