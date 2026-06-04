#include <algorithm>
#include <cmath>
#include <cstring>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

#include <pybind11/pybind11.h>

#include "signalsmith-stretch.h"

namespace py = pybind11;

class StretchEngine {
public:
    StretchEngine(int channels, int sample_rate)
        : channels_(channels), sample_rate_(sample_rate) {
        if (channels_ < 1 || channels_ > 8) {
            throw std::invalid_argument("channels must be between 1 and 8");
        }
        if (sample_rate_ < 8000 || sample_rate_ > 384000) {
            throw std::invalid_argument("sample_rate is out of range");
        }
        stretch_.presetDefault(channels_, sample_rate_, true);
        stretch_.setTransposeSemitones(0.0f);
    }

    int channels() const {
        return channels_;
    }

    int sample_rate() const {
        return sample_rate_;
    }

    int input_latency() const {
        return stretch_.inputLatency();
    }

    int output_latency() const {
        return stretch_.outputLatency();
    }

    void reset() {
        std::lock_guard<std::mutex> guard(mutex_);
        stretch_.reset();
        output_frame_remainder_ = 0.0;
    }

    void set_modifiers(double tempo_ratio, double semitones) {
        std::lock_guard<std::mutex> guard(mutex_);
        tempo_ratio_ = std::clamp(tempo_ratio, 0.5, 2.0);
        semitones_ = std::clamp(semitones, -24.0, 24.0);
        stretch_.setTransposeSemitones(static_cast<float>(semitones_));
    }

    double tempo_ratio() const {
        return tempo_ratio_;
    }

    double semitones() const {
        return semitones_;
    }

    py::bytes process_f32le(const py::bytes &input_bytes) {
        std::string input = input_bytes;
        std::string output;
        {
            py::gil_scoped_release release;
            output = process_locked(input);
        }
        return py::bytes(output);
    }

    py::bytes flush_f32le(int output_frames = -1) {
        std::string output;
        {
            py::gil_scoped_release release;
            std::lock_guard<std::mutex> guard(mutex_);
            int frames = output_frames > 0 ? output_frames : stretch_.outputLatency();
            frames = std::max(frames, 0);
            prepare_planar_buffers(0, frames);
            stretch_.flush(output_channels_, frames);
            output = interleave_output(frames);
        }
        return py::bytes(output);
    }

private:
    using Stretch = signalsmith::stretch::SignalsmithStretch<float>;

    std::string process_locked(const std::string &input) {
        std::lock_guard<std::mutex> guard(mutex_);
        const size_t frame_bytes = sizeof(float) * static_cast<size_t>(channels_);
        if (input.empty()) {
            return {};
        }
        if (input.size() % frame_bytes != 0) {
            throw std::invalid_argument("F32LE input must contain complete interleaved frames");
        }

        const int input_frames = static_cast<int>(input.size() / frame_bytes);
        const double exact_frames = static_cast<double>(input_frames) / tempo_ratio_ + output_frame_remainder_;
        int output_frames = static_cast<int>(std::floor(exact_frames));
        output_frame_remainder_ = exact_frames - static_cast<double>(output_frames);
        output_frames = std::max(output_frames, 1);

        prepare_planar_buffers(input_frames, output_frames);
        deinterleave_input(input, input_frames);
        stretch_.process(input_channels_, input_frames, output_channels_, output_frames);
        return interleave_output(output_frames);
    }

    void prepare_planar_buffers(int input_frames, int output_frames) {
        input_planar_.assign(static_cast<size_t>(channels_) * std::max(input_frames, 0), 0.0f);
        output_planar_.assign(static_cast<size_t>(channels_) * std::max(output_frames, 0), 0.0f);
        input_channels_.resize(channels_);
        output_channels_.resize(channels_);

        for (int channel = 0; channel < channels_; ++channel) {
            input_channels_[channel] = input_planar_.data() + static_cast<size_t>(channel) * std::max(input_frames, 0);
            output_channels_[channel] = output_planar_.data() + static_cast<size_t>(channel) * std::max(output_frames, 0);
        }
    }

    void deinterleave_input(const std::string &input, int input_frames) {
        const char *data = input.data();
        for (int frame = 0; frame < input_frames; ++frame) {
            for (int channel = 0; channel < channels_; ++channel) {
                float sample = 0.0f;
                const size_t offset = (static_cast<size_t>(frame) * channels_ + channel) * sizeof(float);
                std::memcpy(&sample, data + offset, sizeof(float));
                input_channels_[channel][frame] = sample;
            }
        }
    }

    std::string interleave_output(int output_frames) const {
        std::string output(static_cast<size_t>(output_frames) * channels_ * sizeof(float), '\0');
        for (int frame = 0; frame < output_frames; ++frame) {
            for (int channel = 0; channel < channels_; ++channel) {
                const float sample = output_channels_[channel][frame];
                const size_t offset = (static_cast<size_t>(frame) * channels_ + channel) * sizeof(float);
                std::memcpy(output.data() + offset, &sample, sizeof(float));
            }
        }
        return output;
    }

    int channels_;
    int sample_rate_;
    double tempo_ratio_ = 1.0;
    double semitones_ = 0.0;
    double output_frame_remainder_ = 0.0;
    Stretch stretch_;
    std::vector<float> input_planar_;
    std::vector<float> output_planar_;
    std::vector<float *> input_channels_;
    std::vector<float *> output_channels_;
    mutable std::mutex mutex_;
};

PYBIND11_MODULE(signalsmith_audio_native, module) {
    module.doc() = "Signalsmith Stretch PCM engine for SingWS";
    py::class_<StretchEngine>(module, "StretchEngine")
        .def(py::init<int, int>(), py::arg("channels"), py::arg("sample_rate"))
        .def_property_readonly("channels", &StretchEngine::channels)
        .def_property_readonly("sample_rate", &StretchEngine::sample_rate)
        .def_property_readonly("input_latency_frames", &StretchEngine::input_latency)
        .def_property_readonly("output_latency_frames", &StretchEngine::output_latency)
        .def_property_readonly("tempo_ratio", &StretchEngine::tempo_ratio)
        .def_property_readonly("semitones", &StretchEngine::semitones)
        .def("reset", &StretchEngine::reset)
        .def("set_modifiers", &StretchEngine::set_modifiers, py::arg("tempo_ratio"), py::arg("semitones"))
        .def("process_f32le", &StretchEngine::process_f32le, py::arg("input_bytes"))
        .def("flush_f32le", &StretchEngine::flush_f32le, py::arg("output_frames") = -1);
}
