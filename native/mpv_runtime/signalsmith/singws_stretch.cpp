// Thin C wrapper around Signalsmith Stretch (MIT) for mpv's af_signalsmith filter.
// Key (transpose) and tempo (time ratio) are separate inputs: the transpose factor only ever comes from set_pitch, and the
// time ratio is simply how many output samples the caller asks for per input block.
#include <cmath>
#include <new>
#include <vector>
#include "signalsmith-stretch.h"

namespace {
struct Stretch {
    signalsmith::stretch::SignalsmithStretch<float> s;
    int channels = 0;
    std::vector<const float *> in;
    std::vector<float *> out;
};
}

extern "C" {

void *singws_stretch_new(int channels, int rate) {
    if (channels < 1 || channels > 64 || rate < 8000) return nullptr;
    Stretch *p = new (std::nothrow) Stretch();
    if (!p) return nullptr;
    p->channels = channels;
    p->in.resize(channels);
    p->out.resize(channels);
    p->s.presetDefault(channels, (float)rate);
    return p;
}

void singws_stretch_free(void *h) { delete static_cast<Stretch *>(h); }
void singws_stretch_reset(void *h) { static_cast<Stretch *>(h)->s.reset(); }

void singws_stretch_set_pitch(void *h, double factor) {
    static_cast<Stretch *>(h)->s.setTransposeFactor((float)factor);
}

int singws_stretch_latency(void *h) {
    Stretch *p = static_cast<Stretch *>(h);
    return p->s.inputLatency() + p->s.outputLatency();
}

int singws_stretch_output_latency(void *h) { return static_cast<Stretch *>(h)->s.outputLatency(); }

void singws_stretch_process(void *h, const float *const *in, int n, float *const *out, int m) {
    Stretch *p = static_cast<Stretch *>(h);
    for (int c = 0; c < p->channels; ++c) { p->in[c] = in[c]; p->out[c] = out[c]; }
    p->s.process(p->in.data(), n, p->out.data(), m);
}

void singws_stretch_flush(void *h, float *const *out, int m) {
    Stretch *p = static_cast<Stretch *>(h);
    for (int c = 0; c < p->channels; ++c) p->out[c] = out[c];
    p->s.flush(p->out.data(), m);
}

}
