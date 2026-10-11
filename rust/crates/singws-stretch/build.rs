// Compiles the same C++ wrapper the mpv `signalsmith` audio filter uses (native/mpv_runtime/signalsmith/singws_stretch.cpp) around the
// vendored MIT header, so there is one implementation of key/tempo for karaoke whichever path plays it.
fn main() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let wrapper = root.join("native/mpv_runtime/signalsmith/singws_stretch.cpp");
    println!("cargo:rerun-if-changed={}", wrapper.display());
    println!("cargo:rerun-if-changed={}", root.join("vendor/signalsmith-stretch/signalsmith-stretch.h").display());
    cc::Build::new()
        .cpp(true)
        .std("c++14")
        .file(&wrapper)
        .include(root.join("vendor/signalsmith-stretch"))
        .include(root.join("vendor"))
        .opt_level(2)
        .warnings(false)
        .compile("singws_stretch_cpp");
}
