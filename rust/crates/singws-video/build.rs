// Builds the thin Objective-C shim (macOS only). The deployment target comes from MACOSX_DEPLOYMENT_TARGET like the other native pieces
// (rust/build_video.sh sets it); frameworks are linked here so the dylib needs nothing at run time but the system.
fn main() {
    println!("cargo:rerun-if-changed=shim/video_shim.m");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        cc::Build::new().file("shim/video_shim.m").flag("-fobjc-arc").flag("-fmodules").warnings(false).compile("singws_video_shim");
        for fw in ["Cocoa", "AppKit", "Foundation", "OpenGL", "CoreGraphics"] {
            println!("cargo:rustc-link-lib=framework={fw}");
        }
    }
}
