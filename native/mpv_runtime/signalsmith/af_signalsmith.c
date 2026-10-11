/*
 * SingWS: pitch/tempo audio filter built on Signalsmith Stretch (MIT), replacing af_rubberband.
 * LGPL-2.1+ like the mpv file it is modelled on.
 *
 * Key and tempo are independent by construction:
 *   - pitch  = the `pitch-scale` option / `set-pitch` command only (the transpose factor);
 *   - tempo  = mpv's playback speed (MP_FILTER_COMMAND_SET_SPEED) only (the time ratio: n input samples -> n/speed output samples).
 * Changing one never feeds the other.
 */
#include <math.h>
#include <stdlib.h>

#include "config.h"

#include "audio/aframe.h"
#include "audio/format.h"
#include "common/common.h"
#include "filters/f_autoconvert.h"
#include "filters/filter_internal.h"
#include "filters/user_filters.h"
#include "options/m_option.h"

void *singws_stretch_new(int channels, int rate);
void singws_stretch_free(void *h);
void singws_stretch_reset(void *h);
void singws_stretch_set_pitch(void *h, double factor);
int singws_stretch_output_latency(void *h);
void singws_stretch_process(void *h, const float *const *in, int n, float *const *out, int m);
void singws_stretch_flush(void *h, float *const *out, int m);

struct f_opts {
    double scale;
};

struct priv {
    struct f_opts *opts;
    struct mp_pin *in_pin;
    struct mp_aframe *cur_format;
    struct mp_aframe_pool *out_pool;
    void *st;
    double speed;
    double pitch;
    double carry;       // fractional output samples owed to the time ratio
    double delay;       // input samples held inside the stretcher (for timestamps)
    bool flushed;
};

static bool update_pitch(struct priv *p, double new_pitch)
{
    if (new_pitch < 0.01 || new_pitch > 100.0)
        return false;
    p->pitch = new_pitch;
    if (p->st)
        singws_stretch_set_pitch(p->st, p->pitch);
    return true;
}

static void drop_state(struct priv *p)
{
    if (p->st)
        singws_stretch_free(p->st);
    p->st = NULL;
    p->carry = 0;
    p->delay = 0;
    p->flushed = false;
}

// Writes `samples` of output made by `fill` into a new frame and sends it downstream.
static bool emit(struct mp_filter *f, struct mp_aframe *like, int samples, double pts_end,
                 const float *const *in, int n, bool flush)
{
    struct priv *p = f->priv;
    struct mp_aframe *out = mp_aframe_new_ref(p->cur_format);
    if (mp_aframe_pool_allocate(p->out_pool, out, samples) < 0) {
        talloc_free(out);
        return false;
    }
    mp_aframe_copy_attributes(out, like);
    float *out_data[MP_NUM_CHANNELS] = {0};
    uint8_t **planes = mp_aframe_get_data_rw(out);
    int num_planes = mp_aframe_get_planes(out);
    for (int c = 0; c < num_planes; c++)
        out_data[c] = (void *)planes[c];
    if (flush)
        singws_stretch_flush(p->st, (float *const *)out_data, samples);
    else
        singws_stretch_process(p->st, in, n, (float *const *)out_data, samples);
    mp_aframe_set_size(out, samples);

    p->delay += n - samples * p->speed;
    if (pts_end != MP_NOPTS_VALUE) {
        double d = p->delay / mp_aframe_get_effective_rate(out);
        mp_aframe_set_pts(out, pts_end - d);
    }
    mp_aframe_mul_speed(out, p->speed);
    mp_pin_in_write(f->ppins[1], MAKE_FRAME(MP_FRAME_AUDIO, out));
    return true;
}

static void af_signalsmith_process(struct mp_filter *f)
{
    struct priv *p = f->priv;

    if (!mp_pin_in_needs_data(f->ppins[1]))
        return;

    struct mp_frame frame = mp_pin_out_read(p->in_pin);
    if (!frame.type)
        return; // no new data yet

    if (frame.type == MP_FRAME_EOF) {
        if (p->st && !p->flushed) {
            // Tail still inside the stretcher.
            struct mp_aframe *like = p->cur_format;
            int tail = singws_stretch_output_latency(p->st);
            p->flushed = true;
            if (tail > 0 && !emit(f, like, tail, MP_NOPTS_VALUE, NULL, 0, true))
                goto error;
            mp_pin_out_repeat_eof(p->in_pin);
            return;
        }
        drop_state(p);
        mp_pin_in_write(f->ppins[1], MP_EOF_FRAME);
        return;
    }
    if (frame.type != MP_FRAME_AUDIO) {
        MP_ERR(f, "unexpected frame type\n");
        goto error;
    }

    struct mp_aframe *in = frame.data;
    if (mp_aframe_get_format(in) != AF_FORMAT_FLOATP) {
        talloc_free(in);
        goto error;
    }
    if (p->st && !mp_aframe_config_equals(in, p->cur_format))
        drop_state(p); // format change: restart cleanly (a few ms of tail are discarded)
    if (!p->st) {
        p->st = singws_stretch_new(mp_aframe_get_channels(in), mp_aframe_get_rate(in));
        if (!p->st) {
            MP_FATAL(f, "Signalsmith Stretch initialization failed.\n");
            talloc_free(in);
            goto error;
        }
        mp_aframe_config_copy(p->cur_format, in);
        singws_stretch_set_pitch(p->st, p->pitch);
    }

    int n = mp_aframe_get_size(in);
    double want = n / p->speed + p->carry;
    int m = (int)floor(want);
    p->carry = want - m;
    const float *in_data[MP_NUM_CHANNELS] = {0};
    uint8_t **iplanes = mp_aframe_get_data_ro(in);
    int num_planes = mp_aframe_get_planes(in);
    for (int c = 0; c < num_planes; c++)
        in_data[c] = (void *)iplanes[c];

    double pts = mp_aframe_get_pts(in);
    double pts_end = MP_NOPTS_VALUE;
    if (pts != MP_NOPTS_VALUE)
        pts_end = pts + n / (double)mp_aframe_get_effective_rate(in);

    if (m < 1) {
        // Tiny frame at very high speed: still feed the input so no audio is skipped.
        float dummy[MP_NUM_CHANNELS];
        float *odummy[MP_NUM_CHANNELS];
        for (int c = 0; c < num_planes; c++)
            odummy[c] = &dummy[c];
        singws_stretch_process(p->st, in_data, n, odummy, 0);
        p->delay += n;
        talloc_free(in);
        mp_filter_internal_mark_progress(f);
        return;
    }
    bool ok = emit(f, in, m, pts_end, in_data, n, false);
    talloc_free(in);
    if (!ok)
        goto error;
    return;
error:
    mp_filter_internal_mark_failed(f);
}

static bool af_signalsmith_command(struct mp_filter *f, struct mp_filter_command *cmd)
{
    struct priv *p = f->priv;

    switch (cmd->type) {
    case MP_FILTER_COMMAND_TEXT: {
        char *endptr = NULL;
        double pitch = p->pitch;
        if (!strcmp(cmd->cmd, "set-pitch")) {
            pitch = strtod(cmd->arg, &endptr);
            if (*endptr)
                return false;
            return update_pitch(p, pitch);
        } else if (!strcmp(cmd->cmd, "multiply-pitch")) {
            double mult = strtod(cmd->arg, &endptr);
            if (*endptr || mult <= 0)
                return false;
            pitch *= mult;
            return update_pitch(p, pitch);
        }
        return false;
    }
    case MP_FILTER_COMMAND_SET_SPEED:
        if (cmd->speed > 0.01)
            p->speed = cmd->speed;
        return true;
    }
    return false;
}

static void af_signalsmith_reset(struct mp_filter *f)
{
    struct priv *p = f->priv;
    if (p->st)
        singws_stretch_reset(p->st);
    p->carry = 0;
    p->delay = 0;
    p->flushed = false;
}

static void af_signalsmith_destroy(struct mp_filter *f)
{
    drop_state(f->priv);
}

static const struct mp_filter_info af_signalsmith_filter = {
    .name = "signalsmith",
    .priv_size = sizeof(struct priv),
    .process = af_signalsmith_process,
    .command = af_signalsmith_command,
    .reset = af_signalsmith_reset,
    .destroy = af_signalsmith_destroy,
};

static struct mp_filter *af_signalsmith_create(struct mp_filter *parent, void *options)
{
    struct mp_filter *f = mp_filter_create(parent, &af_signalsmith_filter);
    if (!f) {
        talloc_free(options);
        return NULL;
    }
    mp_filter_add_pin(f, MP_PIN_IN, "in");
    mp_filter_add_pin(f, MP_PIN_OUT, "out");

    struct priv *p = f->priv;
    p->opts = talloc_steal(p, options);
    p->speed = 1.0;
    p->pitch = p->opts->scale;
    p->cur_format = talloc_steal(p, mp_aframe_create());
    p->out_pool = mp_aframe_pool_create(p);

    struct mp_autoconvert *conv = mp_autoconvert_create(f);
    if (!conv)
        abort();
    mp_autoconvert_add_afmt(conv, AF_FORMAT_FLOATP);
    mp_pin_connect(conv->f->pins[0], f->ppins[0]);
    p->in_pin = conv->f->pins[1];
    return f;
}

#define OPT_BASE_STRUCT struct f_opts

const struct mp_user_filter_entry af_signalsmith = {
    .desc = {
        .description = "Pitch/tempo conversion with Signalsmith Stretch",
        .name = "signalsmith",
        .priv_size = sizeof(OPT_BASE_STRUCT),
        .priv_defaults = &(const OPT_BASE_STRUCT) {
            .scale = 1.0,
        },
        .options = (const struct m_option[]) {
            {"pitch-scale", OPT_DOUBLE(scale), M_RANGE(0.01, 100)},
            {0}
        },
    },
    .create = af_signalsmith_create,
};
