// The attached screens as the app's window rules see them (QApplication.screens(), primaryScreen(), screenAt(QCursor.pos())): geometry, AVAILABLE geometry
// (without menu bar and dock), device pixel ratio and the cursor position, as JSON for the Rust placement rules (singws_cdg::window_rules).
#include <QCursor>
#include <QGuiApplication>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QScreen>

extern "C" char *singws_screens_json_live()   // malloc'ed, NUL-terminated; the caller frees it with singws_screens_json_free
{
    QJsonArray screens;
    const auto list = QGuiApplication::screens();
    int primary = -1;
    for (int i = 0; i < list.size(); ++i) {
        QScreen *s = list[i];
        if (s == QGuiApplication::primaryScreen()) primary = i;
        const QRect g = s->geometry(), a = s->availableGeometry();
        screens.append(QJsonObject{{"name", s->name()}, {"dpr", s->devicePixelRatio()},
                                   {"x", g.x()}, {"y", g.y()}, {"w", g.width()}, {"h", g.height()},
                                   {"ax", a.x()}, {"ay", a.y()}, {"aw", a.width()}, {"ah", a.height()}});
    }
    const QPoint c = QCursor::pos();
    int under = -1;
    if (QScreen *s = QGuiApplication::screenAt(c)) under = list.indexOf(s);
    const QJsonObject o{{"screens", screens}, {"primary", primary}, {"cursor_screen", under}, {"cursor", QJsonArray{c.x(), c.y()}}};
    const QByteArray bytes = QJsonDocument(o).toJson(QJsonDocument::Compact);
    char *out = static_cast<char *>(malloc(bytes.size() + 1));
    memcpy(out, bytes.constData(), bytes.size() + 1);
    return out;
}

extern "C" void singws_screens_json_free(char *p) { free(p); }
