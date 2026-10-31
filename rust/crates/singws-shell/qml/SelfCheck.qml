// `--selfcheck` (rust/spike_host.sh): two plain Qt Quick windows stand in for the audience output and the host preview. Their native ids (NSViews on macOS, looked up by title in Rust)
// go to the Rust video bridge exactly as the Python app hands over QWidget.winId(). Nothing here is part of the shell proper.
import QtQuick
import QtQuick.Window
import com.singws.shell

Window {
    id: outWin
    width: 640; height: 360; x: 80; y: 80; visible: true; title: "SingWS spike output"; color: "#202020"
    SelfCheck { id: check }

    Window {
        id: prevWin
        width: 320; height: 180; x: 740; y: 80; visible: true; title: "SingWS spike preview"; color: "#202020"
    }

    Timer {
        interval: 1500; running: true; repeat: false
        onTriggered: {
            check.start()
            refreshTimer.start()
            probeTimer.start()
        }
    }
    Timer { id: refreshTimer; interval: 1000; repeat: false; onTriggered: check.refresh() }
    Timer {
        id: probeTimer
        interval: 3500; repeat: false
        onTriggered: { check.probe(); doneTimer.start() }
    }
    Timer {
        id: doneTimer
        interval: 4500; repeat: false      // the BGM thread plays ~5 s from start()
        onTriggered: { check.finish(); Qt.quit() }
    }
    Timer { interval: 25000; running: true; onTriggered: { check.finish(); Qt.exit(3) } }   // watchdog: never hang
}
