import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import com.singws.shell

Item {
    id: page
    function arg(name) {
        var a = Qt.application.arguments
        for (var i = 0; i < a.length - 1; i++) if (a[i] === name) return a[i + 1]
        return ""
    }

    SettingsPanel { id: panel }
    property var control: null   // the shared ControlPanel (Main.qml); null when the page is used on its own
    property var cview: control ? JSON.parse(control.viewJson) : ({enabled: false})
    Connections { target: page.control; function onViewJsonChanged() { page.cview = JSON.parse(page.control.viewJson) } }
    // connected to the app: the few settings the app lets the shell change (KaraokeApp._SHELL_SETTINGS_SCHEMA) get a button; everything else stays read-only
    property bool live: cview.enabled === true
    readonly property var boolKeys: ["ticker_custom_enabled", "loudness_scan_holds_for_playback", "karaoke_auto_advance", "bg_end_silence_trim_enabled", "karaoke_bgm_crossfade_enabled", "ui_record_confirm", "rotation_estimate_include_all_songs"]
    function setSetting(label, key, value) { if (control) control.send(label, "settings.set", JSON.stringify({key: key, value: value}), false) }
    property var rows: JSON.parse(panel.rowsJson)
    Connections { target: panel; function onRowsJsonChanged() { page.rows = JSON.parse(panel.rowsJson) } }

    Component.onCompleted: { if (arg("--settings-query") !== "") field.text = arg("--settings-query"); panel.refresh(field.text) }
    Timer { id: debounce; interval: 120; onTriggered: panel.refresh(field.text) }
    Timer { interval: 5000; running: page.visible; repeat: true; onTriggered: panel.refresh(field.text) }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        Label { text: page.live ? "SETTINGS (a few can be changed; the rest are read-only)" : "SETTINGS (read-only)"; color: "#e8e6f5"; font.pixelSize: 15; font.weight: Font.DemiBold }

        TextField {
            id: field
            Layout.fillWidth: true
            placeholderText: "Search settings by name"
            color: "#e8e6f5"
            placeholderTextColor: "#7a77a0"
            font.pixelSize: 14
            selectByMouse: true
            background: Rectangle { color: "#12102a"; radius: 8; border.color: field.activeFocus ? "#7c3aed" : "#2a2650"; border.width: 1 }
            onTextChanged: debounce.restart()
            Accessible.name: "Search settings"
        }

        Label { Layout.fillWidth: true; text: panel.status; color: "#9a97c0"; font.pixelSize: 12; wrapMode: Text.Wrap }
        Rectangle {
            Layout.fillWidth: true
            visible: page.live && page.cview.result !== undefined && page.cview.result !== null
            height: visible ? resultText.implicitHeight + 12 : 0
            radius: 6
            color: !page.cview.result ? "transparent" : (page.cview.result.kind === "ok" ? "#12301f" : (page.cview.result.kind === "warn" ? "#33290f" : "#3a1414"))
            Label { id: resultText; anchors.fill: parent; anchors.margins: 6; wrapMode: Text.Wrap; color: "#ffffff"; font.pixelSize: 12; text: page.cview.result ? page.cview.result.text : "" }
            Accessible.role: Accessible.StaticText
            Accessible.name: page.cview.result ? page.cview.result.text : ""
        }

        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: page.rows
            spacing: 2
            ScrollBar.vertical: ScrollBar {}
            delegate: Rectangle {
                width: list.width
                height: 32
                radius: 6
                color: "#1a1838"
                Accessible.role: Accessible.ListItem
                Accessible.name: modelData.key + ", " + modelData.value
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 10
                    anchors.rightMargin: 10
                    Label { text: modelData.key; color: "#c9c6e8"; font.pixelSize: 13; elide: Text.ElideRight; Layout.preferredWidth: 320 }
                    Label { text: modelData.value; color: modelData.hidden ? "#f0b44c" : "#ffffff"; font.pixelSize: 13; font.italic: modelData.hidden; elide: Text.ElideRight; Layout.fillWidth: true }
                    Button {
                        visible: page.live && page.boolKeys.indexOf(modelData.key) >= 0 && (modelData.value === "true" || modelData.value === "false")
                        text: modelData.value === "true" ? "Turn off" : "Turn on"
                        implicitHeight: 26
                        onClicked: page.setSetting((modelData.value === "true" ? "Turn off " : "Turn on ") + modelData.key, modelData.key, modelData.value !== "true")
                        Accessible.name: (modelData.value === "true" ? "Turn off " : "Turn on ") + modelData.key
                    }
                    TextField {
                        id: thresholdField
                        visible: page.live && modelData.key === "end_silence_trim_threshold_sec"
                        implicitWidth: 64
                        implicitHeight: 26
                        text: modelData.value
                        validator: DoubleValidator { bottom: 0.5; top: 60.0; decimals: 2; notation: DoubleValidator.StandardNotation }
                        selectByMouse: true
                        Accessible.name: "Seconds of silence before the karaoke song ends early (0.5 to 60)"
                    }
                    TextField {
                        id: paddingField
                        visible: page.live && modelData.key === "rotation_padding_sec"
                        implicitWidth: 64
                        implicitHeight: 26
                        text: modelData.value
                        validator: IntValidator { bottom: 0; top: 600 }
                        selectByMouse: true
                        Accessible.name: "Seconds added per singer in the queue time estimate (0 to 600)"
                    }
                    Button {
                        visible: paddingField.visible
                        text: "Set"
                        implicitHeight: 26
                        enabled: paddingField.acceptableInput
                        onClicked: page.setSetting("Set " + modelData.key, modelData.key, parseInt(paddingField.text, 10))
                        Accessible.name: "Save the queue padding"
                    }
                    Button {
                        visible: thresholdField.visible
                        text: "Set"
                        implicitHeight: 26
                        enabled: thresholdField.acceptableInput
                        onClicked: page.setSetting("Set " + modelData.key, modelData.key, parseFloat(thresholdField.text))
                        Accessible.name: "Save the silence threshold"
                    }
                }
            }
        }
    }
}
