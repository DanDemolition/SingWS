from pathlib import Path

from setuptools import Extension, setup


ROOT = Path(__file__).resolve().parents[1]

extension = Extension(
    "signalsmith_audio_native",
    sources=[str(ROOT / "native" / "signalsmith_audio_native.cpp")],
    include_dirs=[
        str(ROOT / "vendor" / "pybind11" / "include"),
        str(ROOT / "vendor" / "signalsmith-stretch"),
        str(ROOT / "vendor" / "signalsmith-linear" / "include"),
    ],
    language="c++",
    extra_compile_args=["-std=c++17", "-O3"],
)

setup(
    name="signalsmith-audio-native",
    version="0.1.0",
    ext_modules=[extension],
)
