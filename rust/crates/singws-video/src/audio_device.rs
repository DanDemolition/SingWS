//! Resolve the host's saved output label to an mpv `audio-device` name (port of `mpvAudioDeviceForLabel`).
use unicode_normalization::UnicodeNormalization;

/// Case, diacritics and punctuation insensitive key: "Built-in Output" and "built in output" match.
pub fn normalized_label(value: &str) -> String {
    value
        .nfd()
        .filter(|c| !('\u{0300}'..='\u{036f}').contains(c))
        .flat_map(|c| c.to_lowercase())
        .filter(|c| c.is_alphanumeric())
        .collect()
}

/// `devices` = (name, description) pairs from `audio-device-list`. `None` means "no safe match": the caller must NOT fall back to
/// the system default (it can be the AirPlay TV).
pub fn device_for_label(devices: &[(String, String)], requested: &str) -> Option<String> {
    if requested.is_empty() || requested.eq_ignore_ascii_case("auto") {
        return Some("auto".to_string());
    }
    let wanted = normalized_label(requested);
    let mut partial: Vec<&str> = Vec::new();
    for (name, description) in devices {
        if name.is_empty() {
            continue;
        }
        let name_key = normalized_label(name);
        let description_key = normalized_label(description);
        if name.to_lowercase() == requested.to_lowercase()
            || description.to_lowercase() == requested.to_lowercase()
            || name_key == wanted
            || description_key == wanted
        {
            return Some(name.clone());
        }
        if !wanted.is_empty()
            && ((!description_key.is_empty() && (description_key.contains(&wanted) || wanted.contains(&description_key)))
                || (!name_key.is_empty() && (name_key.contains(&wanted) || wanted.contains(&name_key))))
        {
            partial.push(name);
        }
    }
    if partial.len() == 1 {
        Some(partial[0].to_string())
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn devs() -> Vec<(String, String)> {
        vec![
            ("auto".into(), "Autoselect device".into()),
            ("coreaudio/BuiltInSpeakerDevice".into(), "MacBook Pro Speakers".into()),
            ("coreaudio/AppleUSBAudioEngine:Focusrite:Scarlett:123".into(), "Scarlett 2i2 USB".into()),
            ("coreaudio/HDMI1".into(), "HDMI Splitter".into()),
        ]
    }
    #[test]
    fn auto_and_empty_mean_auto() {
        assert_eq!(device_for_label(&devs(), ""), Some("auto".into()));
        assert_eq!(device_for_label(&devs(), "AUTO"), Some("auto".into()));
    }
    #[test]
    fn exact_description_wins_regardless_of_case_and_punctuation() {
        assert_eq!(device_for_label(&devs(), "macbook pro speakers"), Some("coreaudio/BuiltInSpeakerDevice".into()));
        assert_eq!(device_for_label(&devs(), "Scarlett-2i2 usb"), Some("coreaudio/AppleUSBAudioEngine:Focusrite:Scarlett:123".into()));
    }
    #[test]
    fn a_single_partial_match_is_used_but_ambiguity_is_refused() {
        assert_eq!(device_for_label(&devs(), "Scarlett"), Some("coreaudio/AppleUSBAudioEngine:Focusrite:Scarlett:123".into()));
        let mut d = devs();
        d.push(("coreaudio/Other".into(), "Scarlett Solo".into()));
        assert_eq!(device_for_label(&d, "Scarlett"), None);
    }
    #[test]
    fn unknown_output_is_refused_never_defaulted() {
        assert_eq!(device_for_label(&devs(), "External Headphones"), None);
    }
    #[test]
    fn diacritics_are_folded() {
        let d = vec![("coreaudio/X".to_string(), "Café Speakers".to_string())];
        assert_eq!(device_for_label(&d, "cafe speakers"), Some("coreaudio/X".into()));
    }
}
