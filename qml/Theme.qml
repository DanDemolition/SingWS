pragma Singleton
import QtQuick

QtObject {
    readonly property color canvas: "#030710"
    readonly property color panel: "#06101B"
    readonly property color panelAlt: "#091725"
    readonly property color panelDeep: "#02060D"
    readonly property color panelSoft: "#0D1B2B"
    readonly property color border: "#15263A"
    readonly property color borderSoft: "#102033"
    readonly property color borderStrong: "#25405F"
    readonly property color text: "#E6E8F0"
    readonly property color textMuted: "#B4B8C6"
    readonly property color textSoft: "#7F8496"
    readonly property color accent: "#6D28FF"
    readonly property color accentBright: "#9B2CFF"
    readonly property color accentSoft: "#271052"
    readonly property color rowAlt: Qt.rgba(0.42, 0.16, 1.0, 0.13)
    readonly property color success: "#00F060"
    readonly property color warning: "#F5C84B"
    readonly property color danger: "#FF3B5C"
    readonly property color lyricHot: "#FF8A00"

    readonly property int radius: 8
    readonly property int gap: 8
    readonly property int pad: 14
    readonly property int tightPad: 10

    function cardGradient() {
        return Qt.rgba(0.03, 0.08, 0.13, 0.98)
    }
}
