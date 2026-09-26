# -*- coding: utf-8 -*-
"""Единая дизайн-система приложения в стиле фирменных КП/писем."""
import tkinter as tk
from tkinter import ttk

INK = "#142B45"
ACCENT = "#3565EA"
ACCENT_HOVER = "#2653CA"
MUTED = "#6A788C"
LINE = "#DEE5EF"
WASH = "#F4F7FC"
APP_BG = "#EDF1F7"
WHITE = "#FFFFFF"
SOFT_BLUE = "#EEF3FF"
SOFT_RED = "#FFF1F0"
SOFT_YELLOW = "#FFF8E7"
SOFT_GREEN = "#ECF7F0"
RED = "#B42318"
AMBER = "#9A6700"
GREEN = "#287A4B"

FONT = "Segoe UI"
FONT_SIZE = 10


def apply(root):
    root.configure(bg=APP_BG)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    # Base surfaces
    style.configure("TFrame", background=APP_BG)
    style.configure("Card.TFrame", background=WHITE, relief="flat")
    style.configure("Header.TFrame", background=INK)
    style.configure("Status.TFrame", background=WHITE)
    style.configure("TLabel", background=APP_BG, foreground=INK, font=(FONT, FONT_SIZE))
    style.configure("Card.TLabel", background=WHITE, foreground=INK, font=(FONT, FONT_SIZE))
    style.configure("Muted.TLabel", background=APP_BG, foreground=MUTED, font=(FONT, FONT_SIZE-1))
    style.configure("CardMuted.TLabel", background=WHITE, foreground=MUTED, font=(FONT, FONT_SIZE-1))
    style.configure("Header.TLabel", background=INK, foreground=WHITE, font=(FONT, FONT_SIZE))
    style.configure("HeaderTitle.TLabel", background=INK, foreground=WHITE, font=(FONT, 17, "bold"))
    style.configure("HeaderSub.TLabel", background=INK, foreground="#B6C7DF", font=(FONT, 10))
    style.configure("HeaderOwner.TLabel", background=INK, foreground="#D9E5F5", font=(FONT, 11, "bold"))
    style.configure("HeaderClock.TLabel", background=INK, foreground=WHITE, font=(FONT, 10, "bold"))
    style.configure("HeaderClockBig.TLabel", background=INK, foreground=WHITE, font=(FONT, 16, "bold"))
    style.configure("HeaderStatus.TLabel", background=INK, foreground=WHITE, font=(FONT, 9, "bold"))
    style.configure("HeaderQuote.TLabel", background=INK, foreground=WHITE, font=(FONT, 10, "italic"))
    style.configure("Section.TLabel", background=APP_BG, foreground=INK, font=(FONT, 11, "bold"))
    style.configure("KPI.TLabel", background=WHITE, foreground=INK, font=(FONT, 10, "bold"), padding=(12, 9))
    style.configure("KPIAccent.TLabel", background=SOFT_BLUE, foreground=ACCENT, font=(FONT, 10, "bold"), padding=(12, 9))
    style.configure("AttentionKPI.TLabel", background=SOFT_BLUE, foreground=ACCENT, font=(FONT, 11, "bold"), padding=(14, 10))
    style.configure("OverdueKPI.TLabel", background=SOFT_RED, foreground=RED, font=(FONT, 11, "bold"), padding=(14, 10))

    # Inputs
    for name in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(name, fieldbackground=WHITE, foreground=INK, bordercolor=LINE,
                        lightcolor=LINE, darkcolor=LINE, padding=6, font=(FONT, FONT_SIZE))
        style.map(name, bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)], darkcolor=[("focus", ACCENT)])

    # Buttons
    style.configure("TButton", background=WHITE, foreground=INK, bordercolor=LINE,
                    lightcolor=LINE, darkcolor=LINE, padding=(11, 7), font=(FONT, FONT_SIZE, "bold"))
    style.map("TButton", background=[("active", "#EAF0FF"), ("pressed", "#DFE8FF")],
              bordercolor=[("active", "#BDCCEF")])
    style.configure("Primary.TButton", background=ACCENT, foreground=WHITE, bordercolor=ACCENT,
                    lightcolor=ACCENT, darkcolor=ACCENT, padding=(13, 8), font=(FONT, FONT_SIZE, "bold"))
    style.map("Primary.TButton", background=[("active", ACCENT_HOVER), ("pressed", "#2147AE")],
              foreground=[("disabled", "#D9E2F3"), ("!disabled", WHITE)])
    style.configure("Danger.TButton", background=WHITE, foreground=RED, bordercolor=LINE,
                    lightcolor=LINE, darkcolor=LINE, padding=(11, 7), font=(FONT, FONT_SIZE, "bold"))
    style.map("Danger.TButton", background=[("active", SOFT_RED)], bordercolor=[("active", "#F0B8B3")])
    style.configure("Header.TButton", background=INK, foreground=WHITE, bordercolor="#58708C",
                    lightcolor="#58708C", darkcolor="#58708C", padding=(10, 6), font=(FONT, 9, "bold"))
    style.map("Header.TButton", background=[("active", "#294562")])

    # Notebook tabs
    style.configure("TNotebook", background=APP_BG, borderwidth=0, tabmargins=(8, 8, 8, 0))
    style.configure("TNotebook.Tab", background=APP_BG, foreground=MUTED, borderwidth=0,
                    padding=(15, 10), font=(FONT, FONT_SIZE, "bold"))
    style.map("TNotebook.Tab", background=[("selected", WHITE), ("active", "#E8EDF5")],
              foreground=[("selected", INK), ("active", INK)])

    # Tables
    style.configure("Treeview", background=WHITE, fieldbackground=WHITE, foreground=INK,
                    bordercolor=LINE, lightcolor=LINE, darkcolor=LINE,
                    font=(FONT, FONT_SIZE), rowheight=30)
    style.configure("Treeview.Heading", background=WASH, foreground=INK, bordercolor=LINE,
                    lightcolor=LINE, darkcolor=LINE, font=(FONT, FONT_SIZE, "bold"), padding=(7, 8))
    style.map("Treeview.Heading", background=[("active", "#E8EEF8")])
    style.map("Treeview", background=[("selected", SOFT_BLUE)], foreground=[("selected", INK)])

    style.configure("TSeparator", background=LINE)
    return style