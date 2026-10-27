// Qt Quick image provider "cdg": QML asks for image://cdg/<milliseconds> and gets the CDG picture at that moment,
// drawn by the Rust decoder (singws-cdg). Rust owns the data; this file only hands pixels to Qt.
#include <QImage>
#include <QQmlEngine>
#include <QQuickImageProvider>

extern "C" int singws_cdg_demo_frame(int ms, unsigned char *rgba300x216);   // defined in src/cdg_demo.rs

namespace {
class CdgImageProvider : public QQuickImageProvider {
public:
    CdgImageProvider() : QQuickImageProvider(QQuickImageProvider::Image) {}
    QImage requestImage(const QString &id, QSize *size, const QSize &) override {
        bool ok = false;
        const int ms = id.section('/', 0, 0).toInt(&ok);
        QImage img(300, 216, QImage::Format_RGBA8888);   // 1200 bytes per line: no padding, so bits() is one flat buffer
        if (!ok || !singws_cdg_demo_frame(ms, img.bits())) img.fill(Qt::transparent);
        if (size) *size = img.size();
        return img;
    }
};
}

extern "C" void singws_cdg_install_provider(QQmlEngine *engine) {
    engine->addImageProvider(QStringLiteral("cdg"), new CdgImageProvider);
}
