//! Single-producer / single-consumer lock-free ring of f32 samples (interleaved stereo in this crate).
//! The decoder thread is the only producer and the audio callback the only consumer; neither ever blocks or allocates.

use std::cell::UnsafeCell;
use std::sync::atomic::{AtomicUsize, Ordering};

pub struct Ring {
    buf: Box<[UnsafeCell<f32>]>,
    /// Total samples ever written / read (monotonic; index = counter % capacity).
    head: AtomicUsize,
    tail: AtomicUsize,
}

// SAFETY: one producer writes only the free region [head, tail+cap), one consumer reads only the filled region [tail, head);
// the regions never overlap and the counters are published with release/acquire.
unsafe impl Sync for Ring {}
unsafe impl Send for Ring {}

impl Ring {
    pub fn new(samples: usize) -> Self {
        let cap = samples.max(2);
        Self { buf: (0..cap).map(|_| UnsafeCell::new(0.0)).collect(), head: AtomicUsize::new(0), tail: AtomicUsize::new(0) }
    }

    pub fn capacity(&self) -> usize {
        self.buf.len()
    }

    /// Samples currently readable.
    pub fn len(&self) -> usize {
        self.head.load(Ordering::Acquire).wrapping_sub(self.tail.load(Ordering::Acquire))
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// Producer only. Writes as many samples as fit; returns how many.
    pub fn push(&self, data: &[f32]) -> usize {
        let cap = self.buf.len();
        let head = self.head.load(Ordering::Relaxed);
        let tail = self.tail.load(Ordering::Acquire);
        let free = cap - head.wrapping_sub(tail);
        let n = data.len().min(free);
        for (i, s) in data[..n].iter().enumerate() {
            // SAFETY: slot (head+i) % cap is in the free region, owned by the producer until `head` is published.
            unsafe { *self.buf[(head + i) % cap].get() = *s };
        }
        self.head.store(head.wrapping_add(n), Ordering::Release);
        n
    }

    /// Consumer only. Reads up to `out.len()` samples; returns how many.
    pub fn pop(&self, out: &mut [f32]) -> usize {
        let cap = self.buf.len();
        let tail = self.tail.load(Ordering::Relaxed);
        let head = self.head.load(Ordering::Acquire);
        let n = out.len().min(head.wrapping_sub(tail));
        for (i, o) in out[..n].iter_mut().enumerate() {
            // SAFETY: slot (tail+i) % cap is in the filled region, owned by the consumer until `tail` is published.
            *o = unsafe { *self.buf[(tail + i) % cap].get() };
        }
        self.tail.store(tail.wrapping_add(n), Ordering::Release);
        n
    }

    /// Consumer only: throw away everything currently readable. Returns the number of samples dropped.
    pub fn discard_all(&self) -> usize {
        let head = self.head.load(Ordering::Acquire);
        let tail = self.tail.load(Ordering::Relaxed);
        self.tail.store(head, Ordering::Release);
        head.wrapping_sub(tail)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    #[test]
    fn fifo_order_wraparound_and_capacity() {
        let r = Ring::new(8);
        assert_eq!(r.push(&[1.0, 2.0, 3.0, 4.0, 5.0, 6.0]), 6);
        assert_eq!(r.push(&[7.0, 8.0, 9.0]), 2); // only 2 free
        let mut out = [0.0; 5];
        assert_eq!(r.pop(&mut out), 5);
        assert_eq!(out, [1.0, 2.0, 3.0, 4.0, 5.0]);
        assert_eq!(r.push(&[10.0, 11.0, 12.0, 13.0]), 4); // wraps
        let mut all = [0.0; 16];
        let n = r.pop(&mut all);
        assert_eq!(&all[..n], &[6.0, 7.0, 8.0, 10.0, 11.0, 12.0, 13.0]);
        assert!(r.is_empty());
    }

    #[test]
    fn discard_all_empties_the_ring() {
        let r = Ring::new(16);
        r.push(&[1.0; 10]);
        assert_eq!(r.discard_all(), 10);
        assert!(r.is_empty());
        assert_eq!(r.push(&[2.0; 16]), 16);
    }

    #[test]
    fn two_threads_move_a_million_samples_in_order() {
        let r = Arc::new(Ring::new(1024));
        let total = 1_000_000usize;
        let p = r.clone();
        let producer = std::thread::spawn(move || {
            let mut next = 0usize;
            let mut chunk = vec![0.0f32; 333];
            while next < total {
                let n = chunk.len().min(total - next);
                for (i, c) in chunk[..n].iter_mut().enumerate() {
                    *c = (next + i) as f32;
                }
                let mut off = 0;
                while off < n {
                    let w = p.push(&chunk[off..n]);
                    off += w;
                    if w == 0 {
                        std::thread::yield_now();
                    }
                }
                next += n;
            }
        });
        let mut got = 0usize;
        let mut buf = vec![0.0f32; 257];
        while got < total {
            let n = r.pop(&mut buf);
            for (i, v) in buf[..n].iter().enumerate() {
                assert_eq!(*v, (got + i) as f32, "out of order at {}", got + i);
            }
            got += n;
            if n == 0 {
                std::thread::yield_now();
            }
        }
        producer.join().unwrap();
    }
}
