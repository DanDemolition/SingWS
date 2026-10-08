//! Properties the audio thread depends on: no allocation while processing, block-size independence, no NaN/denormal trouble.
use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;

use singws_dsp::{MasterProcessor, PARAM_COUNT};

struct Counting;

// Per-thread, so other tests running in parallel threads cannot be mistaken for allocations on this one.
thread_local! {
    static ALLOCS: Cell<usize> = const { Cell::new(0) };
}

fn bump() {
    let _ = ALLOCS.try_with(|c| c.set(c.get() + 1));
}

unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, l: Layout) -> *mut u8 {
        bump();
        unsafe { System.alloc(l) }
    }
    unsafe fn dealloc(&self, p: *mut u8, l: Layout) {
        unsafe { System.dealloc(p, l) }
    }
    unsafe fn realloc(&self, p: *mut u8, l: Layout, n: usize) -> *mut u8 {
        bump();
        unsafe { System.realloc(p, l, n) }
    }
}

#[global_allocator]
static A: Counting = Counting;

fn noise(n: usize, seed: u64) -> Vec<f32> {
    let mut s = seed;
    (0..n)
        .map(|_| {
            s = s.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            ((s >> 40) as f32 / (1u64 << 24) as f32 - 0.5) * 1.6
        })
        .collect()
}

fn everything_on() -> [f64; PARAM_COUNT] {
    let mut v = [0.0; PARAM_COUNT];
    // gate on, EQ on, exciter on, compressor on, limiter on
    v.copy_from_slice(&[
        1.0, -58.0, 1.6, 5.0, 140.0, -18.0, 1.0, 90.0, 1.0, 3200.0, 1.0, 0.7, 9000.0, 1.5, 0.15, 4000.0, 1.0, -20.0, 2.0, 6.0, 18.0, 180.0,
        4.0, 1.0, -1.0, 1.2, 80.0, -0.1,
    ]);
    v
}

#[test]
fn processing_does_not_allocate() {
    let mut p = MasterProcessor::new(48000.0, 2);
    p.set_params(&everything_on());
    let mut buf = noise(2400, 7);
    p.process_interleaved(&mut buf); // warm up
    let before = ALLOCS.with(|c| c.get());
    for _ in 0..200 {
        p.process_interleaved(&mut buf);
    }
    // set_params / configure / reset must not allocate either (they run on the audio thread when the side channel is drained)
    p.set_params(&everything_on());
    p.configure_stream(44100.0, 2);
    p.reset_state();
    p.process_interleaved(&mut buf);
    let after = ALLOCS.with(|c| c.get());
    assert_eq!(after - before, 0, "the processing path allocated {} time(s)", after - before);
}

#[test]
fn block_size_does_not_change_the_output() {
    let input = noise(48000 * 2, 11);
    let mut whole = input.clone();
    let mut a = MasterProcessor::new(48000.0, 2);
    a.set_params(&everything_on());
    a.process_interleaved(&mut whole);
    for block_frames in [1usize, 3, 100, 777, 4096] {
        let mut split = input.clone();
        let mut b = MasterProcessor::new(48000.0, 2);
        b.set_params(&everything_on());
        for chunk in split.chunks_mut(block_frames * 2) {
            b.process_interleaved(chunk);
        }
        assert_eq!(whole, split, "block of {block_frames} frames changed the result");
    }
}

#[test]
fn output_is_finite_and_within_the_ceiling_for_extreme_input() {
    let mut p = MasterProcessor::new(44100.0, 2);
    p.set_params(&everything_on());
    let mut buf: Vec<f32> = noise(44100, 3).iter().map(|v| v * 40.0).collect(); // far above full scale
    buf[100] = f32::MAX / 4.0;
    p.process_interleaved(&mut buf);
    let ceiling = 10f32.powf(-0.1 / 20.0) + 1e-6;
    assert!(buf.iter().all(|v| v.is_finite() && v.abs() <= ceiling), "non-finite or over-ceiling sample");
}

#[test]
fn silence_decays_to_zero_without_denormal_slowdown() {
    let mut p = MasterProcessor::new(48000.0, 2);
    p.set_params(&everything_on());
    let mut loud = noise(48000, 5);
    p.process_interleaved(&mut loud);
    let mut silence = vec![0.0f32; 48000 * 2 * 20]; // 20 s of stereo
    let start = std::time::Instant::now();
    p.process_interleaved(&mut silence);
    let took = start.elapsed();
    // The EQ and gain smoothers ring for a while after loud input; after 15 s it must be silent.
    assert!(silence[48000 * 2 * 15..].iter().all(|v| v.abs() < 1e-6), "tail did not decay");
    // 20 s of audio must take a small fraction of real time even on a slow machine.
    assert!(took.as_secs_f64() < 2.0, "20 s of silence took {took:?}");
}

#[test]
fn ragged_and_oversized_buffers_are_left_untouched() {
    let mut p = MasterProcessor::new(48000.0, 2);
    p.set_params(&everything_on());
    let mut odd = noise(2401, 9);
    let copy = odd.clone();
    p.process_interleaved(&mut odd);
    assert_eq!(odd, copy);
    let mut many = MasterProcessor::new(48000.0, 9);
    let mut buf = noise(9 * 100, 9);
    let copy = buf.clone();
    many.process_interleaved(&mut buf);
    assert_eq!(buf, copy);
}
