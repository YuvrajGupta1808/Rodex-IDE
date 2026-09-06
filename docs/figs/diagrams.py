#!/usr/bin/env python3
"""Print-quality SVG figures for the Rodex dossier.

Palette and type match the LaTeX document: Charter for labels, Menlo for
identifiers, a restrained slate/ink scheme with no decorative colour.
"""

INK   = "#1F2933"
BODY  = "#3E4C59"
MUTE  = "#7B8794"
RULE  = "#BCC5CE"
FAINT = "#F0F3F5"
WHITE = "#FFFFFF"
OK    = "#2F6B4F"
BAD   = "#9B3B36"

SERIF = "Charter, Georgia, serif"
MONO  = "Menlo, monospace"


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Fig:
    def __init__(self, w, h):
        self.w, self.h, self.o = w, h, []

    def rect(self, x, y, w, h, fill=WHITE, stroke=RULE, sw=1.2, rx=3, dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        st = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
        self.o.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
                      f'fill="{fill}" rx="{rx}"{st}{d}/>')

    def text(self, x, y, s, size=13, fill=INK, fam=SERIF, weight="400",
             anchor="start", ls=0, style=""):
        lsa = f' letter-spacing="{ls}"' if ls else ""
        sty = f' font-style="{style}"' if style else ""
        self.o.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" '
                      f'font-family="{fam}" font-weight="{weight}" '
                      f'text-anchor="{anchor}"{lsa}{sty}>{esc(s)}</text>')

    def line(self, x1, y1, x2, y2, stroke=RULE, sw=1.2, dash=None, arrow=False):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        a = ' marker-end="url(#ar)"' if arrow else ""
        self.o.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                      f'stroke="{stroke}" stroke-width="{sw}"{d}{a}/>')

    def path(self, d, stroke=RULE, sw=1.2, fill="none", arrow=False, dash=None):
        a = ' marker-end="url(#ar)"' if arrow else ""
        ds = f' stroke-dasharray="{dash}"' if dash else ""
        self.o.append(f'<path d="{d}" fill="{fill}" stroke="{stroke}" '
                      f'stroke-width="{sw}"{a}{ds}/>')

    def save(self, path, label):
        head = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" '
                f'width="{self.w}" height="{self.h}" role="img" aria-label="{esc(label)}">'
                f'<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" '
                f'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
                f'<path d="M0,1 L9,5 L0,9 z" fill="{MUTE}"/></marker></defs>'
                f'<rect width="{self.w}" height="{self.h}" fill="{WHITE}"/>')
        open(path, "w").write(head + "".join(self.o) + "</svg>")
        print("wrote", path)


# ─────────────────────────── Figure 1: review pipeline ───────────────────────
f = Fig(1000, 430)

f.text(0, 14, "BROWSER", 11, MUTE, SERIF, "700", ls=1.6)
f.rect(0, 26, 1000, 54, FAINT, RULE)
f.text(20, 51, "Authenticated session", 14, INK, SERIF, "700")
f.text(20, 69, "Firebase email link  ·  signed cookie  ·  gated on every route", 12, BODY, SERIF)
f.text(980, 58, "Server-Sent Events", 12, MUTE, MONO, anchor="end")

# stream arrow down the right edge
f.path("M 962 80 L 962 300", MUTE, 1.1, dash="3 4", arrow=True)
f.text(952, 195, "live events", 11, MUTE, SERIF, anchor="end")

f.text(0, 108, "COORDINATOR", 11, MUTE, SERIF, "700", ls=1.6)
f.rect(0, 120, 830, 62, WHITE, INK, 1.6)
f.text(20, 145, "Bounded decision loop", 14, INK, SERIF, "700")
f.text(20, 164, "plans  ·  dispatches  ·  judges findings  ·  retries failures  ·  finishes",
       12, BODY, SERIF)
f.text(812, 155, "14 iterations max", 11, MUTE, MONO, anchor="end")

# fan-out
f.path("M 150 182 L 150 210", MUTE, 1.2, arrow=True)
f.path("M 415 182 L 415 210", MUTE, 1.2, arrow=True)
f.path("M 680 182 L 680 210", MUTE, 1.2, arrow=True)
f.text(437, 226, "dispatched in one turn — run concurrently", 11, MUTE, SERIF, style="italic")

f.text(0, 232, "SPECIALISTS", 11, MUTE, SERIF, "700", ls=1.6)
specs = [
    (0,   "Security",      "injection · secrets · authn"),
    (283, "Bug Detection", "nulls · leaks · races"),
    (566, "Fix",           "writes and applies patches"),
]
for x, name, sub in specs:
    f.rect(x, 244, 264, 56, WHITE, RULE)
    f.text(x + 16, 267, name, 13.5, INK, SERIF, "700")
    f.text(x + 16, 286, sub, 11.5, BODY, SERIF)

# into verification
f.path("M 698 300 L 698 330", MUTE, 1.2, arrow=True)

f.text(0, 330, "VERIFICATION", 11, MUTE, SERIF, "700", ls=1.6)
f.rect(566, 342, 264, 62, FAINT, INK, 1.6)
f.text(582, 366, "Two gates", 13.5, INK, SERIF, "700")
f.text(582, 385, "compile  +  AST pattern removal", 11.5, BODY, SERIF)

# accept / reject
f.path("M 830 373 L 892 373", OK, 1.4, arrow=True)
f.text(898, 369, "recorded", 12, OK, SERIF, "700")

# rejection returns to the coordinator: down, left along the margin, back up
f.path("M 566 373 L 470 373", BAD, 1.3, dash="4 3")
f.path("M 470 373 L 470 216", BAD, 1.3, dash="4 3")
f.path("M 470 216 L 470 182", BAD, 1.3, dash="4 3", arrow=True)
f.text(462, 340, "rejected — rolled back,", 11.5, BAD, SERIF, anchor="end", style="italic")
f.text(462, 356, "reason returned to the coordinator", 11.5, BAD, SERIF,
       anchor="end", style="italic")

f.save("fig-pipeline.svg", "Review pipeline: authenticated browser session, coordinator "
       "decision loop, three specialists dispatched concurrently, and two-gate "
       "verification that either records a patch or rolls it back.")


# ─────────────────────────── Figure 2: the two gates ─────────────────────────
g = Fig(1000, 250)

g.rect(0, 30, 196, 66, WHITE, RULE)
g.text(98, 58, "Patch proposed", 13.5, INK, SERIF, "700", anchor="middle")
g.text(98, 78, "by the fix agent", 11.5, BODY, SERIF, anchor="middle")
g.path("M 196 63 L 250 63", MUTE, 1.3, arrow=True)

# gate 1
g.rect(250, 20, 230, 86, WHITE, INK, 1.6)
g.text(266, 42, "GATE 1", 10.5, MUTE, SERIF, "700", ls=1.6)
g.text(266, 63, "Compile", 14, INK, SERIF, "700")
g.text(266, 82, "written to sandbox,", 11.5, BODY, SERIF)
g.text(266, 97, "compiled", 11.5, BODY, SERIF)
g.path("M 480 63 L 534 63", MUTE, 1.3, arrow=True)

# gate 2
g.rect(534, 20, 230, 86, WHITE, INK, 1.6)
g.text(550, 42, "GATE 2", 10.5, MUTE, SERIF, "700", ls=1.6)
g.text(550, 63, "Pattern removal", 14, INK, SERIF, "700")
g.text(550, 82, "AST walked before and after;", 11.5, BODY, SERIF)
g.text(550, 97, "occurrences counted", 11.5, BODY, SERIF)
g.path("M 764 63 L 818 63", OK, 1.4, arrow=True)

g.rect(818, 30, 182, 66, FAINT, OK, 1.6)
g.text(909, 58, "Recorded", 13.5, OK, SERIF, "700", anchor="middle")
g.text(909, 78, "written to the file", 11.5, BODY, SERIF, anchor="middle")

# failure paths
g.path("M 365 106 L 365 176", BAD, 1.3, arrow=True, dash="4 3")
g.text(378, 140, "does not parse", 11.5, BAD, SERIF, style="italic")
g.path("M 649 106 L 649 176", BAD, 1.3, arrow=True, dash="4 3")
g.text(662, 140, "count not reduced", 11.5, BAD, SERIF, style="italic")

g.rect(250, 176, 514, 58, WHITE, BAD, 1.6)
g.text(507, 200, "Rolled back", 13.5, BAD, SERIF, "700", anchor="middle")
g.text(507, 220, "failure reason returned to the coordinator, which decides whether to retry",
       11.5, BODY, SERIF, anchor="middle")

g.save("fig-gates.svg", "The two verification gates: compile, then AST pattern removal. "
       "Failure at either gate rolls the patch back and returns the reason to the "
       "coordinator.")
