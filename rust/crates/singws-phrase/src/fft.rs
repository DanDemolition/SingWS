//! A small complex FFT for any length: iterative radix-2 for powers of two, Bluestein's chirp-z for the rest. Accuracy is a few 1e-13 relative in f64, far below what the
//! float32 numpy FFTs it is compared with carry.
use std::f64::consts::PI;

#[derive(Clone, Copy, Debug)]
pub struct C {
    pub re: f64,
    pub im: f64,
}

impl C {
    pub fn new(re: f64, im: f64) -> C {
        C { re, im }
    }
    fn mul(self, o: C) -> C {
        C::new(self.re * o.re - self.im * o.im, self.re * o.im + self.im * o.re)
    }
    pub fn conj(self) -> C {
        C::new(self.re, -self.im)
    }
    pub fn abs(self) -> f64 {
        self.re.hypot(self.im)
    }
}

fn fft_pow2(a: &mut [C], inverse: bool) {
    let n = a.len();
    let mut j = 0usize;
    for i in 1..n {
        let mut bit = n >> 1;
        while j & bit != 0 {
            j ^= bit;
            bit >>= 1;
        }
        j ^= bit;
        if i < j {
            a.swap(i, j);
        }
    }
    let mut len = 2;
    while len <= n {
        let ang = 2.0 * PI / len as f64 * if inverse { 1.0 } else { -1.0 };
        let tw: Vec<C> = (0..len / 2).map(|k| C::new((ang * k as f64).cos(), (ang * k as f64).sin())).collect();
        for start in (0..n).step_by(len) {
            for k in 0..len / 2 {
                let u = a[start + k];
                let v = a[start + k + len / 2].mul(tw[k]);
                a[start + k] = C::new(u.re + v.re, u.im + v.im);
                a[start + k + len / 2] = C::new(u.re - v.re, u.im - v.im);
            }
        }
        len <<= 1;
    }
    if inverse {
        let inv = 1.0 / n as f64;
        for x in a.iter_mut() {
            x.re *= inv;
            x.im *= inv;
        }
    }
}

/// Forward (or inverse, normalized by 1/n) DFT of any length.
pub fn fft(input: &[C], inverse: bool) -> Vec<C> {
    let n = input.len();
    if n <= 1 {
        return input.to_vec();
    }
    if n.is_power_of_two() {
        let mut a = input.to_vec();
        fft_pow2(&mut a, inverse);
        return a;
    }
    // Bluestein: X[k] = conj-chirp[k] * sum_j (x[j] * conj-chirp[j]) * chirp[k-j]
    let sign = if inverse { 1.0 } else { -1.0 };
    let chirp: Vec<C> = (0..n)
        .map(|k| {
            let kk = (k as u128 * k as u128 % (2 * n as u128)) as f64;
            let ang = sign * PI * kk / n as f64;
            C::new(ang.cos(), ang.sin())
        })
        .collect();
    let m = (2 * n - 1).next_power_of_two();
    let mut a = vec![C::new(0.0, 0.0); m];
    let mut b = vec![C::new(0.0, 0.0); m];
    for k in 0..n {
        a[k] = input[k].mul(chirp[k]);
    }
    b[0] = chirp[0].conj();
    for k in 1..n {
        b[k] = chirp[k].conj();
        b[m - k] = chirp[k].conj();
    }
    fft_pow2(&mut a, false);
    fft_pow2(&mut b, false);
    for i in 0..m {
        a[i] = a[i].mul(b[i]);
    }
    fft_pow2(&mut a, true);
    let mut out: Vec<C> = (0..n).map(|k| a[k].mul(chirp[k])).collect();
    if inverse {
        let inv = 1.0 / n as f64;
        for x in out.iter_mut() {
            x.re *= inv;
            x.im *= inv;
        }
    }
    out
}

/// `np.fft.rfft(x, n)` (zero padded or cut to n): the first n/2+1 bins.
pub fn rfft(x: &[f64], n: usize) -> Vec<C> {
    let data: Vec<C> = (0..n).map(|i| C::new(if i < x.len() { x[i] } else { 0.0 }, 0.0)).collect();
    fft(&data, false).into_iter().take(n / 2 + 1).collect()
}

/// `np.fft.irfft(spec)` with the default output length 2*(m-1).
pub fn irfft(spec: &[C]) -> Vec<f64> {
    let m = spec.len();
    let n = 2 * (m - 1);
    let mut full = vec![C::new(0.0, 0.0); n];
    for k in 0..m {
        full[k] = spec[k];
    }
    for k in 1..m - 1 {
        full[n - k] = spec[k].conj();
    }
    full[0].im = 0.0;
    if n / 2 < m {
        full[n / 2].im = 0.0;
    }
    fft(&full, true).into_iter().map(|c| c.re).collect()
}
