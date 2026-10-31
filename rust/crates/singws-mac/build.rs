// Builds the thin Objective-C shim (macOS only), the same way singws-video does. Frameworks are system ones; nothing is bundled.
fn main() {
    println!("cargo:rerun-if-changed=shim/mac_shim.m");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        cc::Build::new().file("shim/mac_shim.m").flag("-fobjc-arc").flag("-fmodules").warnings(false).compile("singws_mac_shim");
        for fw in ["Foundation", "AppKit", "ApplicationServices", "CoreLocation"] {
            println!("cargo:rustc-link-lib=framework={fw}");
        }
    }
}
