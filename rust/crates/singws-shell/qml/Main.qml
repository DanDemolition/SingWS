import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import com.singws.shell

ApplicationWindow {
    id: win
    width: 980
    height: 720
    visible: true
    title: "SingWS (Rust shell, read-only)"
    color: "#0b0a16"

    // Test hooks: `--tab library|history`, per-page options (--query, --singer-search, --singer-key, --song-query) and
    // `--snapshot FILE.png` (save the window after the first results, then quit).
    function arg(name) {
        var a = Qt.application.arguments
        for (var i = 0; i < a.length - 1; i++) if (a[i] === name) return a[i + 1]
        return ""
    }

    Rectangle {
        id: canvas
        anchors.fill: parent
        color: "#0b0a16"

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 12
            spacing: 10

            TabBar {
                id: tabs
                Layout.fillWidth: true
                background: Rectangle { color: "transparent" }
                currentIndex: win.arg("--tab") === "history" ? 1 : 0
                Repeater {
                    model: ["Library", "Singer history"]
                    TabButton {
                        text: modelData
                        font.pixelSize: 13
                        contentItem: Label { text: parent.text; color: parent.checked ? "#ffffff" : "#9a97c0"; font: parent.font; horizontalAlignment: Text.AlignHCenter }
                        background: Rectangle { color: parent.checked ? "#4b2bb0" : "#12102a"; radius: 6 }
                    }
                }
            }

            StackLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: tabs.currentIndex
                LibraryPage {}
                HistoryPage {}
            }
        }
    }

    Timer {
        interval: 1200
        running: win.arg("--snapshot") !== ""
        onTriggered: canvas.grabToImage(function(r) { r.saveToFile(win.arg("--snapshot")); Qt.quit() })
    }
}
