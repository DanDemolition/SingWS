// `--play-demo` (second no-Python host spike): one plain window stands in for the audience output. The host first makes sure its native view exists and the
// window is SHOWN (the bridge silently draws nothing for a hidden view), then starts the song; the Rust side runs the protocol from a 10 ms timer.
import QtQuick
import QtQuick.Window
import com.singws.shell

Window {
    id: outWin
    width: 640; height: 400; x: 80; y: 80; visible: true; title: "SingWS play-demo output"; color: "#000000"
    property int tries: 0
    property bool started: false
    PlayDemo { id: demo }
    // the bridge needs BOTH the output and the preview view (as the app has); with one it runs headless
    Window { id: prevWin; width: 320; height: 200; x: 740; y: 80; visible: true; title: "SingWS play-demo preview"; color: "#000000" }

    Timer {
        interval: 100; running: true; repeat: true
        onTriggered: {
            if (outWin.started) return
            outWin.tries++
            if (demo.prepare()) {
                outWin.started = true
                if (demo.start()) ticker.start(); else { demo.finish(false); Qt.exit(2) }
            } else if (outWin.tries > 50) {
                demo.finish(false); Qt.exit(2)      // the window never became ready: a clear failure, not a silent black picture
            }
        }
    }
    Timer {
        id: ticker; interval: 10; repeat: true
        onTriggered: {
            demo.tick()
            if (demo.done) { ticker.stop(); demo.finish(true); Qt.quit() }
        }
    }
    Timer { interval: 1200000; running: true; onTriggered: { demo.finish(false); Qt.exit(3) } }   // watchdog: never hang
}
