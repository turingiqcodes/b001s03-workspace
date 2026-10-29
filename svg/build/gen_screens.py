"""Generate the Digital Library screens in svg/ and the clickable walkthrough svg/prototype.html.

Usage (from the repo root):
    svg/build/export_data.sh svg/build/data          # read-only export of the tables to JSON
    python3 -I svg/build/gen_screens.py svg svg/build/data svg/build/header.part

<data dir> holds one JSON array per table. Every number on the screens comes from it.
"""
import json
import sys
from collections import Counter
from pathlib import Path

SVG_DIR, DATA_DIR, HEADER = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
INK, MUTED, LINE, BRAND, MINT, BG, SOFT, FIELD = "#1f2a24", "#7a837e", "#e4e7e5", "#1f4d3a", "#dcefe5", "#eef6f1", "#a8cbb8", "#f6f8f7"
RED, RED_LINE, AMBER_BG, AMBER = "#a23f2a", "#e5b7ab", "#fdf3dc", "#8a5a12"
ICON = 'fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"'

ICONS = {
    "home": '<rect x="2" y="2" width="7" height="7" rx="1.5"/><rect x="11" y="2" width="7" height="7" rx="1.5"/><rect x="2" y="11" width="7" height="7" rx="1.5"/><rect x="11" y="11" width="7" height="7" rx="1.5"/>',
    "library": '<path d="M10 5C8 3.5 5.5 3 3 3v12c2.5 0 5 .5 7 2 2-1.5 4.5-2 7-2V3c-2.5 0-5 .5-7 2z"/><path d="M10 5v12"/>',
    "community": '<circle cx="7" cy="7" r="3"/><path d="M1 17c0-3 2.7-5 6-5s6 2 6 5"/><circle cx="14.5" cy="6" r="2.5"/><path d="M15 11c2.5 0 4.5 1.6 4.5 4.5"/>',
    "person": '<circle cx="10" cy="7" r="3.5"/><path d="M3 18c0-3.9 3.1-6.5 7-6.5s7 2.6 7 6.5"/>',
    "ai": '<path d="M11 2L4 11h6l-1 7 7-9h-6z"/>',
    "sales": '<path d="M2 3h2.5l2 10h9.5l2-7H5.5"/><circle cx="8" cy="16.5" r="1.3"/><circle cx="15" cy="16.5" r="1.3"/>',
    "discount": '<path d="M10.5 2H17a1 1 0 0 1 1 1v6.5l-8.5 8.5L2 10.5z"/><circle cx="13.5" cy="6.5" r="1.4"/>',
    "publisher": '<path d="M3 18V5l7-3 7 3v13"/><path d="M1.5 18h17M7 8h2M11 8h2M7 12h2M11 12h2"/>',
    "employee": '<rect x="2" y="4" width="16" height="13" rx="2"/><circle cx="7.5" cy="10" r="2.2"/><path d="M4 15c.6-1.6 2-2.4 3.5-2.4s2.9.8 3.5 2.4M12.5 9h3.5M12.5 12h3"/>',
    "access": '<circle cx="7" cy="12" r="4"/><path d="M10 9l7-7M14 5l2.5 2.5M12 7l2 2"/>',
    "settings": '<circle cx="10" cy="10" r="2.6"/><circle cx="10" cy="10" r="6.5"/><path d="M10 1v2M10 17v2M1 10h2M17 10h2M3.6 3.6l1.4 1.4M15 15l1.4 1.4M3.6 16.4L5 15M15 5l1.4-1.4"/>',
    "help": '<circle cx="10" cy="10" r="8"/><path d="M7.6 7.8a2.5 2.5 0 1 1 3.4 2.4c-.7.3-1 .8-1 1.6"/><path d="M10 14.6v.1"/>',
    "store": '<path d="M2 7l1.5-4.5h13L18 7"/><path d="M2 7c0 1.5 1.3 2.5 2.7 2.5S7.3 8.5 7.3 7c0 1.5 1.3 2.5 2.7 2.5s2.7-1 2.7-2.5c0 1.5 1.3 2.5 2.6 2.5S18 8.5 18 7M3.5 9.5V17h13V9.5M8 17v-4h4v4"/>',
    "units": '<path d="M10 2l7 3.5v9L10 18l-7-3.5v-9z"/><path d="M3 5.5l7 3.5 7-3.5M10 9v9"/>',
    "money": '<circle cx="10" cy="10" r="8"/><path d="M12.5 7c-.5-1-1.5-1.4-2.5-1.4-1.4 0-2.5.8-2.5 2s1 1.7 2.5 2 2.5.8 2.5 2-1.1 2-2.5 2c-1.1 0-2.1-.5-2.6-1.5M10 4v1.6M10 14.4V16"/>',
    "plus": '<path d="M10 4v12M4 10h12"/>',
    "chev": '<path d="M6 8l4 4 4-4"/>',
    "search": '<circle cx="8.5" cy="8.5" r="6"/><path d="M13 13l5 5"/>',
    "edit": '<path d="M13.5 3l3.5 3.5L7 16.5H3.5V13z"/>',
    "info": '<circle cx="10" cy="10" r="8"/><path d="M10 9v5M10 6.2v.1"/>',
    "cal": '<rect x="2.5" y="4" width="15" height="13.5" rx="2"/><path d="M2.5 8.5h15M6.5 2v4M13.5 2v4"/>',
    "back": '<path d="M16 10H4M9 5l-5 5 5 5"/>',
    "trash": '<path d="M3 5h14M8 5V3h4v2M5 5l1 12h8l1-12M8.5 8.5v5.5M11.5 8.5v5.5"/>',
    "key": '<circle cx="7" cy="12" r="4"/><path d="M10 9l7-7M14 5l2.5 2.5"/>',
    "lock": '<rect x="3.5" y="9" width="13" height="9" rx="2"/><path d="M6.5 9V6a3.5 3.5 0 0 1 7 0v3"/>',
    "phone": '<path d="M4 2.5h3.5l1.8 4.3-2.4 1.6a9.6 9.6 0 0 0 4.7 4.7l1.6-2.4 4.3 1.8v3.5a1.8 1.8 0 0 1-1.9 1.8A15.6 15.6 0 0 1 2.2 4.4 1.8 1.8 0 0 1 4 2.5z"/>',
    "pin": '<path d="M10 18s6-5.5 6-10.2a6 6 0 0 0-12 0C4 12.5 10 18 10 18z"/><circle cx="10" cy="7.8" r="2.2"/>',
    "idcard": '<rect x="1.5" y="4" width="17" height="12" rx="2"/><circle cx="7" cy="9.5" r="2"/><path d="M4 14c.5-1.4 1.6-2 3-2s2.5.6 3 2M12.5 8.5h3.5M12.5 11.5h3"/>',
    "homeaddr": '<path d="M3 9l7-6 7 6v8.5H3z"/><path d="M8 17.5V12h4v5.5"/>',
    "map": '<path d="M2 5l5-2 6 2 5-2v12l-5 2-6-2-5 2z"/><path d="M7 3v12M13 5v12"/>',
    "mail": '<rect x="2" y="4" width="16" height="12" rx="2"/><path d="M2.5 5l7.5 6 7.5-6"/>',
    "check": '<path d="M4 10.5l4 4 8-9"/>',
    "shelf": '<path d="M2 17.5h16M3.5 17.5V4h3v13.5M6.5 17.5V6h3v11.5M11 17.5l-1.2-11 3-.4 1.2 11M15 17.5V5h2.5v12.5"/>',
    "eye": '<path d="M1.5 10S4.5 4 10 4s8.5 6 8.5 6-3 6-8.5 6-8.5-6-8.5-6z"/><circle cx="10" cy="10" r="2.5"/>',
    "sun": '<circle cx="10" cy="10" r="3.5"/><path d="M10 1.5v2M10 16.5v2M1.5 10h2M16.5 10h2M4 4l1.4 1.4M14.6 14.6L16 16M4 16l1.4-1.4M14.6 5.4L16 4"/>',
    "moon": '<path d="M16.5 12.5A7 7 0 0 1 7.5 3.5a7 7 0 1 0 9 9z"/>',
    "monitor": '<rect x="2" y="3" width="16" height="11" rx="2"/><path d="M7 17.5h6M10 14v3.5"/>',
    "table": '<rect x="2.5" y="3.5" width="15" height="13" rx="2"/><path d="M2.5 8h15M2.5 12.2h15M8 8v8.5"/>',
    "shield": '<path d="M10 2l6.5 2.5V9c0 4.2-2.8 7.5-6.5 9-3.7-1.5-6.5-4.8-6.5-9V4.5z"/><path d="M7 10l2.2 2.2L13.5 8"/>',
    "database": '<ellipse cx="10" cy="4.5" rx="7" ry="2.5"/><path d="M3 4.5v11c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5v-11M3 10c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5"/>',
    "logout": '<path d="M8 3H4.5A1.5 1.5 0 0 0 3 4.5v11A1.5 1.5 0 0 0 4.5 17H8M13 6l4 4-4 4M17 10H8"/>',
}


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;")


def icon(name, x, y, color=INK, scale=1):
    t = f"translate({x} {y})" + (f" scale({scale})" if scale != 1 else "")
    return f'<g transform="{t}" {ICON.format(c=color)}>{ICONS[name]}</g>'


def text(x, y, s, size=13, weight=None, color=INK, anchor=None, extra=""):
    w = f' font-weight="{weight}"' if weight else ""
    a = f' text-anchor="{anchor}"' if anchor else ""
    return f'<text x="{x}" y="{y}" font-size="{size}"{w}{a} fill="{color}"{extra}>{esc(s)}</text>'


def rect(x, y, w, h, rx=0, fill="#ffffff", stroke=None, extra=""):
    s = f' stroke="{stroke}"' if stroke else ""
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}"{s}{extra}/>'


def hline(x1, x2, y):
    return f'<path d="M{x1} {y + .5}h{x2 - x1}" stroke="{LINE}"/>'


def vline(x, y1, y2):
    return f'<path d="M{x + .5} {y1}v{y2 - y1}" stroke="{LINE}"/>'


def circle(cx, cy, r, fill, extra=""):
    return f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}"{extra}/>'


def clip(s, n):
    s = str(s)
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def initials(name):
    parts = name.split()
    return (parts[0][0] + parts[-1][0]).upper()


# ---------------------------------------------------------------- page chrome
def header(title_id, title, role):
    out = []
    for l in HEADER.read_text().splitlines():
        l = l.replace("ab-title", title_id).replace("Digital Library – Add New Book", f"Digital Library – {esc(title)}")
        if ">Profile<" in l:
            l = l.replace(">Profile<", ">Admin<" if role == "admin" else ">Author<")
            if role == "admin":
                l += ('\n<circle cx="1104" cy="54" r="8" fill="#ffffff"/><path d="M1104 48.5l4.5 1.8v3c0 2.6-1.9 4.4-4.5 5.2-2.6-.8-4.5-2.6-4.5-5.2v-3z" fill="#e0a83a"/>')
        if role == "author":
            l = l.replace("Search books or authors", "Search the library")
        out += l.splitlines()
    return out


SIDEBARS = {
    "admin": [
        ("MENU", [("home", "Dashboard"), ("library", "Library"), ("community", "Community"), ("ai", "AI Intelligence")]),
        ("ADMIN", [("sales", "Sales"), ("discount", "Discounts"), ("store", "Stores"), ("publisher", "Publishers"),
                   ("employee", "Employees"), ("access", "Author Access")]),
    ],
    "author": [("MENU", [("library", "My Books"), ("shelf", "Library"), ("plus", "Add Book"), ("person", "My Profile"), ("community", "Community"), ("ai", "AI Intelligence")])],
}
SIDEBAR_FOOT = [("settings", "Settings"), ("help", "Help")]
SIDE_STEP = 46


def sidebar_layout(role):
    """Section label positions and the icon y of every item, shared by the drawing and the walkthrough hotspots."""
    labels, items, y = [], [], 118
    for label, entries in SIDEBARS[role]:
        labels.append((label, y))
        for i, (k, name) in enumerate(entries):
            items.append((k, name, y + 29 + SIDE_STEP * i))
        y = items[-1][2] + SIDE_STEP + 16
    foot = [(k, name, 676 + SIDE_STEP * i) for i, (k, name) in enumerate(SIDEBAR_FOOT)]
    return labels, items, foot


def sidebar(active, role):
    """`active` is the set of item keys this screen covers."""
    labels, items, foot = sidebar_layout(role)
    out = [f"<!-- sidebar ({role}) -->", rect(0, 81, 210, 719), f'<path d="M210.5 81v719" stroke="{LINE}"/>']
    out += [text(28, y, label, 11, 700, MUTED, extra=' letter-spacing="1.2"') for label, y in labels]
    for k, name, y in items:
        on = k in active
        if on:
            out += [rect(14, y - 11, 182, 42, 8, MINT), rect(0, y - 5, 4, 30, 2, BRAND)]
        out += [icon(k, 28, y, BRAND if on else INK), text(60, y + 15, name, 15, 600 if on else 500, BRAND if on else INK)]
        if k == "ai":
            out.append(icon("chev", 172, y, MUTED))
    out.append(hline(14, 196, 656))
    for k, name, y in foot:
        on = k in active
        if on:
            out += [rect(14, y - 11, 182, 42, 8, MINT), rect(0, y - 5, 4, 30, 2, BRAND)]
        out += [icon(k, 28, y, BRAND if on else INK), text(60, y + 15, name, 15, 600 if on else 500, BRAND if on else INK)]
    return out


def heading(title, crumb, sub, root="Admin", count=None):
    t = text(272, 129, title, 22, 700) if count is None else \
        f'<text x="272" y="129" font-size="22" font-weight="700" fill="{INK}">{esc(title)} <tspan font-weight="500" fill="{MUTED}">({count})</tspan></text>'
    return ["<!-- heading -->", icon("back", 240, 112), t,
            f'<text x="1168" y="128" font-size="13" text-anchor="end" fill="{MUTED}">{esc(root)}  /  <tspan fill="{BRAND}" font-weight="600">{esc(crumb)}</tspan></text>',
            text(240, 156, sub, 14, color=MUTED)]


def stat_tiles(y, tiles, h=88):
    out = []
    for i, (ic, label, value, note) in enumerate(tiles):
        x = 240 + i * 236
        out += [rect(x, y, 220, h, 12, stroke=LINE), rect(x + 20, y + 20, 36, 36, 9, BG), icon(ic, x + 28, y + 28, BRAND),
                text(x + 68, y + 34, label, 13, color=MUTED), text(x + 68, y + 60, value, 22, 700), text(x + 20, y + h - 14, note, 12, color=MUTED)]
    return out


def button(x, y, w, label, primary=True, ic="plus", h=36, danger=False):
    fg = "#ffffff" if primary else (RED if danger else BRAND)
    bg = BRAND if primary else "#ffffff"
    border = None if primary else (RED_LINE if danger else LINE)
    out = [rect(x, y, w, h, 8, bg, border)]
    if ic:
        out.append(icon(ic, x + 14, y + (h - 20) / 2 + 1, fg, .9))
    out.append(text(x + (38 if ic else w / 2), y + h / 2 + 5, label, 14, 600, fg, None if ic else "middle"))
    return out


def select(x, y, w, label, ic=None, h=36, color=INK, fill=FIELD):
    out = [rect(x, y, w, h, 9, fill, LINE)]
    tx = x + 14
    if ic:
        out.append(icon(ic, x + 12, y + (h - 20) / 2, MUTED, .9))
        tx = x + 40
    return out + [text(tx, y + h / 2 + 5, label, 14, color=color), icon("chev", x + w - 30, y + (h - 20) / 2, MUTED)]


def pill(cx, y, label, fill, color, size=11, pad=10):
    w = len(label) * size * .6 + pad * 2
    return [rect(round(cx - w / 2, 1), y, round(w, 1), 22, 11, fill), text(cx, y + 15, label, size, 600, color, "middle")]


def status_active(x, y):
    """Green 'Active' pill whose left edge is x."""
    return [rect(x, y, 60, 22, 11, MINT), circle(x + 12, y + 11, 3.5, "#2d8a5a"), text(x + 38, y + 15, "Active", 11, 600, BRAND, "middle")]


def pager(x, y, labels, current="1"):
    out = []
    for i, lbl in enumerate(labels):
        bx = x + i * 34
        on = lbl == current
        out += [rect(bx, y, 28, 28, 7, BRAND if on else "#ffffff", None if on or lbl == "…" else LINE),
                text(bx + 14, y + 19, lbl, 13, 600, "#ffffff" if on else INK, "middle")]
    return out


def table_head(x, w, y, cols):
    out = [rect(x + 1, y, w - 2, 32, 0, FIELD), hline(x, x + w, y), hline(x, x + w, y + 31)]
    return out + [text(cx, y + 21, label, 12, 600, MUTED, anchor) for label, cx, anchor in cols]


def write(name, title_id, title, role, active, body):
    lines = header(title_id, title, role) + sidebar(active, role) + body
    out = [lines[0]] + ["  " + l.strip() for l in lines[1:]]
    path = SVG_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n</svg>\n")
    print("wrote", path)


# ---------------------------------------------------------------- data
def load(name):
    return json.loads((DATA_DIR / f"{name}.json").read_text())


def load_optional(name):
    path = DATA_DIR / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else []


AUTHORS = {a["au_id"]: a for a in load("authors")}
TITLES = {t["title_id"]: t for t in load("titles")}
TITLEAUTHOR = load("titleauthor")
PUBLISHERS = load("publishers")
PUB = {p["pub_id"]: p for p in PUBLISHERS}
PUB_INFO = {p["pub_id"]: p["pr_info"] for p in load("pub_info")}
EMPLOYEES = load("employee")
JOBS = {j["job_id"]: j for j in load("jobs")}
STORES = {s["stor_id"]: s for s in load("stores")}
SALES = load("sales")
DISCOUNTS = load("discounts")
ROYSCHED = load("roysched")
REMOVALS = load_optional("app_title_removals")

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
TYPE_LABEL = {"business": "Business", "psychology": "Psychology", "popular_comp": "Popular computing",
              "mod_cook": "Modern cooking", "trad_cook": "Traditional cooking", "UNDECIDED": "Undecided"}
COVER = {"business": ("#2d6a4f", "#f2c66d"), "psychology": ("#b5523b", "#ffffff"), "popular_comp": ("#26415e", "#f2c66d"),
         "mod_cook": ("#e8a35c", INK), "trad_cook": ("#f2c66d", INK), "UNDECIDED": ("#6f9c86", "#ffffff")}

# The one author with a login (app_users), and today's date for its created_at.
LOGIN_AU_ID, LOGIN_USERNAME, CREATED = "486-29-1786", "clocksley", "26 Oct 2026"


def fmt_date(d, short=False):
    if not d:
        return "Not set"
    y, m, day = d[:10].split("-")
    return f"{MONTHS[int(m) - 1]} {y}" if short else f"{int(day)} {MONTHS[int(m) - 1]} {y}"


def money(v):
    return "Not priced" if v is None else f"${v:,.2f}"


def ttype(t):
    return TYPE_LABEL.get(t["type"].strip(), t["type"].strip())


def full_name(a):
    return f'{a["au_fname"]} {a["au_lname"]}'


def byline(a):
    """Short author line for a cover, e.g. 'C. LOCKSLEY'."""
    return f'{a["au_fname"][0]}. {a["au_lname"]}'.upper()


def title_authors(title_id):
    rows = sorted((r for r in TITLEAUTHOR if r["title_id"] == title_id), key=lambda r: r["au_ord"])
    return [AUTHORS[r["au_id"]] for r in rows]


def books_of(au_id):
    return [r for r in TITLEAUTHOR if r["au_id"] == au_id]


def store_name(stor_id):
    return STORES[stor_id]["stor_name"] if stor_id else "All stores"


def disc_scope(d):
    qty = f'Qty {d["lowqty"]:,} – {d["highqty"]:,}' if d["lowqty"] is not None else "Any quantity"
    return f"{store_name(d['stor_id'])} · {qty}"


def pct(v):
    return f"{v:g}%"


ORDERS = len({(s["stor_id"], s["ord_num"]) for s in SALES})
UNITS = sum(s["qty"] for s in SALES)
REVENUE = sum(s["qty"] * TITLES[s["title_id"]]["price"] for s in SALES if TITLES[s["title_id"]]["price"] is not None)
RECENT = sorted(SALES, key=lambda s: (s["ord_date"], s["ord_num"]), reverse=True)
STAFF = Counter(e["pub_id"] for e in EMPLOYEES)
TITLE_COUNT = Counter(t["pub_id"] for t in TITLES.values())
STORE_DISCOUNTS = sum(1 for d in DISCOUNTS if d["stor_id"])
LOGIN_AUTHOR = AUTHORS[LOGIN_AU_ID]
LOGIN_BOOKS = books_of(LOGIN_AU_ID)


def sales_rows(rows, cols, x, w, y):
    out = []
    for i, r in enumerate(rows):
        ry = y + i * 38
        for fn, cx in cols:
            out += fn(r, cx, ry + 24)
        if i < len(rows) - 1:
            out.append(hline(x + 16, x + w - 16, ry + 37))
    return out


TERMS_STYLE = {"Net 30": (BG, BRAND), "Net 60": ("#eef1f6", "#2f4a6b"), "ON invoice": (AMBER_BG, AMBER)}
C_ORDER = lambda r, x, y: [text(x, y, r["ord_num"], 13, 600)]
C_DATE = lambda r, x, y: [text(x, y, fmt_date(r["ord_date"]), 13, color=MUTED)]
C_STORE = lambda n: lambda r, x, y: [text(x, y, clip(store_name(r["stor_id"]), n), 13)]
C_TITLE = lambda n: lambda r, x, y: [text(x, y, clip(TITLES[r["title_id"]]["title"], n), 13)]
C_QTY = lambda r, x, y: [text(x, y, r["qty"], 13, 600, anchor="end")]
C_TERMS = lambda r, x, y: pill(x, y - 15, r["payterms"], *TERMS_STYLE.get(r["payterms"], (FIELD, INK)))


# ---------------------------------------------------------------- admin: dashboard (landing after admin login)
def admin_dashboard():
    body = heading("Admin Dashboard", "Dashboard", "Welcome back. Here’s how sales, discounts, publishers and employees look today.")
    body += stat_tiles(174, [
        ("sales", "Orders", str(ORDERS), f"{len(SALES)} order lines · {len(STORES)} stores"),
        ("money", "Revenue", f"${REVENUE:,.2f}", f"{UNITS:,} units sold"),
        ("publisher", "Publishers", str(len(PUBLISHERS)), f"{len(TITLES)} titles published"),
        ("employee", "Employees", str(len(EMPLOYEES)), f"across {len(STAFF)} publishers"),
    ], h=96)
    x, w, y = 240, 560, 286
    body += ["<!-- recent sales -->", rect(x, y, w, 448, 14, stroke=LINE), text(x + 24, y + 32, "Recent sales", 15, 700),
             text(x + 134, y + 32, f"{len(SALES)} lines", 13, color=MUTED), text(x + w - 24, y + 32, "View all sales →", 13, 600, BRAND, "end")]
    body += table_head(x, w, y + 50, [("Order #", 264, None), ("Date", 344, None), ("Store", 444, None), ("Title", 596, None), ("Qty", 776, "end")])
    body += sales_rows(RECENT[:9], [(C_ORDER, 264), (C_DATE, 344), (C_STORE(18), 444), (C_TITLE(19), 596), (C_QTY, 776)], x, w, y + 82)
    body += ["<!-- discounts -->", rect(816, 286, 352, 216, 14, stroke=LINE), text(840, 318, "Discounts", 15, 700),
             text(922, 318, str(len(DISCOUNTS)), 13, color=MUTED), text(1144, 318, "View all →", 13, 600, BRAND, "end"), hline(816, 1168, 334)]
    for i, d in enumerate(sorted(DISCOUNTS, key=lambda d: -d["discount"])[:3]):
        y = 350 + i * 50
        body += [rect(840, y, 32, 32, 8, BG), icon("discount", 846, y + 6, BRAND), text(884, y + 14, d["discounttype"], 14, 600),
                 text(884, y + 31, clip(disc_scope(d), 34), 12, color=MUTED), text(1144, y + 22, pct(d["discount"]), 16, 700, BRAND, "end")]
    body += ["<!-- publishers -->", rect(816, 518, 352, 216, 14, stroke=LINE), text(840, 550, "Publishers", 15, 700),
             text(1144, 550, "View all →", 13, 600, BRAND, "end"), hline(816, 1168, 566),
             text(840, 590, "Publisher", 12, 600, MUTED), text(1060, 590, "Titles", 12, 600, MUTED, "end"), text(1144, 590, "Staff", 12, 600, MUTED, "end")]
    for i, p in enumerate(sorted(PUBLISHERS, key=lambda p: (-TITLE_COUNT[p["pub_id"]], p["pub_name"]))[:5]):
        y = 616 + i * 25
        body += [text(840, y, p["pub_name"], 13, 500), text(1060, y, TITLE_COUNT[p["pub_id"]], 13, 600, anchor="end"),
                 text(1144, y, STAFF[p["pub_id"]], 13, 600, anchor="end")]
    write("admin-dashboard.svg", "adm-title", "Admin Dashboard", "admin", {"home"}, body)


# ---------------------------------------------------------------- admin: library (all titles)
def cover(x, y, t, w=104, h=152, author_line=None):
    fill, ink = COVER.get(t["type"].strip(), COVER["UNDECIDED"])
    words, lines = t["title"].upper().replace(":", "").split(), [""]
    for wd in words:
        if len(lines[-1]) + len(wd) + 1 > 11 and lines[-1]:
            lines.append(wd)
        else:
            lines[-1] = (lines[-1] + " " + wd).strip()
    if len(lines) > 4:
        lines = lines[:4]
        lines[3] = clip(lines[3] + "…", 11)
    cx = x + w / 2
    out = [rect(x + 3, y + 4, w, h, 4, INK, extra=' opacity=".12"'), rect(x, y, w, h, 4, fill), rect(x, y, 8, h, 3, "#000000", extra=' opacity=".15"')]
    out += [text(cx + 4, y + 44 + i * 17, ln, 12, 800, ink, "middle", ' letter-spacing=".5"') for i, ln in enumerate(lines)]
    out += [rect(cx - 14, y + h - 30, 36, 2, 0, ink, extra=' opacity=".7"'),
            text(cx + 4, y + h - 14, clip(author_line or "", 18), 8, 600, ink, "middle", ' letter-spacing="1" opacity=".85"')]
    return out


LIB_SHOWN = sorted(TITLES.values(), key=lambda t: (t["ytd_sales"] is None, -(t["ytd_sales"] or 0)))[:8]


def lib_card_pos(i):
    return 240 + (i % 4) * 238, 206 + (i // 4) * 284


def admin_library():
    body = heading("Library", "Library", "", count=len(TITLES))[:-1]
    body += button(864, 104, 164, "Removed books", False, "trash") + button(1040, 104, 128, "Add Book")
    types = Counter(t["type"].strip() for t in TITLES.values())
    chips = [("All", len(TITLES))] + [(TYPE_LABEL[k].replace("Popular computing", "Computing"), n) for k, n in types.most_common()]
    x = 240
    for i, (label, n) in enumerate(chips):
        s = f"{label} {n}"
        w = len(s) * 7.4 + 26
        on = i == 0
        body += [rect(x, 156, round(w), 30, 15, BRAND if on else "#ffffff", BRAND if on else LINE),
                 text(round(x + w / 2, 1), 176, s, 13, 600, "#ffffff" if on else INK, "middle")]
        x += round(w) + 10
    for i, t in enumerate(LIB_SHOWN):
        cx, cy = lib_card_pos(i)
        auths = title_authors(t["title_id"])
        by = ", ".join(full_name(a) for a in auths) if auths else "No author linked"
        body += [f"<!-- title: {t['title_id']} -->", rect(cx, cy, 214, 196, 12, MINT)]
        body += cover(cx + 55, cy + 22, t, author_line=(byline(auths[0]) if auths else ""))
        body += [text(cx + 2, cy + 222, clip(t["title"], 27), 15, 600), text(cx + 2, cy + 242, clip(by, 24), 13, color=MUTED),
                 text(cx + 212, cy + 242, money(t["price"]) if t["price"] is not None else "–", 13, 600, anchor="end")]
    write("dashboard.svg", "db-title", "Library", "admin", {"library"}, body)


# ---------------------------------------------------------------- author: library (every author's books, view only)
# The signed-in author's own books first (tagged Yours), then the best sellers.
AUTHOR_LIB_SHOWN = ([TITLES[r["title_id"]] for r in LOGIN_BOOKS] + [t for t in LIB_SHOWN if t["title_id"] not in {r["title_id"] for r in LOGIN_BOOKS}])[:8]


def author_library():
    body = heading("Library", "All books", "Every book in the library, from every author. Open one to read about it.", author_root(), count=len(TITLES))
    body += [rect(430, 106, 100, 26, 13, MINT), icon("eye", 442, 109, BRAND, .8), text(466, 124, "View only", 12, 600, BRAND)]
    types = Counter(t["type"].strip() for t in TITLES.values())
    chips = [("All", len(TITLES))] + [(TYPE_LABEL[k].replace("Popular computing", "Computing"), n) for k, n in types.most_common()]
    x = 240
    for i, (label, n) in enumerate(chips):
        s = f"{label} {n}"
        w = len(s) * 7.4 + 26
        on = i == 0
        body += [rect(x, 172, round(w), 30, 15, BRAND if on else "#ffffff", BRAND if on else LINE),
                 text(round(x + w / 2, 1), 192, s, 13, 600, "#ffffff" if on else INK, "middle")]
        x += round(w) + 10
    for i, t in enumerate(AUTHOR_LIB_SHOWN):
        cx, cy = lib_card_pos(i)
        cy += 16
        auths = title_authors(t["title_id"])
        by = ", ".join(full_name(a) for a in auths) if auths else "No author linked"
        body += [f"<!-- title: {t['title_id']} -->", rect(cx, cy, 214, 186, 12, MINT)]
        body += cover(cx + 55, cy + 17, t, author_line=(byline(auths[0]) if auths else ""))
        if any(a["au_id"] == LOGIN_AU_ID for a in auths):
            body += [rect(cx + 8, cy + 8, 56, 20, 10, "#ffffff"), circle(cx + 19, cy + 18, 3, "#2d8a5a"), text(cx + 41, cy + 22, "Yours", 10, 600, BRAND, "middle")]
        body += [text(cx + 2, cy + 208, clip(t["title"], 22), 15, 600), text(cx + 2, cy + 226, clip(by, 22), 13, color=MUTED),
                 text(cx + 212, cy + 226, money(t["price"]) if t["price"] is not None else "–", 13, 600, anchor="end")]
    write("author-library.svg", "al-title", "Library", "author", {"shelf"}, body)


# ---------------------------------------------------------------- admin: author access (only admins create author logins)
def author_access():
    a = LOGIN_AUTHOR
    contracted = sum(1 for x in AUTHORS.values() if x["contract"])
    body = heading("Author Access", "Author Access", "Only admins create logins. An author with a login can add and delete their own books.")
    body += [rect(240, 172, 300, 36, 9, FIELD, LINE), icon("search", 252, 180, MUTED, .9), text(280, 195, "Search authors", 14, color=MUTED)]
    body += select(552, 172, 200, "All login status") + button(980, 172, 188, "Create author login")
    body += stat_tiles(224, [
        ("community", "Authors", str(len(AUTHORS)), "in the authors table"),
        ("edit", "With contract", str(contracted), "contract = true"),
        ("access", "Active logins", "1", full_name(a)),
        ("library", "Books managed", str(len(LOGIN_BOOKS)), "by authors with a login"),
    ])
    others = sorted((x for x in AUTHORS.values() if x["au_id"] != LOGIN_AU_ID), key=lambda x: (x["au_lname"], x["au_fname"]))
    rows = [a] + others[:7]
    body += ["<!-- authors table -->", rect(240, 328, 600, 452, 14, stroke=LINE), text(264, 360, "Authors", 15, 700), text(334, 360, len(AUTHORS), 13, color=MUTED)]
    body += table_head(240, 600, 378, [("Author", 264, None), ("Author ID", 434, None), ("City", 540, None), ("Contract", 656, "middle"), ("Books", 742, "end"), ("Login", 788, "middle")])
    for i, x in enumerate(rows):
        y = 410 + i * 38
        login = x["au_id"] == LOGIN_AU_ID
        if login:
            body += [rect(241, y, 598, 37, 0, BG), rect(240, y + 6, 4, 26, 2, BRAND)]
        body += [circle(276, y + 19, 12, MINT), text(276, y + 23, initials(full_name(x)), 10, 700, BRAND, "middle"),
                 text(296, y + 24, clip(full_name(x), 16), 13, 600), text(434, y + 24, x["au_id"], 12, color=MUTED), text(540, y + 24, clip(x["city"], 14), 13)]
        body += pill(656, y + 8, "Yes" if x["contract"] else "No", BG if x["contract"] else "#f6e3de", BRAND if x["contract"] else RED)
        body += [text(742, y + 24, len(books_of(x["au_id"])), 13, 600, anchor="end")]
        body += status_active(758, y + 8) if login else [rect(756, y + 7, 64, 24, 7, "#ffffff", LINE), text(788, y + 23, "Create", 12, 600, BRAND, "middle")]
        if i < len(rows) - 1:
            body.append(hline(256, 824, y + 37))
    pages = -(-len(AUTHORS) // 8)
    body += [text(264, 762, f"Showing 1–8 of {len(AUTHORS)}", 13, color=MUTED)] + pager(630, 742, ["‹"] + [str(n) for n in range(1, pages + 1)] + ["›"])
    body += ["<!-- login details -->", rect(856, 328, 312, 452, 14, stroke=LINE), text(880, 360, "Login details", 15, 700), hline(856, 1168, 377),
             circle(904, 414, 24, BRAND), text(904, 420, initials(full_name(a)), 16, 700, "#ffffff", "middle"),
             text(940, 410, full_name(a), 15, 700), text(940, 430, f'{a["au_id"]} · {a["city"]}', 12, color=MUTED)]
    for i, (k, v) in enumerate([("Username", LOGIN_USERNAME), ("Role", "Author"), ("Status", None), ("Password", "Not set yet"),
                                ("Created", f"{CREATED} by admin"), ("Last login", "Never")]):
        y = 470 + i * 28
        body.append(text(880, y, k, 13, color=MUTED))
        body += status_active(1084, y - 15) if v is None else [text(1144, y, v, 13, 600, AMBER if v == "Not set yet" else INK, "end")]
    body += [hline(872, 1152, 630), text(880, 652, "This author can", 12, 600, MUTED)]
    for i, perm in enumerate(["See their own books", "Add new books", "Delete their own books"]):
        y = 674 + i * 19
        body += [circle(886, y - 4, 6, BRAND), f'<path d="M883.3 {y - 4}l1.9 1.9 3.4-3.4" fill="none" stroke="#ffffff" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>',
                 text(900, y, perm, 13)]
    body += button(872, 734, 152, "Set password", False, "key", 34) + button(1032, 734, 120, "Deactivate", False, "lock", 34, danger=True)
    write("admin-author-access.svg", "aaa-title", "Author Access", "admin", {"access"}, body)


# ---------------------------------------------------------------- author: my books (landing after author login)
def author_root():
    return full_name(LOGIN_AUTHOR)


def book_card(x, y, t, role_line):
    auths = title_authors(t["title_id"])
    out = [f"<!-- book: {t['title_id']} -->", rect(x, y, 456, 360, 14, stroke=LINE), rect(x + 20, y + 20, 150, 210, 12, MINT)]
    out += cover(x + 43, y + 40, t, author_line=(byline(auths[0]) if auths else ""))
    out += [text(x + 190, y + 46, clip(t["title"], 26), 17, 700), text(x + 190, y + 68, t["title_id"], 12, 600, MUTED)]
    pub = PUB.get(t["pub_id"], {}).get("pub_name", "–")
    for j, (k, v) in enumerate([("Type", ttype(t)), ("Publisher", pub), ("Price", money(t["price"])),
                                ("Published", fmt_date(t["pubdate"])), ("YTD sales", f'{t["ytd_sales"]:,}' if t["ytd_sales"] is not None else "–")]):
        ry = y + 104 + j * 24
        out += [text(x + 190, ry, k, 13, color=MUTED), text(x + 432, ry, v, 13, 600, anchor="end")]
    if t["notes"]:
        out += [text(x + 190, y + 226, clip(f'“{t["notes"]}”', 36), 12, color=MUTED, extra=' font-style="italic"')]
    out += [hline(x + 20, x + 436, y + 250), text(x + 20, y + 278, "Role", 12, color=MUTED), text(x + 60, y + 278, role_line, 12, 600)]
    out += button(x + 20, y + 304, 120, "Edit", False, "edit") + button(x + 152, y + 304, 120, "Delete", False, "trash", danger=True)
    return out


def my_books(confirm_delete=False):
    """The author's home screen; with confirm_delete it also shows the 'Delete this book?' dialog on top."""
    books = [TITLES[r["title_id"]] for r in LOGIN_BOOKS]
    sold = sum(s["qty"] for s in SALES if s["title_id"] in {b["title_id"] for b in books})
    ytd = [b["ytd_sales"] for b in books if b["ytd_sales"] is not None]
    pubs = sorted({PUB[b["pub_id"]]["pub_name"] for b in books})
    body = heading("My Books", "My Books", "Books where you are listed as an author. You can add new books or delete your own.", author_root(), len(books))
    body += select(240, 172, 200, "All types") + select(452, 172, 200, "Newest first") + button(1040, 172, 128, "Add Book")
    body += stat_tiles(224, [
        ("library", "My books", str(len(books)), "you are the only author" if all(len(title_authors(b["title_id"])) == 1 for b in books) else "including co-authored"),
        ("units", "Units sold", f"{sold:,}", "from the sales table" if sold else "no sales recorded yet"),
        ("money", "YTD sales", f"{sum(ytd):,}" if ytd else "–", "titles.ytd_sales" if ytd else "not reported yet"),
        ("publisher", "Publishers", str(len(pubs)), clip(", ".join(pubs), 30)),
    ])
    for i, b in enumerate(books[:2]):
        r = next(r for r in LOGIN_BOOKS if r["title_id"] == b["title_id"])
        n = len(title_authors(b["title_id"]))
        who = "Sole author" if n == 1 else f"Author {r['au_ord']} of {n}"
        role = f"{who} · {r['royaltyper']}% royalty share"
        body += book_card(240 + i * 472, 328, b, role)
    if len(books) < 2:
        x, y = 712, 328
        body += ["<!-- add a book -->", rect(x, y, 456, 360, 14, BG, SOFT, ' stroke-width="1.5" stroke-dasharray="6 5"'),
                 circle(x + 228, y + 130, 30, MINT), icon("plus", x + 216, y + 118, BRAND, 1.2),
                 text(x + 228, y + 190, "Add a new book", 16, 700, anchor="middle"),
                 text(x + 228, y + 212, "It will be linked to you as the author.", 13, color=MUTED, anchor="middle")]
        body += button(x + 164, y + 236, 128, "Add Book")
    body += [rect(240, 704, 928, 56, 12, AMBER_BG), icon("info", 260, 722, AMBER, .9),
             text(288, 728, "Deleting a book removes it from your list and the library. Its original records are kept for the admin.", 13, 600, AMBER),
             text(288, 746, "You will be asked to confirm first. Only the admin can bring a deleted book back.", 12, color=AMBER)]
    if not confirm_delete:
        write("author-my-books.svg", "amb-title", "My Books", "author", {"library"}, body)
        return
    b = books[0]
    x, y, w, h = DELETE_DIALOG
    body += ["<!-- delete confirmation -->", '<rect width="1200" height="800" fill="#1f2a24" opacity=".45"/>',
             rect(x, y, w, h, 16, extra=' style="filter: drop-shadow(0 12px 24px rgba(0,0,0,.18))"'),
             circle(x + 48, y + 52, 24, "#f9ebe7"), icon("trash", x + 38, y + 42, RED),
             text(x + 88, y + 48, f"Delete “{clip(b['title'], 28)}”?", 18, 700), text(x + 88, y + 70, b["title_id"], 12, 600, MUTED)]
    for i, ln in enumerate(["It is removed from your list and from the library.",
                            "Its sales and royalty records are kept, and the admin",
                            "can restore the book from Removed books."]):
        body.append(text(x + 32, y + 116 + i * 22, ln, 14, color=INK if i == 0 else MUTED))
    body += [hline(x, x + w, y + h - 68)]
    body += button(*DELETE_CANCEL[:3], "Cancel", False, None, DELETE_CANCEL[3])
    body += [rect(*DELETE_OK, 8, RED), text(DELETE_OK[0] + DELETE_OK[2] / 2, DELETE_OK[1] + 23, "Delete book", 14, 600, "#ffffff", "middle")]
    write("author-delete-confirm.svg", "adl-title", "Delete Book", "author", {"library"}, body)


DELETE_DIALOG = (380, 260, 480, 250)
DELETE_CANCEL = (380 + 480 - 268, 260 + 250 - 52, 112, 36)
DELETE_OK = (380 + 480 - 144, 260 + 250 - 52, 120, 36)
# my_books() buttons the walkthrough clicks: (x, y, w, h)
MYBOOKS_EDIT, MYBOOKS_DELETE = (260, 632, 120, 36), (392, 632, 120, 36)
MYBOOKS_ADD_TOP, MYBOOKS_ADD_CARD = (1040, 172, 128, 36), (876, 564, 128, 36)


# ---------------------------------------------------------------- forms
def field(x, y, w, label, value=None, placeholder=None, required=False, h=42, ic=None, focus=False, locked=False):
    lab = f'<text x="{x}" y="{y}" font-size="13" font-weight="600" fill="{INK}">{esc(label)}' + (f'<tspan fill="#c0533a"> *</tspan>' if required else "") + "</text>"
    out = [lab, rect(x, y + 10, w, h, 9, "#ffffff" if focus else FIELD, BRAND if focus else LINE, ' stroke-width="1.6"' if focus else "")]
    if value is not None:
        out.append(text(x + 14, y + 36, value, 14))
    elif placeholder:
        out.append(text(x + 14, y + 36, placeholder, 14, color=MUTED))
    if focus:
        out.append(f'<path d="M{x + 16 + len(value) * 6.2:.0f} {y + 23}v16" stroke="{BRAND}" stroke-width="1.5"/>')
    if ic:
        out.append(icon(ic, x + w - 32, y + 21, MUTED, .9))
    return out


# ---------------------------------------------------------------- author: profile
def profile():
    a = LOGIN_AUTHOR
    books = [TITLES[r["title_id"]] for r in LOGIN_BOOKS]
    sold = sum(s["qty"] for s in SALES if s["title_id"] in {b["title_id"] for b in books})
    share = LOGIN_BOOKS[0]["royaltyper"] if LOGIN_BOOKS else 0
    body = heading("My Profile", "My Profile", "Your details as stored in the authors table. Ask your admin to change them.", author_root())
    body += ["<!-- summary card -->", '<defs><clipPath id="pf-card"><rect x="240" y="174" width="300" height="560" rx="14"/></clipPath></defs>',
             rect(240, 174, 300, 560, 14), rect(240, 174, 300, 90, 0, MINT, extra=' clip-path="url(#pf-card)"'),
             f'<path d="M240 244c60-26 120 10 180-8s90-30 120-10v38H240z" fill="{SOFT}" opacity=".45" clip-path="url(#pf-card)"/>',
             rect(240, 174, 300, 560, 14, "none", LINE), circle(390, 264, 52, "#ffffff"), circle(390, 264, 47, BRAND),
             text(390, 275, initials(full_name(a)), 30, 700, "#ffffff", "middle", ' letter-spacing="1"'),
             text(390, 344, full_name(a), 20, 700, anchor="middle"), text(390, 366, f"@{LOGIN_USERNAME} · Author", 13, color=MUTED, anchor="middle")]
    body += pill(390, 380, "Contract signed" if a["contract"] else "No contract", MINT if a["contract"] else "#f6e3de", BRAND if a["contract"] else RED, 12, 12)
    body += [text(390, 436, f'Author ID {a["au_id"]}', 13, color=MUTED, anchor="middle"),
             text(390, 456, f'{a["city"]}, {a["state"]}', 13, color=MUTED, anchor="middle"), hline(264, 516, 492)]
    for i, (v, label) in enumerate([(len(books), "Books"), (sold, "Units sold"), (f"{share}%", "Royalty share")]):
        cx = 290 + i * 100
        body += [text(cx, 526, v, 20, 700, anchor="middle"), text(cx, 546, label, 12, color=MUTED, anchor="middle")]
        if i:
            body.append(vline(cx - 50, 508, 552))
    body += [hline(264, 516, 568), icon("cal", 264, 588, MUTED, .8), text(288, 601, "Login created", 13, color=MUTED), text(516, 601, CREATED, 13, 600, anchor="end"),
             icon("key", 264, 618, MUTED, .8), text(288, 631, "Last login", 13, color=MUTED), text(516, 631, "Never", 13, 600, anchor="end")]
    body += button(264, 666, 252, "Change password", False, "key", 40)
    body += ["<!-- personal information -->", rect(560, 174, 608, 282, 14, stroke=LINE), text(588, 206, "Personal information", 15, 700),
             icon("lock", 1016, 194, MUTED, .8), text(1144, 206, "Managed by admin", 12, 600, MUTED, "end"), hline(560, 1168, 222)]
    fields = [("person", "First name", a["au_fname"]), ("person", "Last name", a["au_lname"]),
              ("idcard", "Author ID", a["au_id"]), ("phone", "Phone", a["phone"]),
              ("homeaddr", "Address", a["address"]), ("pin", "City", a["city"]),
              ("map", "State", a["state"]), ("mail", "ZIP code", a["zip"])]
    for i, (ic, label, value) in enumerate(fields):
        x, y = 588 + (i % 2) * 290, 240 + (i // 2) * 52
        body += [rect(x, y, 32, 32, 8, BG), icon(ic, x + 7, y + 7, BRAND, .9), text(x + 44, y + 12, label, 12, color=MUTED), text(x + 44, y + 32, value or "–", 14, 600)]
    body += ["<!-- books + login -->", rect(560, 472, 608, 262, 14, stroke=LINE), vline(864, 492, 714),
             text(588, 504, "My books", 15, 700), text(840, 504, "View all →", 13, 600, BRAND, "end")]
    for i, b in enumerate(books[:2]):
        y = 524 + i * 96
        r = LOGIN_BOOKS[i]
        body += [rect(588, y, 252, 84, 10, FIELD, LINE), rect(600, y + 12, 40, 60, 3, COVER.get(b["type"].strip(), COVER["UNDECIDED"])[0]),
                 text(652, y + 28, clip(b["title"], 22), 14, 700), text(652, y + 46, f'{b["title_id"]} · {ttype(b)}', 12, color=MUTED),
                 text(652, y + 64, f'{PUB[b["pub_id"]]["pub_name"]} · {r["royaltyper"]}%', 12, color=MUTED)]
    body += [text(892, 504, "Login", 15, 700)]
    for i, (k, v) in enumerate([("Username", LOGIN_USERNAME), ("Role", "Author"), ("Status", None), ("Can", "Add and delete own books"),
                                ("Created by", "Admin")]):
        y = 540 + i * 34
        body.append(text(892, y, k, 13, color=MUTED))
        body += status_active(1080, y - 15) if v is None else [text(1140, y, v, 13, 600, anchor="end")]
    write("profile.svg", "pf-title", "My Profile", "author", {"person"}, body)



# ---------------------------------------------------------------- settings (both roles)
SET_LEFT, SET_RIGHT = (240, 452), (708, 460)          # (x, width) of the two columns
SET_THEMES = [("light", "Light", "sun"), ("dark", "Dark", "moon"), ("system", "Match system", "monitor")]
SET_THEME_BOX = (128, 104, 10)                        # width, height, gap
SET_THEME_Y = 244


def set_card(x, w, y, h, ic, title, meta=None):
    out = [rect(x, y, w, h, 14, stroke=LINE), rect(x + 24, y + 16, 32, 32, 8, BG), icon(ic, x + 30, y + 22, BRAND, .9),
           text(x + 68, y + 38, title, 15, 700)]
    if meta:
        out.append(text(x + 80 + len(title) * 9.4, y + 38, meta, 12, color=MUTED))
    return out


def set_row(x, w, y, h, label, note=None, value=None):
    out = [hline(x, x + w, y), text(x + 24, y + (h / 2 - 2 if note else h / 2 + 5), label, 14, 600)]
    if note:
        out.append(text(x + 24, y + h / 2 + 16, note, 12, color=MUTED))
    if value is not None:
        out.append(text(x + w - 24, y + h / 2 + 5, value, 13, 600, anchor="end"))
    return out


def segmented(right, y, labels, current, bw):
    w = len(labels) * bw + 6
    x = right - w
    out = [rect(x, y, w, 36, 10, FIELD, LINE)]
    for i, lbl in enumerate(labels):
        bx = x + 3 + i * bw
        if lbl == current:
            out.append(rect(bx, y + 3, bw, 30, 7, "#ffffff", LINE))
        out.append(text(bx + bw / 2, y + 23, lbl, 13, 600, BRAND if lbl == current else MUTED, "middle"))
    return out


def theme_box(x, y, kind, label, ic, on):
    w, h, _ = SET_THEME_BOX
    page, side = {"light": (BG, "#ffffff"), "dark": ("#0f1512", "#161d19"), "system": (None, "#ffffff")}[kind]
    out = [rect(x, y, w, h, 12, "#ffffff", BRAND if on else LINE, ' stroke-width="2"' if on else ' stroke-width="1.5"')]
    mx, my, mw, mh = x + 10, y + 10, w - 20, 50
    if kind == "system":
        out += [rect(mx, my, mw / 2, mh, 0, BG), rect(mx + mw / 2, my, mw / 2, mh, 0, "#0f1512")]
    else:
        out.append(rect(mx, my, mw, mh, 0, page))
    out += [rect(mx, my, mw * .26, mh, 0, side), rect(mx + mw * .26 + 10, my + 12, mw * .74 - 20, 9, 4, "#6cc39a", extra=' opacity=".75"'),
            rect(mx, my, mw, mh, 8, "none", "#cfd6d2"), icon(ic, x + 10, y + 72, BRAND if on else INK, .8),
            text(x + 32, y + 86, label, 12, 700 if on else 600, BRAND if on else INK)]
    return out


def settings_screen(role):
    admin = role == "admin"
    lx, lw = SET_LEFT
    rx, rw = SET_RIGHT
    body = heading("Settings", "Settings", "How the library looks for you, and your sign-in.", "Admin" if admin else author_root())
    body += ["<!-- appearance -->"] + set_card(lx, lw, 180, 258, "sun", "Appearance", "Saved in this browser")
    bw, bh, gap = SET_THEME_BOX
    for i, (kind, label, ic) in enumerate(SET_THEMES):
        body += theme_box(lx + 24 + i * (bw + gap), SET_THEME_Y, kind, label, ic, kind == "light")
    body += set_row(lx, lw, 370, 68, "Density", "Fits more rows on screen.")
    body += segmented(lx + lw - 24, 386, ["Comfortable", "Compact"], "Comfortable", 100)
    if admin:
        body += ["<!-- tables -->"] + set_card(lx, lw, 454, 124, "table", "Tables", "Saved in this browser")
        body += set_row(lx, lw, 506, 72, "Rows per page", "Used by Sales and Employees.")
        body += segmented(lx + lw - 24, 524, ["8", "15", "25", "50"], "8", 44)
    else:
        body += [rect(lx, 454, lw, 64, 12, BG), icon("info", lx + 18, 476, BRAND, .9),
                 text(lx + 46, 482, "Theme and density are kept in this browser, so", 13, color=BRAND),
                 text(lx + 46, 500, "each computer you use can look the way you like.", 13, color=BRAND)]

    name = "admin" if admin else LOGIN_USERNAME
    body += ["<!-- account -->"] + set_card(rx, rw, 180, 292, "shield", "Account and security")
    rows = [("Username", None, f"@{name}"), ("Role", None, "Admin" if admin else "Author"), ("Login created", None, CREATED)]
    for i, (label, note, value) in enumerate(rows):
        body += set_row(rx, rw, 232 + i * 48, 48, label, note, value)
    body += set_row(rx, rw, 376, 48, "Password")
    body += button(rx + rw - 24 - 184, 382, 184, "Change password", False, "key", 36)
    body += set_row(rx, rw, 424, 48, "Signed-in browsers", "Only this browser.")
    body += button(rx + rw - 24 - 232, 430, 232, "Sign out other browsers", False, "logout", 36)
    if admin:
        body += ["<!-- system -->"] + set_card(rx, rw, 488, 252, "database", "System", "Read-only")
        sysrows = [("Database", "pubs_b001s03 · pubs RDS"), ("Saving mode", None), ("Sign-in length", "12 hours · 30 days remembered"),
                   ("Wrong-password lockout", "5 tries, then 15 minutes"), ("Minimum password length", "10 characters")]
        for i, (label, value) in enumerate(sysrows):
            body += set_row(rx, rw, 540 + i * 40, 40, label, None, value)
        body += pill(rx + rw - 24 - 38, 549 + 40, "Practice", AMBER_BG, AMBER, 11, 10)
    write("admin-settings.svg" if admin else "author-settings.svg", "st-title", "Settings", role, {"settings"}, body)


# ---------------------------------------------------------------- admin: sales, discounts, publishers, employees (one tab each)
def back_and_heading(title, sub, count=None):
    return heading(title, title, sub, count=count)


def admin_sales():
    body = back_and_heading("Sales", "Every order line placed by a store, newest first.", len(SALES))
    body += select(240, 172, 250, "All stores", "store") + select(502, 172, 190, "All pay terms") + select(704, 172, 170, "All dates", "cal")
    body += button(1040, 172, 128, "New order")
    stores_selling = len({s["stor_id"] for s in SALES})
    body += stat_tiles(224, [
        ("sales", "Orders", str(ORDERS), f"{len(SALES)} order lines"),
        ("units", "Units sold", f"{UNITS:,}", f"{UNITS / ORDERS:.1f} units per order"),
        ("money", "Revenue", f"${REVENUE:,.2f}", "qty × title price"),
        ("store", "Stores selling", str(stores_selling), f"of {len(STORES)} stores"),
    ])
    price = lambda r: TITLES[r["title_id"]]["price"]
    cols = [(C_ORDER, 264), (C_DATE, 350), (C_STORE(22), 456), (C_TITLE(25), 632),
            (C_QTY, 860),
            (lambda r, x, y: [text(x, y, money(price(r)) if price(r) is not None else "–", 13, color=MUTED, anchor="end")], 950),
            (lambda r, x, y: [text(x, y, f"${r['qty'] * price(r):,.2f}" if price(r) is not None else "–", 13, 700, anchor="end")], 1050),
            (C_TERMS, 1110)]
    body += ["<!-- sales table -->", rect(240, 328, 928, 452, 14, stroke=LINE), text(264, 360, "Order lines", 15, 700),
             text(368, 360, "joined to titles for the price", 13, color=MUTED)]
    body += table_head(240, 928, 378, [("Order #", 264, None), ("Date", 350, None), ("Store", 456, None), ("Title", 632, None),
                                        ("Qty", 860, "end"), ("Price", 950, "end"), ("Amount", 1050, "end"), ("Terms", 1110, "middle")])
    body += sales_rows(RECENT[:8], cols, 240, 928, 410)
    pages = -(-len(SALES) // 8)
    body += [text(264, 762, f"Showing 1–8 of {len(SALES)}", 13, color=MUTED)] + pager(1150 - (pages + 2) * 34, 742, ["‹"] + [str(n) for n in range(1, pages + 1)] + ["›"])
    write("admin-sales.svg", "asl-title", "Sales", "admin", {"sales"}, body)


def admin_discounts():
    store_rules = [d for d in DISCOUNTS if d["stor_id"]]
    body = back_and_heading("Discounts", "Discount rules. A rule with no store applies to every store.", len(DISCOUNTS))
    body += select(240, 172, 250, "All stores", "store") + select(502, 172, 190, "All discount types") + button(1024, 172, 144, "New discount")
    best = max(DISCOUNTS, key=lambda d: d["discount"])
    body += stat_tiles(224, [
        ("discount", "Discount rules", str(len(DISCOUNTS)), f"{len({d['discounttype'] for d in DISCOUNTS})} discount types"),
        ("store", "Store-specific", str(len(store_rules)), f"{len(DISCOUNTS) - len(store_rules)} apply to all stores"),
        ("units", "Quantity-based", str(sum(1 for d in DISCOUNTS if d["lowqty"] is not None)), "need a minimum order"),
        ("money", "Highest discount", pct(best["discount"]), best["discounttype"]),
    ])
    body += ["<!-- discount rules -->", rect(240, 328, 600, 452, 14, stroke=LINE), text(264, 360, "Discount rules", 15, 700)]
    body += table_head(240, 600, 378, [("Discount type", 264, None), ("Applies to", 448, None), ("Quantity", 614, None), ("Discount", 772, "end")])
    for i, d in enumerate(sorted(DISCOUNTS, key=lambda d: (d["discounttype"], -d["discount"]))):
        y = 410 + i * 48
        qty = f'{d["lowqty"]:,} – {d["highqty"]:,}' if d["lowqty"] is not None else "Any quantity"
        body += [rect(264, y + 12, 28, 28, 7, BG), icon("discount", 268, y + 16, BRAND, .8), text(302, y + 31, d["discounttype"], 13, 600),
                 text(448, y + 31, clip(store_name(d["stor_id"]), 18), 13, BRAND if d["stor_id"] else MUTED),
                 text(614, y + 31, qty, 13, color=MUTED), text(772, y + 31, pct(d["discount"]), 15, 700, BRAND, "end"),
                 icon("edit", 798, y + 17, MUTED, .8)]
        if i < len(DISCOUNTS) - 1:
            body.append(hline(256, 824, y + 47))
    body += ["<!-- stores with a discount -->", rect(856, 328, 312, 452, 14, stroke=LINE), text(880, 360, "Stores with their own discount", 15, 700), hline(856, 1168, 377)]
    for i, d in enumerate(store_rules):
        st = STORES[d["stor_id"]]
        y = 394 + i * 66
        body += [rect(880, y, 36, 36, 9, BG), icon("store", 888, y + 8, BRAND), text(928, y + 15, clip(st["stor_name"], 22), 14, 600),
                 text(928, y + 33, f'{st["city"]}, {st["state"]} · ID {st["stor_id"]}', 12, color=MUTED),
                 text(1144, y + 24, pct(d["discount"]), 16, 700, BRAND, "end")]
    y = 394 + len(store_rules) * 66 + 8
    body += [rect(872, y, 280, 76, 10, FIELD), icon("info", 886, y + 14, MUTED, .8),
             text(908, y + 27, f"The other {len(STORES) - len(store_rules)} stores only get", 12, color=MUTED),
             text(908, y + 45, "the all-store rules on the left.", 12, color=MUTED)]
    write("admin-discounts.svg", "adc-title", "Discounts", "admin", {"discount"}, body)


PUB_CARD = (296, 214)


def pub_card_pos(i):
    return 240 + (i % 3) * 316, 328 + (i // 3) * 232


def admin_publishers():
    body = back_and_heading("Publishers", "Publishers, their titles and how many people work for each.", len(PUBLISHERS))
    body += [rect(240, 172, 300, 36, 9, FIELD, LINE), icon("search", 252, 180, MUTED, .9), text(280, 195, "Search publishers", 14, color=MUTED)]
    body += select(552, 172, 200, "All countries") + button(1016, 172, 152, "Add publisher")
    prices = [t["price"] for t in TITLES.values() if t["price"] is not None]
    body += stat_tiles(224, [
        ("publisher", "Publishers", str(len(PUBLISHERS)), f"{len({p['country'] for p in PUBLISHERS})} country"),
        ("library", "Titles", str(len(TITLES)), f"{sum(1 for t in TITLES.values() if t['price'] is None)} not priced yet"),
        ("employee", "Employees", str(len(EMPLOYEES)), f"{len(EMPLOYEES) / len(PUBLISHERS):.1f} per publisher"),
        ("money", "Average price", f"${sum(prices) / len(prices):,.2f}", "across priced titles"),
    ])
    w, h = PUB_CARD
    for i, p in enumerate(PUBLISHERS):
        x, y = pub_card_pos(i)
        pid = p["pub_id"]
        loc = f'{p["city"]}, {p["state"]} · {p["country"]}' if p["state"] else f'{p["city"]} · {p["country"]}'
        body += [f"<!-- publisher {pid} -->", rect(x, y, w, h, 14, stroke=LINE), rect(x + 20, y + 20, 44, 44, 11, BRAND),
                 text(x + 42, y + 48, initials(p["pub_name"]), 15, 700, "#ffffff", "middle"),
                 text(x + 76, y + 38, clip(p["pub_name"], 22), 15, 700), text(x + 76, y + 57, f"{loc} · ID {pid}", 12, color=MUTED),
                 rect(x + 20, y + 80, w - 40, 44, 9, FIELD), text(x + 32, y + 98, "PR INFO", 10, 700, MUTED, extra=' letter-spacing="1"'),
                 text(x + 32, y + 115, clip(PUB_INFO.get(pid) or "No PR info yet.", 40), 12)]
        for j, (label, val) in enumerate([("Titles", TITLE_COUNT[pid]), ("Employees", STAFF[pid])]):
            sx = x + 20 + j * 92
            body += [text(sx, y + 154, val, 18, 700), text(sx, y + 172, label, 12, color=MUTED)]
        body += [hline(x + 20, x + w - 20, y + 184), text(x + w - 20, y + 205, "View employees →", 12, 600, BRAND, "end"),
                 icon("edit", x + 20, y + 191, MUTED, .75), text(x + 42, y + 205, "Edit", 12, 600, MUTED)]
    write("admin-publishers.svg", "apb-title", "Publishers", "admin", {"publisher"}, body)


def admin_employees():
    staff = sorted(EMPLOYEES, key=lambda e: (e["lname"], e["fname"]))
    bad = [e for e in staff if not JOBS[e["job_id"]]["min_lvl"] <= e["job_lvl"] <= JOBS[e["job_id"]]["max_lvl"]]
    body = back_and_heading("Employees", "Everyone who works for a publisher, with their job and level.", len(EMPLOYEES))
    body += [rect(240, 172, 260, 36, 9, FIELD, LINE), icon("search", 252, 180, MUTED, .9), text(280, 195, "Search employees", 14, color=MUTED)]
    body += select(512, 172, 200, "All publishers", "publisher") + select(724, 172, 160, "All jobs") + button(1016, 172, 152, "Add employee")
    body += stat_tiles(224, [
        ("employee", "Employees", str(len(EMPLOYEES)), f"at {len(STAFF)} publishers"),
        ("idcard", "Jobs in use", str(len({e['job_id'] for e in EMPLOYEES})), f"of {len(JOBS)} jobs defined"),
        ("cal", "Newest hire", fmt_date(max(e["hire_date"] for e in EMPLOYEES), True), "by hire date"),
        ("info", "Level out of range", str(len(bad)), "job_lvl outside min–max"),
    ])
    body += ["<!-- employee table -->", rect(240, 328, 928, 452, 14, stroke=LINE), text(264, 360, "All employees", 15, 700),
             text(378, 360, "sorted by last name", 13, color=MUTED)]
    body += table_head(240, 928, 378, [("Name", 264, None), ("Emp ID", 470, None), ("Publisher", 572, None), ("Job", 742, None),
                                        ("Level (job range)", 920, None), ("Hired", 1144, "end")])
    for i, e in enumerate(staff[:8]):
        y = 410 + i * 38
        j = JOBS[e["job_id"]]
        name = f'{e["fname"]} {e["lname"]}'
        inside = j["min_lvl"] <= e["job_lvl"] <= j["max_lvl"]
        span = j["max_lvl"] - j["min_lvl"]
        bx, bw = 920, 120
        mx = bx + bw * min(max((e["job_lvl"] - j["min_lvl"]) / span if span else 1, 0), 1)
        mark = BRAND if inside else RED
        body += [circle(276, y + 19, 12, MINT), text(276, y + 23, initials(name), 10, 700, BRAND, "middle"), text(296, y + 24, clip(name, 20), 13, 600),
                 text(470, y + 24, e["emp_id"], 12, color=MUTED), text(572, y + 24, clip(PUB[e["pub_id"]]["pub_name"], 20), 13),
                 text(742, y + 24, clip(j["job_desc"], 22), 12),
                 rect(bx, y + 17, bw, 4, 2, LINE), rect(bx, y + 17, round(mx - bx, 1), 4, 2, SOFT),
                 circle(round(mx, 1), y + 19, 5, mark, ' stroke="#ffffff" stroke-width="2"'),
                 text(bx + bw + 10, y + 24, e["job_lvl"], 12, 700, mark), text(1144, y + 24, fmt_date(e["hire_date"], True), 12, color=MUTED, anchor="end")]
        if i < 7:
            body.append(hline(256, 1152, y + 37))
    pages = -(-len(staff) // 8)
    body += [text(264, 762, f"Showing 1–8 of {len(staff)} · red levels are outside the job’s range", 13, color=MUTED)]
    body += pager(1150 - (pages + 2) * 34, 742, ["‹"] + [str(n) for n in range(1, pages + 1)] + ["›"])
    write("admin-employees.svg", "aem-title", "Employees", "admin", {"employee"}, body)


# ---------------------------------------------------------------- forms
# Every form registers its clickable fields here, so the walkthrough fills exactly what is drawn.
# screen -> {"fields": {key: [x, y, w, h, icon]}, "demo": {key: value}, "required": [...], "extras": [(needs, svg)], ...}
FORMS = {}
PANEL = (836, 190, 308, 466)


def form_start(screen, title, crumb, sub, root, done, toast):
    FORMS[screen] = {"fields": {}, "demo": {}, "required": [], "extras": [], "done": done, "toast": toast}
    return FORMS[screen], heading(title, crumb, sub, root) + [rect(240, 174, 928, 560, 14, stroke=LINE)]


def ffield(spec, body, key, x, y, w, label, placeholder=None, demo=None, required=False, ic=None, h=42, value=None):
    """Draw a field; if it has a demo value, the walkthrough fills it on click."""
    body += field(x, y, w, label, value=value, placeholder=placeholder, required=required, h=h, ic=ic)
    if demo is not None:
        spec["fields"][key] = [x, y + 10, w, h, ic]
        spec["demo"][key] = demo
        if required:
            spec["required"].append(key)


def form_end(spec, body, save_label, cancel_go):
    sw = max(94, round(len(save_label) * 8.2 + 28))
    sx = 1140 - sw
    body += [hline(240, 1168, 672), icon("info", 266, 692, MUTED, .8), text(292, 707, "Fields marked * are required.", 13, color=MUTED)]
    body += button(sx - 108, 688, 96, "Cancel", False, None, 32) + button(sx, 688, sw, save_label, True, None, 32)
    spec.update(save=[sx, 688, sw, 32], cancel=[sx - 108, 688, 96, 32], cancel_go=cancel_go)


def side_panel(body, title, lines, top=218):
    x, y, w, h = PANEL
    body += [rect(x, y, w, h, 12, FIELD), text(x + 20, top, title, 14, 700)]
    body += [text(x + 20, top + 26 + i * 20, ln, 12, color=MUTED) for i, ln in enumerate(lines)]


def panel_overlay(spec, needs, parts):
    """Panel content that appears once every field in `needs` is filled."""
    x, y, w, h = PANEL
    spec["extras"].append((needs, rect(x, y, w, h, 12, FIELD) + "".join(parts)))


def wrap(s, n):
    words, lines = s.split(), [""]
    for wd in words:
        if len(lines[-1]) + len(wd) + 1 > n and lines[-1]:
            lines.append(wd)
        else:
            lines[-1] = (lines[-1] + " " + wd).strip()
    return lines


# ---------------------------------------------------------------- forms: add / edit book
ADD_BOOK_FIELDS = {"title": (500, 206, 640), "title_id": (500, 282, 310), "type": (830, 282, 310), "publisher": (500, 358, 310),
                   "pubdate": (830, 358, 310), "price": (500, 434, 200), "advance": (720, 434, 200), "royalty": (940, 434, 200)}
DEMO_BOOK = {"title": "Practical Data Pipelines", "title_id": "PC1001", "type": "Popular computing", "publisher": "Algodata Infosystems",
             "pubdate": "2026-11-01", "price": "24.99", "advance": "4000", "royalty": "12",
             "notes": "A hands-on guide to building reliable data pipelines.", "author": "Charlene Locksley"}


def cover_box(body, t, author_line, caption):
    body += ["<!-- cover preview -->", text(268, 206, "Cover preview", 13, 600),
             rect(268, 216, 200, 270, 12, BG, SOFT, ' stroke-width="1.5" stroke-dasharray="6 5"')]
    body += cover(316, 244, t, author_line=author_line)
    body += [text(368, 446 + i * 18, ln, 12, color=MUTED, anchor="middle") for i, ln in enumerate(caption)]


def saved_box(body, lines):
    body += [rect(268, 510, 200, 120, 12, FIELD), icon("info", 282, 524, MUTED, .8), text(304, 537, "Where this is saved", 12, 600)]
    body += [text(284, 562 + i * 20, ln, 12, color=MUTED) for i, ln in enumerate(lines)]


def book_form(role):
    admin = role == "admin"
    a = LOGIN_AUTHOR
    screen = "admin-add-book" if admin else "add-book"
    done = "dashboard" if admin else "author-my-books"
    spec, body = form_start(screen, "Add New Book", "Add Book",
                            "Fill in the details below and pick the book’s author." if admin else "Fill in the details below. The book will be linked to you as its author.",
                            "Admin" if admin else author_root(), done, f"“{DEMO_BOOK['title']}” saved (demo only, nothing written to the database).")
    cover_box(body, {"title": "Your book title", "type": "UNDECIDED"}, "" if admin else byline(a), ["Made from the title and type,", "no upload needed"])
    saved_box(body, ["Book details → titles", "Author link → titleauthor" if admin else "You as author → titleauthor", "Order 1, 100% royalty share"])
    labels = {"title": ("Book title", "e.g. Practical Data Pipelines", True, None), "title_id": ("Title ID", "6 characters, e.g. PC1001", True, None),
              "type": ("Type", "Select a type", True, "chev"), "publisher": ("Publisher", "Select a publisher", True, "chev"),
              "pubdate": ("Publication date", "YYYY-MM-DD", False, "cal"), "price": ("Price ($)", "e.g. 19.99", False, None),
              "advance": ("Advance ($)", "e.g. 5000", False, None), "royalty": ("Royalty (%)", "e.g. 10", False, None)}
    for key, (x, y, w) in ADD_BOOK_FIELDS.items():
        label, ph, req, ic = labels[key]
        ffield(spec, body, key, x, y, w, label, ph, DEMO_BOOK[key], req, ic)
    ffield(spec, body, "notes", 500, 510, 640, "Notes", "A short summary of the book…", DEMO_BOOK["notes"], h=56)
    if admin:
        ffield(spec, body, "author", 500, 600, 310, "Author", "Select an author", DEMO_BOOK["author"], True, "chev")
        body += [text(830, 628, "Linked as author 1 with a", 12, color=MUTED), text(830, 646, "100% royalty share.", 12, color=MUTED)]
    else:
        body += [text(500, 600, "Author", 13, 600), rect(500, 610, 310, 42, 9, FIELD, LINE), circle(526, 631, 12, BRAND),
                 text(526, 635, initials(full_name(a)), 10, 700, "#ffffff", "middle"), text(546, 636, f"{full_name(a)} (you)", 14, 600),
                 icon("lock", 778, 621, MUTED, .9), text(830, 628, "Added automatically, order 1", 12, color=MUTED),
                 text(830, 646, "with a 100% royalty share.", 12, color=MUTED)]
    filled = {"title": DEMO_BOOK["title"], "type": "popular_comp"}
    spec["extras"].append((["title"], rect(310, 238, 116, 166, 6, BG) + "".join(cover(316, 244, filled, author_line="" if admin else byline(a)))))
    if admin:
        spec["extras"].append((["title", "author"], text(372, 382, byline(a), 8, 600, "#f2c66d", "middle", ' letter-spacing="1" opacity=".85"')))
    form_end(spec, body, "Save Book", done)
    write(f"{screen}.svg", f"{screen}-title", "Add New Book", role, {"library"} if admin else {"plus"}, body)


def edit_book():
    t = TITLES[LOGIN_BOOKS[0]["title_id"]]
    spec, body = form_start("author-edit-book", "Edit Book", "Edit Book", f"Change the details of “{t['title']}”. The title ID can’t be changed.",
                            author_root(), "author-my-books", "Changes saved (demo only, nothing written to the database).")
    cover_box(body, t, byline(LOGIN_AUTHOR), ["The cover follows the title", "and type"])
    saved_box(body, [f"Changes → titles ({t['title_id']})", "Authors stay in titleauthor", "Nothing else changes"])
    body += field(500, 206, 640, "Book title", t["title"], required=True)
    body += field(500, 282, 310, "Title ID", t["title_id"], ic="lock")
    body += field(830, 282, 310, "Type", ttype(t), required=True, ic="chev")
    body += field(500, 358, 310, "Publisher", PUB[t["pub_id"]]["pub_name"], required=True, ic="chev")
    body += field(830, 358, 310, "Publication date", t["pubdate"], ic="cal")
    for key, x, label, cur, demo in [("price", 500, "Price ($)", t["price"], "14.99"), ("advance", 720, "Advance ($)", t["advance"], "2000"),
                                     ("royalty", 940, "Royalty (%)", t["royalty"], "10")]:
        ffield(spec, body, key, x, 434, 200, label, "Not set yet" if cur is None else None, demo, value=None if cur is None else str(cur))
    body += field(500, 510, 640, "Notes", t["notes"] or None, placeholder=None if t["notes"] else "A short summary of the book…", h=56)
    body += [text(500, 628, "Price, advance and royalty are empty in the database for this book.", 12, color=MUTED),
             text(500, 646, "Click them to fill in example values.", 12, color=MUTED)]
    form_end(spec, body, "Save changes", "author-my-books")
    write("author-edit-book.svg", "aeb-title", "Edit Book", "author", {"library"}, body)


# ---------------------------------------------------------------- forms: new order, new discount, add publisher, add employee
def next_order_number():
    nums = [int(s["ord_num"][1:]) for s in SALES if len(s["ord_num"]) == 5 and s["ord_num"][0] == "N" and s["ord_num"][1:].isdigit()]
    return f"N{max(nums, default=0) + 1:04d}"


def new_order():
    store = next(s for s in STORES.values() if s["stor_name"] == "Bookbeat")
    title = TITLES["BU1032"]
    qty = 120
    spec, body = form_start("admin-new-order", "New Order", "New Order", "Record an order line: one store, one title and a quantity.",
                            "Admin", "admin-sales", "Order saved (demo only, nothing written to the database).")
    ffield(spec, body, "store", 268, 206, 260, "Store", "Select a store", store["stor_name"], True, "chev")
    ffield(spec, body, "ord_num", 548, 206, 252, "Order number", f"e.g. {next_order_number()}", next_order_number(), True)
    ffield(spec, body, "title", 268, 282, 532, "Title", "Select a title", f'{title["title"]} ({title["title_id"]})', True, "chev")
    ffield(spec, body, "qty", 268, 358, 160, "Quantity", "e.g. 120", str(qty), True)
    ffield(spec, body, "ord_date", 448, 358, 170, "Order date", "YYYY-MM-DD", "2026-10-26", True, "cal")
    ffield(spec, body, "payterms", 638, 358, 162, "Pay terms", "Select terms", "Net 30", True, "chev")
    body += [rect(268, 440, 532, 64, 10, BG), icon("info", 282, 452, BRAND, .8)]
    body += [text(306, 465 + i * 18, ln, 12, color=BRAND) for i, ln in enumerate(
        ["An order can have several titles: save this line, then add the", "next title with the same store and order number."])]
    side_panel(body, "Order summary", ["Pick a store, a title and a quantity to see", "the amount and the discounts that could apply."])
    x, y, w, _ = PANEL
    rules = sorted((d for d in DISCOUNTS if d["stor_id"] in (None, store["stor_id"])
                    and (d["lowqty"] is None or d["lowqty"] <= qty) and (d["highqty"] is None or qty <= d["highqty"])), key=lambda d: -d["discount"])
    parts = [text(x + 20, 218, "Order summary", 14, 700)]
    for i, (k, v) in enumerate([("Unit price", money(title["price"])), ("Quantity", str(qty)), ("Amount", f"${qty * title['price']:,.2f}")]):
        parts += [text(x + 20, 252 + i * 26, k, 13, color=MUTED), text(x + w - 20, 252 + i * 26, v, 15 if k == "Amount" else 13, 700, BRAND if k == "Amount" else INK, "end")]
    parts += [hline(x + 20, x + w - 20, 320), text(x + 20, 346, "Discounts that could apply", 12, 700, MUTED)]
    for i, d in enumerate(rules[:5]):
        ry = 372 + i * 40
        parts += [text(x + 20, ry, d["discounttype"], 13, 600), text(x + 20, ry + 16, clip(disc_scope(d), 34), 11, color=MUTED),
                  text(x + w - 20, ry + 6, pct(d["discount"]), 14, 700, BRAND, "end")]
    parts += [text(x + 20, 590, "The sales table has no discount column,", 11, color=AMBER),
              text(x + 20, 606, "so the order itself stores no discount.", 11, color=AMBER)]
    panel_overlay(spec, ["store", "title", "qty"], parts)
    form_end(spec, body, "Save order", "admin-sales")
    write("admin-new-order.svg", "ano-title", "New Order", "admin", {"sales"}, body)


def new_discount():
    store = next(s for s in STORES.values() if s["stor_name"] == "Fricative Bookshop")
    spec, body = form_start("admin-new-discount", "New Discount", "New Discount", "Add a discount rule. Leave the store empty to apply it to every store.",
                            "Admin", "admin-discounts", "Discount saved (demo only, nothing written to the database).")
    ffield(spec, body, "dtype", 268, 206, 532, "Discount type", "Select a discount type", "Customer Discount", True, "chev")
    ffield(spec, body, "store", 268, 282, 532, "Store", "All stores (leave empty for every store)", store["stor_name"], False, "chev")
    ffield(spec, body, "lowqty", 268, 358, 160, "Low quantity", "Optional", "10")
    ffield(spec, body, "highqty", 448, 358, 170, "High quantity", "Optional", "200")
    ffield(spec, body, "discount", 638, 358, 162, "Discount (%)", "e.g. 4.5", "4.5", True)
    side_panel(body, "Checks before saving", ["The database rejects a rule when:", "", "• the discount is below 0",
                                               "• the low quantity is below 0", "• the high quantity is below the low one",
                                               "• the store does not exist in stores"])
    x, y, w, _ = PANEL
    has_own = any(d["stor_id"] == store["stor_id"] for d in DISCOUNTS)
    parts = [text(x + 20, 218, "Preview", 14, 700), rect(x + 20, 236, w - 40, 96, 10, "#ffffff", LINE),
             text(x + 36, 264, "Customer Discount", 14, 700), text(x + w - 36, 266, "4.5%", 18, 700, BRAND, "end"),
             text(x + 36, 286, f'{store["stor_name"]} only', 12, color=MUTED), text(x + 36, 306, "Orders of 10 – 200 copies", 12, color=MUTED),
             icon("check", x + 20, 354, BRAND, .9), text(x + 44, 368, "Passes every database check", 13, 600, BRAND),
             text(x + 20, 400, f'{store["stor_name"]} has {"a" if has_own else "no"} discount of its own yet.', 12, color=MUTED)]
    panel_overlay(spec, ["dtype", "store", "discount"], parts)
    form_end(spec, body, "Save discount", "admin-discounts")
    write("admin-new-discount.svg", "and-title", "New Discount", "admin", {"discount"}, body)


def add_publisher():
    demo = {"pub_id": "9004", "name": "Riverstone Books", "city": "Seattle", "state": "WA", "country": "USA",
            "pr": "Riverstone Books publishes practical guides for data teams."}
    spec, body = form_start("admin-add-publisher", "Add Publisher", "Add Publisher", "Add a publisher. The PR text is saved separately in pub_info.",
                            "Admin", "admin-publishers", f"“{demo['name']}” saved (demo only, nothing written to the database).")
    ffield(spec, body, "pub_id", 268, 206, 160, "Publisher ID", "e.g. 9004", demo["pub_id"], True)
    ffield(spec, body, "name", 448, 206, 352, "Name", "e.g. Riverstone Books", demo["name"], True)
    ffield(spec, body, "city", 268, 282, 260, "City", "e.g. Seattle", demo["city"])
    ffield(spec, body, "state", 548, 282, 80, "State", "WA", demo["state"])
    ffield(spec, body, "country", 648, 282, 152, "Country", "e.g. USA", demo["country"])
    ffield(spec, body, "pr", 268, 358, 532, "PR info", "Short description shown on the publisher card…", demo["pr"], h=96)
    body += [text(268, 500, f"Publisher IDs in use: {', '.join(sorted(PUB))}.", 12, color=MUTED)]
    side_panel(body, "Card preview", ["The new publisher appears like this", "on the Publishers tab."])
    x, y, w, _ = PANEL
    preview = [text(x + 20, 218, "Card preview", 14, 700), rect(x + 20, 236, w - 40, 170, 12, "#ffffff", LINE), rect(x + 36, 252, 40, 40, 10, BRAND),
               text(x + 56, 277, initials(demo["name"]), 14, 700, "#ffffff", "middle"), text(x + 88, 268, demo["name"], 14, 700),
               text(x + 88, 286, f'{demo["city"]}, {demo["state"]} · {demo["country"]} · ID {demo["pub_id"]}', 11, color=MUTED),
               rect(x + 36, 304, w - 72, 40, 8, FIELD), text(x + 46, 329, clip(demo["pr"], 38), 11),
               text(x + 36, 378, "0", 16, 700), text(x + 36, 394, "Titles", 11, color=MUTED), text(x + 116, 378, "0", 16, 700),
               text(x + 116, 394, "Employees", 11, color=MUTED)]
    panel_overlay(spec, ["name"], preview)
    form_end(spec, body, "Save publisher", "admin-publishers")
    write("admin-add-publisher.svg", "aap-title", "Add Publisher", "admin", {"publisher"}, body)


def add_employee():
    job = next(j for j in JOBS.values() if j["job_desc"] == "Editor")
    pub = next(p for p in PUBLISHERS if p["pub_name"] == "Data Press")
    level = 60
    spec, body = form_start("admin-add-employee", "Add Employee", "Add Employee", "Add someone who works for a publisher.",
                            "Admin", "admin-employees", "Employee saved (demo only, nothing written to the database).")
    ffield(spec, body, "emp_id", 268, 206, 260, "Employee ID", "3 letters, 5 digits, 1 letter", "KRR41027F", True)
    ffield(spec, body, "hire_date", 548, 206, 252, "Hire date", "YYYY-MM-DD", "2026-11-02", False, "cal")
    ffield(spec, body, "fname", 268, 282, 200, "First name", "e.g. Kavya", "Kavya", True)
    ffield(spec, body, "minit", 488, 282, 60, "M.I.", "–", "R")
    ffield(spec, body, "lname", 568, 282, 232, "Last name", "e.g. Rao", "Rao", True)
    ffield(spec, body, "publisher", 268, 358, 260, "Publisher", "Select a publisher", pub["pub_name"], True, "chev")
    ffield(spec, body, "job", 548, 358, 252, "Job", "Select a job", job["job_desc"], True, "chev")
    ffield(spec, body, "level", 268, 434, 160, "Job level", "e.g. 60", str(level), True)
    body += [text(448, 470, "The allowed range shows once a job is picked.", 12, color=MUTED)]
    spec["extras"].append((["job"], rect(444, 456, 356, 22, 0, "#ffffff") +
                           text(448, 470, f'{job["job_desc"]}: levels {job["min_lvl"]} – {job["max_lvl"]}', 12, 600, BRAND)))
    out_of_range = sum(1 for e in EMPLOYEES if not JOBS[e["job_id"]]["min_lvl"] <= e["job_lvl"] <= JOBS[e["job_id"]]["max_lvl"])
    side_panel(body, "Level check", ["Pick a job and a level to check that the", "level fits the job’s range.", "",
                                     "The database does not check this, which is", f"how {out_of_range} current employees ended up", "outside their job’s range."])
    x, y, w, _ = PANEL
    bx, bw = x + 20, w - 40
    mx = bx + bw * (level - job["min_lvl"]) / (job["max_lvl"] - job["min_lvl"])
    parts = [text(x + 20, 218, "Level check", 14, 700), rect(x + 20, 236, w - 40, 56, 10, MINT), icon("check", x + 32, 254, BRAND),
             text(x + 60, 260, f"{level} is inside the range", 13, 700, BRAND), text(x + 60, 278, f'{job["job_desc"]}: {job["min_lvl"]} – {job["max_lvl"]}', 12, color=BRAND),
             rect(bx, 320, bw, 6, 3, LINE), rect(bx, 320, round(mx - bx, 1), 6, 3, SOFT), circle(round(mx, 1), 323, 7, BRAND, ' stroke="#ffffff" stroke-width="2"'),
             text(bx, 346, job["min_lvl"], 11, color=MUTED), text(bx + bw, 346, job["max_lvl"], 11, color=MUTED, anchor="end"),
             text(x + 20, 386, "Employee ID format (checked by the database):", 12, 600), text(x + 20, 406, "first, middle and last initial, 5 digits,", 12, color=MUTED),
             text(x + 20, 424, "then M or F, e.g. KRR41027F.", 12, color=MUTED)]
    panel_overlay(spec, ["job", "level"], parts)
    form_end(spec, body, "Save employee", "admin-employees")
    write("admin-add-employee.svg", "aae-title", "Add Employee", "admin", {"employee"}, body)


# ---------------------------------------------------------------- admin: stores
def admin_stores():
    body = heading("Stores", "Stores", "Bookstores that place orders, with their sales and discounts.", count=len(STORES))
    body += [rect(240, 172, 300, 36, 9, FIELD, LINE), icon("search", 252, 180, MUTED, .9), text(280, 195, "Search stores", 14, color=MUTED)]
    body += select(552, 172, 200, "All states") + button(1032, 172, 136, "Add store")
    by_store = {sid: [s for s in SALES if s["stor_id"] == sid] for sid in STORES}
    revenue = lambda rows: sum(r["qty"] * TITLES[r["title_id"]]["price"] for r in rows if TITLES[r["title_id"]]["price"] is not None)
    top = max(STORES, key=lambda sid: revenue(by_store[sid]))
    body += stat_tiles(224, [
        ("store", "Stores", str(len(STORES)), f"in {len({s['state'] for s in STORES.values()})} states"),
        ("sales", "Orders", str(ORDERS), "placed by all stores"),
        ("money", "Top store revenue", f"${revenue(by_store[top]):,.2f}", STORES[top]["stor_name"]),
        ("discount", "Own discounts", str(STORE_DISCOUNTS), "stores with their own rule"),
    ])
    body += ["<!-- stores table -->", rect(240, 328, 928, 452, 14, stroke=LINE)]
    body += table_head(240, 928, 340, [("ID", 264, None), ("Store", 316, None), ("Address", 548, None), ("City", 714, None),
                                        ("Orders", 914, "end"), ("Units", 984, "end"), ("Revenue", 1084, "end"), ("Discount", 1144, "end")])
    own = {d["stor_id"]: d["discount"] for d in DISCOUNTS if d["stor_id"]}
    for i, (sid, st) in enumerate(sorted(STORES.items())):
        y = 372 + i * 40
        rows = by_store[sid]
        body += [text(264, y + 25, sid, 12, color=MUTED), text(316, y + 25, clip(st["stor_name"], 26), 13, 600),
                 text(548, y + 25, clip(st["stor_address"], 21), 13, color=MUTED), text(714, y + 25, f'{st["city"]}, {st["state"]} {st["zip"]}', 13),
                 text(914, y + 25, len({r["ord_num"] for r in rows}), 13, 600, anchor="end"), text(984, y + 25, sum(r["qty"] for r in rows), 13, anchor="end"),
                 text(1084, y + 25, f"${revenue(rows):,.2f}", 13, 600, anchor="end"),
                 text(1144, y + 25, pct(own[sid]) if sid in own else "–", 13, 700 if sid in own else None, BRAND if sid in own else MUTED, "end")]
        if i < len(STORES) - 1:
            body.append(hline(256, 1152, y + 39))
    write("admin-stores.svg", "ast-title", "Stores", "admin", {"store"}, body)


# ---------------------------------------------------------------- admin: title detail (one screen per book shown in the library)
TITLE_EDIT, TITLE_REMOVE = (264, 704, 120, 36), (396, 704, 120, 36)


def title_detail(t):
    tid = t["title_id"]
    pub = PUB.get(t["pub_id"], {}).get("pub_name", "–")
    links = sorted((r for r in TITLEAUTHOR if r["title_id"] == tid), key=lambda r: r["au_ord"])
    tiers = sorted((r for r in ROYSCHED if r["title_id"] == tid), key=lambda r: r["lorange"] or 0)
    rows = sorted((s for s in SALES if s["title_id"] == tid), key=lambda s: s["ord_date"], reverse=True)
    body = heading(clip(t["title"], 46), tid, f"{ttype(t)} · {pub} · Title ID {tid}", "Library")
    body += [rect(240, 174, 300, 606, 14, stroke=LINE), rect(264, 194, 252, 196, 12, MINT)]
    auths = title_authors(tid)
    body += cover(338, 216, t, author_line=byline(auths[0]) if auths else "")
    facts = [("Type", ttype(t)), ("Publisher", clip(pub, 20)), ("Price", money(t["price"])),
             ("Advance", "–" if t["advance"] is None else f'${t["advance"]:,.0f}'), ("Royalty", "–" if t["royalty"] is None else f'{t["royalty"]}%'),
             ("YTD sales", "–" if t["ytd_sales"] is None else f'{t["ytd_sales"]:,}'), ("Published", fmt_date(t["pubdate"]))]
    for i, (k, v) in enumerate(facts):
        body += [text(264, 424 + i * 26, k, 13, color=MUTED), text(516, 424 + i * 26, v, 13, 600, anchor="end")]
    body += [rect(264, 606, 252, 82, 10, FIELD), text(278, 626, "NOTES", 10, 700, MUTED, extra=' letter-spacing="1"')]
    body += [text(278, 646 + i * 17, ln, 12) for i, ln in enumerate(wrap(t["notes"] or "No notes.", 36)[:2])]
    body += button(*TITLE_EDIT[:3], "Edit", False, "edit") + button(*TITLE_REMOVE[:3], "Remove", False, "trash", danger=True)
    # authors
    body += [rect(556, 174, 612, 168, 14, stroke=LINE), text(580, 204, "Authors", 15, 700), text(650, 204, len(links), 13, color=MUTED)]
    body += table_head(556, 612, 218, [("Author", 580, None), ("Author ID", 800, None), ("Order", 960, "middle"), ("Royalty share", 1144, "end")])
    if not links:
        body += [icon("info", 580, 264, AMBER, .9), text(606, 278, "No author is linked to this title, so no author login can manage it.", 13, 600, AMBER)]
    share = sum(r["royaltyper"] for r in links)
    if links and share != 100:
        body += [icon("info", 580, 312, AMBER, .8), text(602, 325, f"Royalty shares add up to {share}%, not 100%. Fix this in titleauthor.", 12, 600, AMBER)]
    for i, r in enumerate(links[:2] if links and share != 100 else links[:3]):
        a = AUTHORS[r["au_id"]]
        y = 252 + i * 30
        body += [text(580, y + 18, full_name(a), 13, 600), text(800, y + 18, a["au_id"], 12, color=MUTED),
                 text(960, y + 18, r["au_ord"], 13, anchor="middle"), text(1144, y + 18, f'{r["royaltyper"]}%', 13, 700, BRAND, "end")]
    # royalty schedule
    body += [rect(556, 358, 296, 190, 14, stroke=LINE), text(580, 388, "Royalty schedule", 15, 700), hline(556, 852, 402)]
    if not tiers:
        body += [text(580, 432, "No royalty tiers for this title.", 13, color=MUTED)]
    for i, r in enumerate(tiers[:4]):
        y = 432 + i * 30
        hi = "and up" if r["hirange"] is None else f'– {r["hirange"]:,}'
        body += [text(580, y, f'{r["lorange"]:,} {hi} copies', 13), text(828, y, f'{r["royalty"]}%', 13, 700, BRAND, "end")]
    # sales summary
    revenue = sum(s["qty"] * t["price"] for s in rows) if t["price"] is not None else None
    body += [rect(872, 358, 296, 190, 14, stroke=LINE), text(896, 388, "Sales", 15, 700), hline(872, 1168, 402)]
    for i, (label, val) in enumerate([("Units sold", f'{sum(s["qty"] for s in rows):,}'), ("Revenue", "–" if revenue is None else f"${revenue:,.2f}"),
                                      ("Orders", str(len({(s["stor_id"], s["ord_num"]) for s in rows}))), ("Stores", str(len({s["stor_id"] for s in rows})))]):
        x, y = 896 + (i % 2) * 140, 440 + (i // 2) * 56
        body += [text(x, y, val, 18, 700), text(x, y + 18, label, 12, color=MUTED)]
    # sales lines
    body += [rect(556, 564, 612, 216, 14, stroke=LINE), text(580, 594, "Sales of this title", 15, 700), text(718, 594, f"{len(rows)} line{'' if len(rows) == 1 else 's'}", 13, color=MUTED)]
    body += table_head(556, 612, 608, [("Order #", 580, None), ("Date", 670, None), ("Store", 790, None), ("Qty", 1040, "end"), ("Amount", 1144, "end")])
    if not rows:
        body += [text(580, 672, "No sales recorded for this title yet.", 13, color=MUTED)]
    for i, s in enumerate(rows[:4]):
        y = 640 + i * 34
        amount = "–" if t["price"] is None else f'${s["qty"] * t["price"]:,.2f}'
        body += [text(580, y + 22, s["ord_num"], 13, 600), text(670, y + 22, fmt_date(s["ord_date"]), 13, color=MUTED),
                 text(790, y + 22, clip(store_name(s["stor_id"]), 24), 13), text(1040, y + 22, s["qty"], 13, 600, anchor="end"),
                 text(1144, y + 22, amount, 13, 600, anchor="end")]
    write(f"titles/{tid}.svg", f"t-{tid}-title", t["title"], "admin", {"library"}, body)


# ---------------------------------------------------------------- admin: removed books (app_title_removals)
def removed_books():
    body = heading("Removed books", "Removed books", "Books an author deleted. They stay hidden from the library until you restore them.",
                   "Library", len(REMOVALS))
    body += [rect(240, 174, 928, 64, 12, BG), icon("info", 260, 196, BRAND, .9),
             text(288, 202, "Removing a book only adds a row to app_title_removals. Its rows in titles, sales and roysched are never deleted,", 13, color=BRAND),
             text(288, 222, "so Restore simply deletes that row and the book is back.", 13, color=BRAND)]
    body += [rect(240, 254, 928, 526, 14, stroke=LINE)]
    body += table_head(240, 928, 254, [("Title", 264, None), ("Title ID", 600, None), ("Removed by", 700, None), ("Removed on", 880, None), ("", 1144, "end")])
    if not REMOVALS:
        body += [circle(704, 430, 40, MINT), icon("library", 684, 410, BRAND, 2),
                 text(704, 504, "No books have been removed", 17, 700, anchor="middle"),
                 text(704, 530, "When an author deletes one of their books it shows up here,", 13, color=MUTED, anchor="middle"),
                 text(704, 550, "and you can restore it with one click.", 13, color=MUTED, anchor="middle")]
    for i, r in enumerate(REMOVALS[:10]):
        t = TITLES[r["title_id"]]
        y = 286 + i * 44
        body += [text(264, y + 28, clip(t["title"], 40), 13, 600), text(600, y + 28, t["title_id"], 12, color=MUTED),
                 text(700, y + 28, f'user {r["removed_by"]}', 13), text(880, y + 28, fmt_date(r["removed_at"]), 13, color=MUTED)]
        body += button(1040, y + 8, 104, "Restore", False, "back", 30)
    write("admin-removed-books.svg", "arb-title", "Removed books", "admin", {"library"}, body)


# ---------------------------------------------------------------- clickable walkthrough (admin and author)
SOON = "That action is not part of this walkthrough."
LATER = "Logins and passwords come in a later step."
BACK = (240, 110, 24, 24)
SIDEBAR_GO = {
    "admin": {"home": "admin-dashboard", "library": "dashboard", "sales": "admin-sales", "discount": "admin-discounts", "store": "admin-stores",
              "publisher": "admin-publishers", "employee": "admin-employees", "access": "admin-author-access"},
    "author": {"library": "author-my-books", "shelf": "author-library", "plus": "add-book", "person": "profile", "settings": "author-settings"},
}
SIDEBAR_GO["admin"]["settings"] = "admin-settings"


def spot(r, **kw):
    return dict(r=[round(v, 1) for v in r], **kw)


def nav(role):
    """Sidebar and header hotspots every screen of a role shares."""
    _, items, foot = sidebar_layout(role)
    out = [spot((14, y - 11, 182, 42), go=SIDEBAR_GO[role][k]) if k in SIDEBAR_GO[role] else spot((14, y - 11, 182, 42), toast=f"{name} is not part of this walkthrough.")
           for k, name, y in items]
    out += [spot((14, y - 11, 182, 42), go=SIDEBAR_GO[role][k]) if k in SIDEBAR_GO[role] else spot((14, y - 11, 182, 42), toast=f"{name} is not part of this walkthrough.")
            for k, name, y in foot]
    home = "admin-dashboard" if role == "admin" else "author-my-books"
    return out + [spot((1066, 18, 128, 44), go="login", toast="Logged out."), spot((24, 22, 200, 36), go=home)]


def form_spots(screen):
    f = FORMS[screen]
    out = [spot(r[:4], fill=k) for k, r in f["fields"].items()]
    return out + [spot(f["save"], action="save"), spot(f["cancel"], go=f["cancel_go"], toast="Cancelled. Nothing was saved.")]


def walkthrough_spots():
    A, U = nav("admin"), nav("author")
    lib = [spot((*lib_card_pos(i), 214, 252), go=f"title-{t['title_id']}") for i, t in enumerate(LIB_SHOWN)]
    pubs = [spot((pub_card_pos(i)[0] + PUB_CARD[0] - 136, pub_card_pos(i)[1] + PUB_CARD[1] - 28, 120, 24), go="admin-employees")
            for i in range(len(PUBLISHERS))]
    pub_edits = [spot((pub_card_pos(i)[0] + 16, pub_card_pos(i)[1] + PUB_CARD[1] - 28, 60, 24), toast="Editing uses the same form as Add publisher.")
                 for i in range(len(PUBLISHERS))]
    access_rows = [spot((756, 410 + i * 38 + 7, 64, 24), toast=LATER) for i in range(1, 8)]
    hot = {
        "login": [spot((460, 355, 280, 48), fill="username"), spot((460, 420, 280, 48), fill="password"), spot((460, 528, 280, 48), action="login"),
                  spot((460, 486, 120, 20), toast="Remember me is not part of this walkthrough."), spot((600, 486, 140, 20), toast="Ask your admin to reset your password.")],
        "admin-dashboard": A + [spot((668, 300, 116, 26), go="admin-sales"), spot((1074, 300, 76, 26), go="admin-discounts"),
                                spot((1074, 532, 76, 26), go="admin-publishers")],
        "dashboard": A + lib + [spot((1040, 104, 128, 36), go="admin-add-book"), spot((864, 104, 164, 36), go="admin-removed-books"), spot(BACK, go="admin-dashboard")],
        "admin-removed-books": A + [spot(BACK, go="dashboard")],
        "admin-sales": A + [spot((1040, 172, 128, 36), go="admin-new-order"), spot(BACK, go="admin-dashboard")],
        "admin-discounts": A + [spot((1024, 172, 144, 36), go="admin-new-discount"), spot(BACK, go="admin-dashboard")],
        "admin-stores": A + [spot((1032, 172, 136, 36), toast=SOON), spot(BACK, go="admin-dashboard")],
        "admin-publishers": A + pubs + pub_edits + [spot((1016, 172, 152, 36), go="admin-add-publisher"), spot(BACK, go="admin-dashboard")],
        "admin-employees": A + [spot((1016, 172, 152, 36), go="admin-add-employee"), spot(BACK, go="admin-dashboard")],
        "admin-author-access": A + access_rows + [spot((980, 172, 188, 36), toast=LATER), spot((872, 734, 152, 34), toast=LATER),
                                                  spot((1032, 734, 120, 34), toast=LATER), spot(BACK, go="admin-dashboard")],
        "author-my-books": U + [spot(MYBOOKS_EDIT, go="author-edit-book"), spot(MYBOOKS_DELETE, go="author-delete-confirm"),
                                spot(MYBOOKS_ADD_TOP, go="add-book"), spot(MYBOOKS_ADD_CARD, go="add-book")],
        "author-delete-confirm": [spot(DELETE_CANCEL, go="author-my-books"),
                                  spot(DELETE_OK, go="author-my-books", toast="Book removed (demo only). The admin can restore it from Removed books.")],
        "profile": U + [spot((264, 666, 252, 40), toast=LATER), spot((770, 490, 74, 20), go="author-my-books"), spot(BACK, go="author-my-books")],
    }
    bw, bh, gap = SET_THEME_BOX
    themes = [spot((SET_LEFT[0] + 24 + i * (bw + gap), SET_THEME_Y, bw, bh), toast=f"{label}: the whole app switches at once, and this browser remembers it.")
              for i, (_, label, _) in enumerate(SET_THEMES)]
    account = [spot((SET_RIGHT[0] + SET_RIGHT[1] - 208, 382, 184, 36), toast="Asks for your current password, then the new one twice."),
               spot((SET_RIGHT[0] + SET_RIGHT[1] - 256, 430, 232, 36), toast="Every other browser signed in to this login is signed out.")]
    density = [spot((SET_LEFT[0] + SET_LEFT[1] - 230, 386, 206, 36), toast="Compact tightens tables and cards.")]
    hot["admin-settings"] = A + themes + density + account + [spot((SET_LEFT[0] + SET_LEFT[1] - 206, 524, 182, 36), toast="Sales and Employees show this many rows per page."),
                                                              spot(BACK, go="admin-dashboard")]
    hot["author-settings"] = U + themes + density + account + [spot(BACK, go="author-my-books")]
    hot["author-library"] = U + [spot((lib_card_pos(i)[0], lib_card_pos(i)[1] + 16, 214, 236),
                                      toast="Opens a view-only page: cover, authors, publisher, price and notes. Sales and royalties stay with the admin.")
                                 for i in range(len(AUTHOR_LIB_SHOWN))] + [spot(BACK, go="author-my-books")]
    for t in LIB_SHOWN:
        hot[f"title-{t['title_id']}"] = A + [spot(BACK, go="dashboard"), spot(TITLE_EDIT, toast=SOON),
                                             spot(TITLE_REMOVE, toast="Removing adds the book to Removed books; the admin can restore it.")]
    back = {"admin-add-book": "dashboard", "admin-new-order": "admin-sales", "admin-new-discount": "admin-discounts",
            "admin-add-publisher": "admin-publishers", "admin-add-employee": "admin-employees", "add-book": "author-my-books", "author-edit-book": "author-my-books"}
    for screen in FORMS:
        role_nav = U if screen in ("add-book", "author-edit-book") else A
        hot[screen] = role_nav + form_spots(screen) + [spot(BACK, go=back[screen])]
    return hot


SCREEN_FILES = {"title-" + t["title_id"]: f"titles/{t['title_id']}" for t in LIB_SHOWN}
SCREEN_TITLES = {
    "login": "Login", "admin-dashboard": "Admin Dashboard", "dashboard": "Library", "admin-add-book": "Add Book", "admin-removed-books": "Removed books",
    "admin-sales": "Sales", "admin-new-order": "New Order", "admin-discounts": "Discounts", "admin-new-discount": "New Discount", "admin-stores": "Stores",
    "admin-publishers": "Publishers", "admin-add-publisher": "Add Publisher", "admin-employees": "Employees", "admin-add-employee": "Add Employee",
    "admin-author-access": "Author Access", "author-my-books": "My Books", "add-book": "Add Book", "author-edit-book": "Edit Book",
    "author-delete-confirm": "Delete Book", "profile": "My Profile", "admin-settings": "Settings", "author-settings": "Settings", "author-library": "Library",
}


def fill_overlay(key, x, y, w, h, value, ic):
    """Shown over a field once it is clicked, as if the value had been entered."""
    parts = [rect(x, y, w, h, 9, "#ffffff", BRAND, ' stroke-width="1.6"')]
    lines = wrap(value, 70) if h > 50 else [value]
    parts += [text(x + 14, y + 26 + i * 20, ln, 14, 600) for i, ln in enumerate(lines[:3])]
    if ic:
        parts.append(icon(ic, x + w - 32, y + (h - 20) / 2, MUTED, .9))
    return parts


def prototype():
    hot = walkthrough_spots()
    for t in LIB_SHOWN:
        SCREEN_TITLES[f"title-{t['title_id']}"] = t["title"]
    overlays = {"login": [
        ("username-admin", '<rect x="502" y="359" width="232" height="40" fill="#fbf7f0"/><text x="510" y="385" font-size="15" fill="#4a2810" font-weight="600">admin</text>'),
        ("username-author", f'<rect x="502" y="359" width="232" height="40" fill="#fbf7f0"/><text x="510" y="385" font-size="15" fill="#4a2810" font-weight="600">{LOGIN_USERNAME}</text>'),
        ("password", '<rect x="502" y="424" width="194" height="40" fill="#fbf7f0"/><text x="510" y="451" font-size="18" fill="#4a2810" letter-spacing="2">••••••••</text>')]}
    for screen, f in FORMS.items():
        groups = [(k, "".join(fill_overlay(k, *r[:4], f["demo"][k], r[4]))) for k, r in f["fields"].items()]
        groups += [(" ".join(needs), svg) for needs, svg in f["extras"]]
        overlays[screen] = groups

    sections = []
    for name in hot:
        svg = (SVG_DIR / f"{SCREEN_FILES.get(name, name)}.svg").read_text().replace("<svg ", '<svg class="screen-svg" ', 1)
        layers = "".join(f'<g class="filled" data-needs="{needs}">{g}</g>' for needs, g in overlays.get(name, []))
        spots = "".join(f'<rect class="hot" x="{s["r"][0]}" y="{s["r"][1]}" width="{s["r"][2]}" height="{s["r"][3]}" rx="8" data-spot="{i}"/>'
                        for i, s in enumerate(hot[name]))
        sections.append(f'<section class="screen" id="s-{name}" data-title="{esc(SCREEN_TITLES[name])}" hidden>{svg}'
                        f'<svg class="layer" viewBox="0 0 1200 800" xmlns="http://www.w3.org/2000/svg">{layers}{spots}</svg></section>')
    forms = {k: {"required": f["required"], "done": f["done"], "toast": f["toast"]} for k, f in FORMS.items()}
    html = (PROTOTYPE_HTML.replace("__SECTIONS__", "\n".join(sections)).replace("__HOT__", json.dumps(hot))
            .replace("__FORMS__", json.dumps(forms)).replace("__AUTHOR__", json.dumps(full_name(LOGIN_AUTHOR))))
    (SVG_DIR / "prototype.html").write_text(html)
    print("wrote", SVG_DIR / "prototype.html", f"({len(html) // 1024} KB, {len(hot)} screens)")


PROTOTYPE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Digital Library – Walkthrough</title>
<style>
  :root { --ink: #1f2a24; --muted: #7a837e; --brand: #1f4d3a; --bg: #e9eeeb; --amber: #e0a83a; }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink); font-family: 'Segoe UI', Helvetica, Arial, sans-serif; }
  header { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; max-width: 1200px; margin: 0 auto; padding: 14px 16px; }
  header h1 { font-size: 16px; margin: 0; }
  header .where { color: var(--muted); font-size: 14px; }
  header .spacer { flex: 1; }
  header button, header label { font: inherit; font-size: 13px; }
  header button { border: 1px solid #cfd8d3; background: #fff; border-radius: 8px; padding: 6px 12px; cursor: pointer; }
  .roles { display: inline-flex; border: 1px solid #cfd8d3; border-radius: 8px; overflow: hidden; }
  .roles button { border: 0; border-radius: 0; }
  .roles button[aria-pressed="true"] { background: var(--brand); color: #fff; }
  .stage { position: relative; max-width: 1200px; margin: 0 auto 24px; padding: 0 16px; }
  .screen { position: relative; box-shadow: 0 8px 30px rgba(31, 42, 36, .15); border-radius: 10px; overflow: hidden; background: #fff; }
  .screen-svg, .layer { display: block; width: 100%; height: auto; }
  .layer { position: absolute; inset: 0; }
  .hot { fill: transparent; cursor: pointer; }
  .hot:hover { fill: rgba(224, 168, 58, .14); }
  body.show-areas .hot { stroke: var(--amber); stroke-width: 2; stroke-dasharray: 6 4; fill: rgba(224, 168, 58, .08); }
  .hot.missing { stroke: #c0533a; stroke-width: 2.5; fill: rgba(192, 83, 58, .06); }
  .filled { display: none; pointer-events: none; }
  .filled.on { display: inline; }
  #toast { position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%); background: var(--brand); color: #fff;
           padding: 10px 18px; border-radius: 10px; font-size: 14px; opacity: 0; transition: opacity .2s; pointer-events: none; max-width: calc(100% - 32px); }
  #toast.show { opacity: 1; }
  .hint { max-width: 1200px; margin: 0 auto; padding: 0 16px 10px; color: var(--muted); font-size: 13px; }
</style>
</head>
<body>
<header>
  <h1>Digital Library · Walkthrough</h1>
  <span class="roles" role="group" aria-label="Walk through as">
    <button data-role="admin" aria-pressed="true">Admin</button><button data-role="author" aria-pressed="false">Author</button>
  </span>
  <span class="where" id="where"></span>
  <span class="spacer"></span>
  <label><input type="checkbox" id="areas"> Show clickable areas</label>
  <button id="restart">Restart</button>
</header>
<p class="hint">Demo only: clicking a field fills it with sample data, and nothing is written to the database.</p>
<div class="stage">
__SECTIONS__
</div>
<div id="toast" role="status"></div>
<script>
const HOT = __HOT__;
const FORMS = __FORMS__;
const AUTHOR = __AUTHOR__;
const filled = {};
let current = null, role = 'admin', toastTimer = null;

function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2800);
}

function show(name) {
  document.querySelectorAll('.screen').forEach(s => { s.hidden = s.id !== 's-' + name; });
  current = name;
  document.getElementById('where').textContent = '› ' + document.getElementById('s-' + name).dataset.title;
  window.scrollTo(0, 0);
}

function refresh(name) {
  const have = filled[name] || new Set();
  document.querySelectorAll(`#s-${name} .filled`).forEach(g => {
    g.classList.toggle('on', g.dataset.needs.split(' ').every(k => have.has(k)));
  });
}

function fill(name, key) {
  if (name === 'login' && key === 'username') key = 'username-' + role;
  (filled[name] = filled[name] || new Set()).add(key);
  const i = HOT[name].findIndex(s => s.fill === key);
  document.querySelector(`#s-${name} .hot[data-spot="${i}"]`)?.classList.remove('missing');
  refresh(name);
}

function reset(name) {
  delete filled[name];
  document.querySelectorAll(`#s-${name} .hot.missing`).forEach(h => h.classList.remove('missing'));
  refresh(name);
}

function missing(name, keys) {
  const have = filled[name] || new Set();
  const left = keys.filter(k => !have.has(k));
  left.forEach(k => {
    const i = HOT[name].findIndex(s => s.fill === k);
    document.querySelector(`#s-${name} .hot[data-spot="${i}"]`)?.classList.add('missing');
  });
  return left;
}

function run(name, spot) {
  if (spot.fill) { fill(name, spot.fill); return; }
  if (spot.action === 'login') {
    const have = filled.login || new Set();
    if (!have.has('username-' + role) || !have.has('password')) {
      toast('Click the username and password fields to fill them.');
      return;
    }
    reset('login');
    show(role === 'admin' ? 'admin-dashboard' : 'author-my-books');
    toast(role === 'admin' ? 'Signed in as admin.' : `Signed in as ${AUTHOR}.`);
    return;
  }
  if (spot.action === 'save') {
    const f = FORMS[name];
    if (missing(name, f.required).length) { toast('Fill the required fields first: click each one marked *.'); return; }
    reset(name);
    show(f.done);
    toast(f.toast);
    return;
  }
  if (spot.go) {
    if (FORMS[name]) reset(name);
    show(spot.go);
  }
  if (spot.toast) toast(spot.toast);
}

document.querySelectorAll('.screen').forEach(sec => {
  const name = sec.id.slice(2);
  sec.querySelectorAll('.hot').forEach(el => el.addEventListener('click', () => run(name, HOT[name][+el.dataset.spot])));
});
function restart() { Object.keys(filled).forEach(reset); show('login'); }
document.querySelectorAll('.roles button').forEach(b => b.addEventListener('click', () => {
  role = b.dataset.role;
  document.querySelectorAll('.roles button').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
  restart();
  toast(role === 'admin' ? 'Walking through as the admin.' : `Walking through as the author ${AUTHOR}.`);
}));
document.getElementById('areas').addEventListener('change', e => document.body.classList.toggle('show-areas', e.target.checked));
document.getElementById('restart').addEventListener('click', restart);
show('login');
</script>
</body>
</html>
"""


if __name__ == "__main__":
    admin_dashboard()
    admin_library()
    for t in LIB_SHOWN:
        title_detail(t)
    removed_books()
    admin_sales()
    admin_discounts()
    admin_stores()
    admin_publishers()
    admin_employees()
    author_access()
    book_form("admin")
    new_order()
    new_discount()
    add_publisher()
    add_employee()
    my_books()
    my_books(confirm_delete=True)
    book_form("author")
    edit_book()
    profile()
    settings_screen("admin")
    settings_screen("author")
    author_library()
    prototype()
