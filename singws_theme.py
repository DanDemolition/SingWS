"""Central design system tokens and QSS components for SingWS."""

VISUAL = {
    "canvas": "#030710",
    "surface": "#07101B",
    "surface_alt": "#0A1422",
    "surface_deep": "#02060D",
    "surface_elevated": "#0E1A2C",
    "surface_input": "#050B14",
    "surface_input_focus": "#0B1626",
    "deck": "#050B16",
    "deck_elevated": "#0D182A",
    "border": "#18263A",
    "border_strong": "#273B58",
    "border_focus": "#8D5CFF",
    "text": "#E6E8F0",
    "text_muted": "#B4B8C6",
    "text_soft": "#7F8496",
    "text_bright": "#FFFFFF",
    "accent": "#6D28FF",
    "accent_bright": "#A020FF",
    "accent_dim": "#2E1065",
    "accent_text": "#FFFFFF",
    "success": "#00F060",
    "warning": "#F5C84B",
    "danger": "#FF5F7A",
    "now_singing": "#8DFFB1",
    "ticker": "#F7D046",
}

SPACING = {
    "xxs": 2,
    "xs": 4,
    "sm": 6,
    "md": 8,
    "lg": 12,
    "xl": 16,
    "xxl": 20,
    "section": 24,
}

RADIUS = {
    "none": 0,
    "xs": 4,
    "sm": 6,
    "md": 8,
    "lg": 8,
    "pill": 999,
}

TYPOGRAPHY = {
    "caption": {"size": 11, "weight": 650},
    "label": {"size": 12, "weight": 700},
    "body": {"size": 13, "weight": 650},
    "body_strong": {"size": 13, "weight": 750},
    "title": {"size": 14, "weight": 850},
    "display": {"size": 24, "weight": 850},
}

ELEVATION = {
    "flat": "rgba(255,255,255,0.012)",
    "raised": "rgba(255,255,255,0.024)",
    "deck": "rgba(109,40,255,0.20)",
    "border_subtle": "rgba(115,144,180,0.14)",
    "border": "rgba(115,144,180,0.16)",
    "border_accent": "rgba(109,40,255,0.30)",
    "border_accent_strong": "rgba(160,32,255,0.42)",
}


def color(name: str, default: str = "") -> str:
    return str(VISUAL.get(name, default))


def space(name: str) -> int:
    return int(SPACING[name])


def radius(name: str = "md") -> int:
    return int(RADIUS[name])


def clamp_radius(value: int) -> int:
    return min(radius("lg"), max(radius("none"), int(value)))


def font_rule(token: str = "body", *, color_name: str = "text") -> str:
    spec = TYPOGRAPHY.get(token, TYPOGRAPHY["body"])
    return f"color:{color(color_name)}; font-size:{spec['size']}px; font-weight:{spec['weight']}; letter-spacing:0px;"


def section_title_css() -> str:
    return f"color:{color('text_bright')}; font-size:13px; font-weight:850; letter-spacing:0px;"


def section_meta_css() -> str:
    return f"color:{color('text_soft')}; font-size:11px; font-weight:650; letter-spacing:0px;"


def panel_frame_css(object_name: str, radius_px: int = 8, border_alpha: float = 0.08) -> str:
    r = clamp_radius(radius_px)
    alpha = min(max(float(border_alpha), 0.10), 0.22)
    return f"""
        QFrame#{object_name} {{
            background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 rgba(9,20,34,0.98),
                stop:0.55 rgba(5,12,22,0.99),
                stop:1 rgba(2,6,13,0.99));
            border-radius: {r}px;
            border: 1px solid rgba(115,144,180,{alpha:.3f});
        }}
    """


def card_css(object_name: str, radius_px: int = 8) -> str:
    r = clamp_radius(radius_px)
    return f"""
        QFrame#{object_name} {{
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 rgba(8,17,29,0.98),
                stop:1 rgba(2,7,14,0.99));
            border-radius: {r}px;
            border: 1px solid rgba(115,144,180,0.14);
        }}
    """


def deck_css(object_name: str, radius_px: int = 8, accent: bool = True) -> str:
    r = clamp_radius(radius_px)
    border = ELEVATION["border_accent_strong"] if accent else ELEVATION["border"]
    glow = "rgba(109,40,255,0.15)" if accent else "rgba(255,255,255,0.010)"
    return f"""
        QFrame#{object_name}, QWidget#{object_name} {{
            background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 {glow},
                stop:0.42 rgba(8,17,30,0.98),
                stop:1 rgba(3,8,16,0.99));
            border: 1px solid {border};
            border-radius: {r}px;
        }}
    """


def header_bar_css(object_name: str) -> str:
    return f"""
        QFrame#{object_name} {{
            background: transparent;
            border: none;
            border-radius: {radius('md')}px;
        }}
    """


def workspace_shell_css(object_name: str, *, accent: bool = False) -> str:
    border = ELEVATION["border_accent"] if accent else "rgba(115,144,180,0.22)"
    return f"""
        QFrame#{object_name} {{
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 rgba(9,20,34,0.98),
                stop:0.50 rgba(5,13,23,0.99),
                stop:1 rgba(2,7,14,0.99));
            border: 1px solid {border};
            border-radius: {radius('md')}px;
        }}
    """


def nav_switch_css(object_name: str) -> str:
    return f"""
        QFrame#{object_name} {{
            background: rgba(2,6,13,0.82);
            border: 1px solid rgba(115,144,180,0.16);
            border-radius: {radius('md')}px;
        }}
    """


def status_pill_css(object_name: str = "", *, accent: bool = False) -> str:
    selector = f"QFrame#{object_name}" if object_name else "QFrame"
    border = "rgba(109,40,255,0.34)" if accent else "rgba(115,144,180,0.16)"
    return f"""
        {selector} {{
            background: rgba(2,6,13,0.66);
            border: 1px solid {border};
            border-radius: {radius('md')}px;
        }}
    """


def tab_button_css(*, active: bool = False) -> str:
    if active:
        return button_css(padding="6px 12px", radius_px=radius("md"), variant="primary").replace(
            "font-weight: 650;", "font-weight: 850;"
        ).replace(
            "border: 1px solid rgba(199,180,255,0.72);",
            "border: 1px solid rgba(199,180,255,0.72);"
        )
    return f"""
        QPushButton {{
            background: transparent;
            color: {color('text_soft')};
            border: 1px solid transparent;
            border-radius: {radius('md')}px;
            padding: 6px 12px;
            font-size: 12px;
            font-weight: 750;
        }}
        QPushButton:hover {{
            background: rgba(124,61,255,0.12);
            color: {color('text_bright')};
            border-color: rgba(124,61,255,0.30);
        }}
    """


def performance_deck_css(object_name: str) -> str:
    return deck_css(object_name, accent=True)


def utility_panel_css(object_name: str) -> str:
    return f"""
        QWidget#{object_name}, QFrame#{object_name} {{
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 rgba(255,255,255,0.018),
                stop:1 rgba(255,255,255,0.006));
            border: none;
        }}
    """


def control_grid_css(object_name: str) -> str:
    return f"""
        QWidget#{object_name}, QFrame#{object_name} {{
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 rgba(9,16,28,0.70),
                stop:1 rgba(3,8,15,0.72));
            border: 1px solid rgba(115,144,180,0.12);
            border-radius: {radius('md')}px;
        }}
    """


def bottom_nav_css(object_name: str) -> str:
    return f"""
        QFrame#{object_name} {{
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 rgba(10,20,34,0.92),
                stop:1 rgba(2,7,14,0.97));
            border: 1px solid rgba(115,144,180,0.10);
            border-radius: 16px;
        }}
    """


def nav_item_css(*, active: bool = False) -> str:
    # Icon-above-label nav cell. Each button holds two child QLabels:
    #   QLabel#navIcon  (large glyph) and QLabel#navLabel (small caption).
    # Child labels are styled via descendant selectors so a single
    # setStyleSheet() on the button recolors its contents.
    if active:
        bg = "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #8B43FF, stop:1 #6D28FF)"
        border = "rgba(192,156,255,0.55)"
        hover_bg = bg
        icon_color = color("text_bright")
        label_color = color("text_bright")
    else:
        bg = "transparent"
        border = "transparent"
        hover_bg = "rgba(109,40,255,0.14)"
        icon_color = color("text_soft")
        label_color = color("text_soft")
    return f"""
        QPushButton {{
            background: {bg};
            border: 1px solid {border};
            border-radius: 14px;
            padding: 3px 4px 4px 4px;
        }}
        QPushButton:hover {{
            background: {hover_bg};
            border-color: rgba(160,32,255,0.42);
        }}
        QPushButton:pressed {{
            background: rgba(46,16,101,0.85);
        }}
        QPushButton QLabel {{
            background: transparent;
            border: none;
        }}
        QPushButton QLabel#navIcon {{
            color: {icon_color};
            font-size: 18px;
            font-weight: 600;
        }}
        QPushButton QLabel#navLabel {{
            color: {label_color};
            font-size: 11px;
            font-weight: 750;
        }}
        QPushButton:hover QLabel#navIcon, QPushButton:hover QLabel#navLabel {{
            color: {color('text_bright')};
        }}
    """


def value_chip_css() -> str:
    return f"""
        QLabel {{
            color: {color('text_bright')};
            background-color: rgba(255,255,255,0.032);
            border: 1px solid rgba(115,144,180,0.13);
            border-radius: {radius('md')}px;
            font-size: 13px;
            font-weight: 800;
        }}
    """


def action_button_css(*, danger: bool = False) -> str:
    css = button_css(padding="8px 12px", radius_px=radius("md"), variant="subtle")
    if danger:
        css += f"""
            QPushButton {{
                color: {color('danger')};
                border-color: rgba(255,95,122,0.35);
            }}
            QPushButton:hover {{
                background: rgba(255,95,122,0.14);
                border-color: rgba(255,95,122,0.50);
            }}
        """
    else:
        css += f"""
            QPushButton {{
                color: {color('text_muted')};
                font-size: 13px;
                font-weight: 720;
            }}
        """
    return css


def button_css(*, padding: str = "6px 12px", radius_px: int = 8, variant: str = "default") -> str:
    r = clamp_radius(radius_px)
    if variant == "primary":
        background = f"qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {color('accent_bright')}, stop:1 {color('accent')})"
        border = "rgba(199,180,255,0.72)"
        text = color("accent_text")
        hover = color("accent_bright")
    else:
        background = "rgba(255,255,255,0.024)" if variant == "default" else "rgba(255,255,255,0.020)"
        border = "rgba(115,144,180,0.14)" if variant == "default" else "rgba(115,144,180,0.12)"
        text = color("text")
        hover = "rgba(124,61,255,0.12)"
    return f"""
        QPushButton, QToolButton {{
            background: {background};
            color: {text};
            border: 1px solid {border};
            border-radius: {r}px;
            padding: {padding};
            font-weight: 650;
            font-size: 13px;
        }}
        QPushButton:hover, QToolButton:hover {{
            background: {hover};
            border-color: rgba(167,139,250,0.38);
            color: {color('text_bright')};
        }}
        QPushButton:pressed, QToolButton:pressed {{
            background: rgba(46,16,101,0.74);
            border-color: rgba(167,139,250,0.48);
            color: {color('accent_text')};
        }}
        QPushButton:disabled, QToolButton:disabled {{
            color: {color('text_soft')};
            background: {color('surface')};
            border-color: {color('border')};
        }}
    """


def input_css(*, padding: str = "10px 13px", radius_px: int = 8) -> str:
    r = clamp_radius(radius_px)
    return f"""
        QLineEdit, QTextEdit, QPlainTextEdit {{
            background-color: {color('surface_input')};
            color: {color('text_bright')};
            border: 1px solid rgba(255,255,255,0.040);
            border-radius: {r}px;
            padding: {padding};
            font-size: 13px;
            font-weight: 650;
            selection-background-color: {color('accent')};
            selection-color: {color('accent_text')};
        }}
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
            border: 1px solid {color('border_focus')};
            background-color: {color('surface_input_focus')};
        }}
        QLineEdit#librarySearchInput {{
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 rgba(4,9,17,0.98),
                stop:1 rgba(8,15,27,0.98));
            border: 1px solid rgba(115,144,180,0.18);
            border-radius: {r}px;
            padding: 9px 13px;
            font-size: 13px;
            font-weight: 650;
        }}
        QLineEdit#librarySearchInput:focus {{
            border: 1px solid rgba(167,139,250,0.78);
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 rgba(16,24,39,1.0),
                stop:1 rgba(21,28,45,1.0));
        }}
    """


def combo_css(*, radius_px: int = 8) -> str:
    r = clamp_radius(radius_px)
    return f"""
        QComboBox {{
            background-color: rgba(5,11,20,0.98);
            color: {color('text_bright')};
            border: 1px solid rgba(115,144,180,0.18);
            border-radius: {r}px;
            padding: 5px 9px;
            font-size: 12px;
            font-weight: 650;
            min-height: 24px;
        }}
        QComboBox:hover {{
            background-color: rgba(255,255,255,0.045);
            border-color: rgba(167,139,250,0.32);
        }}
        QComboBox:focus {{
            border: 1px solid {color('border_focus')};
            background-color: {color('surface_input_focus')};
        }}
        QComboBox QAbstractItemView {{
            background-color: {color('surface_deep')};
            color: {color('text_bright')};
            border: 1px solid {color('border')};
            selection-background-color: {color('accent')};
        }}
    """


def checkbox_css(*, size: int = 13) -> str:
    return f"""
        QCheckBox {{
            color: {color('text_soft')};
            spacing: 5px;
            font-size: 12px;
            font-weight: 650;
            background: transparent;
        }}
        QCheckBox::indicator {{
            width: {size}px;
            height: {size}px;
            border-radius: 4px;
            border: 1px solid rgba(115,144,180,0.22);
            background: rgba(255,255,255,0.030);
        }}
        QCheckBox::indicator:hover {{
            border-color: rgba(167,139,250,0.48);
            background: rgba(124,61,255,0.14);
        }}
        QCheckBox::indicator:checked {{
            border-color: rgba(167,139,250,0.70);
            background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 rgba(160,32,255,0.82),
                stop:1 rgba(109,40,255,0.78));
        }}
    """


def scroll_bar_css() -> str:
    return f"""
        QScrollBar:vertical {{
            background: {color('surface_deep')};
            width: 10px;
            margin: 0px;
            border: none;
        }}
        QScrollBar::handle:vertical {{
            background: rgba(255,255,255,0.12);
            min-height: 28px;
            border-radius: 5px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {color('accent_bright')};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            height: 0px;
            background: transparent;
        }}
        QScrollBar:horizontal {{
            background: {color('surface_deep')};
            height: 10px;
            margin: 0px;
            border: none;
        }}
        QScrollBar::handle:horizontal {{
            background: rgba(255,255,255,0.12);
            min-width: 28px;
            border-radius: 5px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {color('accent_bright')};
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
            width: 0px;
            background: transparent;
        }}
    """


def list_css() -> str:
    return f"""
        QListWidget, QListView, QTreeView, QTableView {{
            background-color: rgba(2,7,14,0.92);
            alternate-background-color: rgba(255,255,255,0.010);
            color: {color('text')};
            selection-background-color: {color('accent')};
            selection-color: {color('accent_text')};
            border: 1px solid rgba(115,144,180,0.12);
            border-radius: 8px;
            outline: none;
            padding: 2px;
            show-decoration-selected: 1;
        }}
        QListWidget::item, QListView::item, QTreeView::item {{
            padding: 11px 12px;
            border: none;
            border-radius: 6px;
        }}
        QListWidget::item:hover, QListView::item:hover, QTreeView::item:hover {{
            background-color: rgba(124,61,255,0.075);
            border: 1px solid rgba(167,139,250,0.14);
        }}
        QListWidget::item:selected, QListView::item:selected, QTreeView::item:selected {{
            background-color: rgba(109,40,255,0.50);
            color: {color('accent_text')};
            border: 1px solid rgba(167,139,250,0.26);
        }}
        QListWidget::item:selected:!active, QListView::item:selected:!active, QTreeView::item:selected:!active {{
            background-color: rgba(109,40,255,0.36);
            color: {color('accent_text')};
            border: 1px solid rgba(167,139,250,0.20);
        }}
        QListWidget::item:selected:active, QListView::item:selected:active, QTreeView::item:selected:active {{
            background-color: rgba(109,40,255,0.54);
            color: {color('accent_text')};
        }}
        QListWidget::drop-indicator, QListView::drop-indicator, QTreeView::drop-indicator {{
            height: 2px;
            background: {color('accent_bright')};
            border: none;
        }}
        QHeaderView::section {{
            background-color: {color('surface_alt')};
            color: {color('text_muted')};
            border: none;
            border-bottom: 1px solid rgba(255,255,255,0.035);
            padding: 7px 9px;
            font-weight: 750;
        }}
        {scroll_bar_css()}
    """


def slider_css(*, deck: bool = False) -> str:
    if deck:
        groove_h = 7
        handle = 16
        margin = -5
        border = "2px"
        bg = "rgba(3,8,15,0.88)"
    else:
        groove_h = 7
        handle = 14
        margin = -4
        border = "1px"
        bg = color("surface_input")
    return f"""
        QSlider {{
            min-height: {22 if deck else 0}px;
        }}
        QSlider::groove:horizontal {{
            height: {groove_h}px;
            background: {bg};
            border: 1px solid rgba(115,144,180,0.12);
            border-radius: {max(4, groove_h // 2)}px;
        }}
        QSlider::sub-page:horizontal {{
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 {color('accent')},
                stop:1 {color('accent_bright')});
            border-radius: {max(4, groove_h // 2)}px;
        }}
        QSlider::handle:horizontal {{
            width: {handle}px;
            height: {handle}px;
            margin: {margin}px 0;
            background: {color('accent_bright')};
            border: {border} solid rgba(255,255,255,0.78);
            border-radius: {handle // 2}px;
        }}
        QSlider::handle:horizontal:hover {{
            background: {color('text_bright')};
            border-color: {color('text_bright')};
        }}
    """


def menu_css() -> str:
    return f"""
        QMenu {{
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 rgba(18,27,47,0.98),
                stop:1 rgba(7,11,22,0.99));
            color: {color('text')};
            border: 1px solid rgba(124,61,255,0.30);
            border-radius: 8px;
            padding: 6px;
            font-size: 12px;
            font-weight: 650;
        }}
        QMenu::item {{
            min-height: 24px;
            padding: 6px 26px 6px 12px;
            border-radius: 6px;
            background: transparent;
        }}
        QMenu::item:selected {{
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 rgba(124,61,255,0.72),
                stop:1 rgba(80,42,164,0.68));
            color: {color('accent_text')};
        }}
        QMenu::item:disabled {{
            color: {color('text_soft')};
        }}
        QMenu::separator {{
            height: 1px;
            margin: 6px 8px;
            background: rgba(255,255,255,0.055);
        }}
        QMenu::indicator {{
            width: 14px;
            height: 14px;
            left: 7px;
        }}
    """


def tooltip_css() -> str:
    return f"""
        QToolTip {{
            color: {color('text')};
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 rgba(18,27,47,0.98),
                stop:1 rgba(7,11,22,0.99));
            border: 1px solid rgba(124,61,255,0.30);
            border-radius: {radius('md')}px;
            padding: 6px 8px;
            font-size: 12px;
            font-weight: 650;
        }}
    """
