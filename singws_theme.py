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
    # the spinning record on the Show tab hero
    "record_ring_a": "#C084FC",
    "record_ring_b": "#34E08A",
    "record_ring_c": "#A855F7",
    "record_label_a": "#A855F7",
    "record_label_b": "#6D28D9",
    "record_label_c": "#2E1065",
    "record_name_a": "#1A1944",
    "record_name_b": "#0A0A1C",
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

# The scales every UI value should come from. tools/ui_token_audit.py counts how many literals in the app fall outside
# them, and test_ui_tokens_ratchet.py only lets that number go down.
FONT_SCALE = (11, 12, 13, 14, 16, 18, 24, 32, 40)   # px: caption, label, body, title, section, large, hero, display, display-xl
RADIUS_SCALE = (0, 4, 6, 8, 12, 14, 999)         # px: square, chip, control, card, large card, glass card, pill
SPACING_SCALE = (2, 4, 6, 8, 12, 16, 20, 24)     # px


# Named text roles: one size and one weight each, so the same kind of label looks the same everywhere. Change a role here
# and every label that uses it follows. (size px, weight)
TEXT_ROLES = {
    "caption": (11, 700),
    "label": (12, 700),
    "body": (13, 700),
    "section": (13, 850),
    "title": (14, 850),
    "large": (18, 800),
    "hero": (24, 850),
    "display": (40, 850),
}


def text_css(role: str, color_name: str = "text", extra: str = "") -> str:
    size, weight = TEXT_ROLES.get(role, TEXT_ROLES["body"])
    return f"color:{color(color_name)}; font-size:{size}px; font-weight:{weight}; letter-spacing:0px;{(' ' + extra.strip()) if extra.strip() else ''}"


def font_px(size: int) -> int:
    """The nearest size on FONT_SCALE (ties round up), for migrating one-off sizes."""
    return min(FONT_SCALE, key=lambda s: (abs(s - int(size)), -s))


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


def glass_card_css(object_name: str) -> str:
    """Show-tab redesign card: a faint glass gradient over the canvas, a fine border and a 14 px corner."""
    return f"""
        QFrame#{object_name} {{
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 rgba(255,255,255,0.040),
                stop:1 rgba(255,255,255,0.016));
            border-radius: 14px;
            border: 1px solid rgba(140,150,200,0.14);
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


def hero_card_css(object_name: str, border_alpha: float = 0.32) -> str:
    """Show-tab redesign hero: the mockup's deep purple gradient with a violet border and a 14 px corner."""
    return f"""
        QFrame#{object_name} {{
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 rgba(20,16,58,1),
                stop:1 rgba(12,12,32,1));
            border: 1px solid rgba(160,140,255,{border_alpha:.3f});
            border-radius: 14px;
        }}
    """


def glass_nav_css(object_name: str) -> str:
    """Show-tab redesign: the bottom nav strip as a fine-bordered glass bar."""
    return f"""
        QFrame#{object_name} {{
            background: rgba(255,255,255,0.030);
            border: 1px solid rgba(140,150,200,0.14);
            border-radius: 16px;
        }}
    """


def glass_nav_item_css(*, active: bool = False) -> str:
    """Show-tab redesign nav cell: a soft hover tint, and a purple gradient for the active one (the labels are styled directly)."""
    if active:
        bg = f"qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {color('accent_bright')}, stop:1 {color('accent')})"
        border = "rgba(200,180,255,0.50)"
        hover = bg
    else:
        bg, border, hover = "transparent", "transparent", "rgba(139,92,246,0.10)"
    return f"""
        QPushButton {{ background: {bg}; border: 1px solid {border}; border-radius: 12px; padding: 3px 4px 4px 4px; }}
        QPushButton:hover {{ background: {hover}; }}
        QPushButton:pressed {{ background: rgba(46,16,101,0.85); }}
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


def chip_pill_css() -> str:
    """A small rounded 'pill' label (Key +2, Tempo 100%, CDG ...)."""
    return f"""
        QLabel {{
            color: {color('text')};
            background-color: rgba(255,255,255,0.06);
            border: 1px solid rgba(115,144,180,0.16);
            border-radius: 12px;
            padding: 0px 11px;
            font-size: 11px;
            font-weight: 700;
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
            outline: none;
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
            outline: none;
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
            background-color: {color('surface_alt')};
            alternate-background-color: rgba(255,255,255,0.020);
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


def thin_slider_css(groove: int = 5) -> str:
    """Show-tab redesign progress bar: a thin track with a purple fill and no knob (it is still draggable)."""
    r = max(2, groove // 2)
    return f"""
        QSlider {{ min-height: {max(14, groove + 8)}px; }}
        QSlider::groove:horizontal {{ height: {groove}px; background: rgba(255,255,255,0.08); border-radius: {r}px; }}
        QSlider::sub-page:horizontal {{
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {color('accent')}, stop:1 {color('accent_bright')});
            border-radius: {r}px;
        }}
        QSlider::handle:horizontal {{ width: 0px; height: 0px; margin: 0px; background: transparent; border: none; }}
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

def bgm_styles() -> dict:
    """BGM tab redesign: every stylesheet it needs, by name (kept here so the app file holds no new colour or size literals)."""
    ghost = ("QPushButton { background: transparent; color: rgba(205,210,230,0.96); border: 1px solid rgba(140,150,200,0.28);"
             " border-radius: 8px; padding: 6px 12px; font-size: 12px; font-weight: 600; }"
             " QPushButton:hover { background: rgba(124,61,255,0.16); border-color: rgba(167,139,250,0.5); color: white; }"
             " QPushButton:disabled { color: rgba(255,255,255,0.30); }")
    box = ("background: rgba(0,0,0,0.20); border: 1px solid rgba(140,150,200,0.14); border-radius: 8px;"
           " padding: 8px 12px; color: #e8eaf4; font-size: 13px;")
    return {
        "shell": "QFrame#bgWorkspaceShell { background: transparent; border: none; }",
        "title": "color: rgba(154,160,189,1); font-size: 11px; font-weight: 700; letter-spacing: 2px;",
        "kicker": "color: #6df0a0; font-size: 11px; font-weight: 800; letter-spacing: 1.5px;",
        "now": "color: #ffffff; font-size: 18px; font-weight: 800;",
        "slider": thin_slider_css(5),
        "ghost": ghost,
        "danger": ghost.replace("rgba(140,150,200,0.28)", "rgba(255,92,92,0.45)").replace("rgba(205,210,230,0.96)", "rgba(255,150,150,0.95)"),
        "primary": ("QPushButton { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #8b4dff, stop:1 #6a2fe6); color: white; border: none;"
                    " border-radius: 8px; font-weight: 700; } QPushButton:hover { background: #9a62ff; }"),
        "pill": ("QCheckBox { background: transparent; color: rgba(205,210,230,0.96); padding: 4px 14px; spacing: 0px;"
                 " border: 1px solid rgba(140,150,200,0.28); border-radius: 14px; font-size: 12px; font-weight: 600; }"
                 " QCheckBox:checked { background: rgba(124,61,255,0.22); border-color: rgba(167,139,250,0.55); color: #c4b5fd; }"
                 " QCheckBox::indicator { width: 0px; height: 0px; margin: 0px; image: none; }"),
        "input": f"QLineEdit {{ {box} }}",
        "tree": ("QTreeView { background: transparent; border: none; font-size: 12px; color: #e8eaf4; outline: 0; }"
                 " QTreeView::item { padding: 5px 4px; border-radius: 8px; }"
                 " QTreeView::item:hover { background: rgba(255,255,255,0.055); }"
                 " QTreeView::item:selected { background: rgba(124,61,255,0.34); color: white; }"),
        "list": ("QListWidget { background: transparent; border: none; outline: 0; }"
                 " QListWidget::item { background: transparent; border: none; }"),
        "player": ("QFrame#bgPlayerFrame {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(124,61,255,0.30), stop:1 rgba(20,16,50,0.62));"
                   " border: 1px solid rgba(167,139,250,{edge:.2f}); border-radius: 14px; }}"),
    }


def history_styles() -> dict:
    """Singer History redesign stylesheets by name (the BGM ones are reused so both pages match)."""
    s = bgm_styles()
    s["name"] = "color: #ffffff; font-size: 24px; font-weight: 800;"
    s["caps"] = "color: rgba(154,160,189,1); font-size: 11px; font-weight: 700; letter-spacing: 2px;"
    s["view"] = "QListView { background: transparent; border: none; outline: 0; } QListView::item { background: transparent; border: none; }"
    s["plain_card"] = "QFrame#{name} { background: rgba(255,255,255,0.03); border: 1px solid rgba(140,150,200,0.12); border-radius: 12px; }"
    s["shell"] = "QFrame#singerHistoryShell { background: transparent; border: none; }"
    s["primary_btn"] = s["primary"].replace("border-radius: 8px;", "border-radius: 8px; padding: 6px 14px; font-size: 12px;", 1)
    s["muted"] = "color: rgba(154,160,189,1); font-size: 12px; font-weight: 600;"
    s["toggle"] = (s["ghost"].replace("QPushButton {", "QPushButton { text-align: left;", 1))
    return s


def chat_styles() -> dict:
    """Chat page redesign stylesheets by name (shares the ghost/primary/input looks of the other redesigned pages)."""
    s = history_styles()
    s["viewbox"] = ("QTextBrowser { background: rgba(0,0,0,0.20); border: 1px solid rgba(140,150,200,0.14); border-radius: 12px;"
                    " padding: 10px; color: #e8eaf4; font-size: 13px; }")
    s["chatlist"] = ("QListWidget { background: rgba(255,255,255,0.03); border: 1px solid rgba(140,150,200,0.12); border-radius: 12px; padding: 6px; outline: 0; }"
                     " QListWidget::item { padding: 9px 10px; border-radius: 8px; color: #e8eaf4; }"
                     " QListWidget::item:hover { background: rgba(255,255,255,0.055); }"
                     " QListWidget::item:selected { background: rgba(124,61,255,0.34); color: white; }")
    s["tabbar"] = ("QTabBar { background: transparent; qproperty-drawBase: 0; }"
                   " QTabBar::tab { background: transparent; color: rgba(205,210,230,0.96); border: 1px solid rgba(140,150,200,0.28);"
                   " border-radius: 14px; padding: 7px 20px; margin: 2px 8px 6px 0px; font-size: 13px; font-weight: 700; min-height: 18px; }"
                   " QTabBar::tab:hover { background: rgba(124,61,255,0.16); }"
                   " QTabBar::tab:selected { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #8b4dff, stop:1 #6a2fe6); color: white; border-color: transparent; }")
    return s


def dialog_extra_css() -> str:
    """Appended to the shared dialog stylesheet when the dialogs redesign is on (Network, EQ, Settings and the other dialogs).
    Later rules win over the base ones: softer cards, ghost buttons, rounded inputs, pill tabs."""
    return """
        QDialog { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #151030, stop:1 #0a0a16); }
        QPushButton { background: transparent; border: 1px solid rgba(140,150,200,0.28); border-radius: 9px; }
        QPushButton:hover { background: rgba(124,61,255,0.16); border-color: rgba(167,139,250,0.5); }
        QDialogButtonBox QPushButton:default { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #8b4dff, stop:1 #6a2fe6); border: none; color: white; font-weight: 700; }
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox { background-color: rgba(0,0,0,0.22); border: 1px solid rgba(140,150,200,0.16); border-radius: 10px; padding: 7px 10px; }
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid rgba(167,139,250,0.6); }
        QListWidget, QListView, QTreeView, QTableView { background-color: rgba(255,255,255,0.03); border: 1px solid rgba(140,150,200,0.12); border-radius: 12px; }
        QTabWidget::pane { border: none; }
        QTabBar::tab { background: transparent; color: rgba(205,210,230,0.96); border: 1px solid rgba(140,150,200,0.22); border-radius: 12px; padding: 8px 14px; margin: 3px 4px; font-weight: 700; }
        QTabBar::tab:hover { background: rgba(124,61,255,0.16); }
        QTabBar::tab:selected { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #8b4dff, stop:1 #6a2fe6); color: white; border-color: transparent; }
        QGroupBox { border: 1px solid rgba(140,150,200,0.16); border-radius: 12px; margin-top: 10px; padding-top: 8px; }
    """
