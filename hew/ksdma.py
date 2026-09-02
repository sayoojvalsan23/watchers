"""
KSDMA gauge readings — the first feed that can actually see the event.

WHY THIS EXISTS
---------------
Every gridded product we tested is blind to the rainfall that kills people in
the Western Ghats. Measured at Chooralmala / Mundakkai for 30 July 2024:

    IMD gauge, Vythiri            280.0 mm      <- KSDMA, this module
    CHIRPS v2.0     ~5.5 km        49.7 mm
    Open-Meteo ERA5  ~25 km        51.6 mm
    NASA POWER      ~50 km         52.6 mm

A NINEFOLD improvement in grid resolution moved the answer by 3 mm. Resolution
was never the constraint: these products blend satellite IR with GAUGE data,
and with no gauge in the blend near Wayanad they all fall back on the same
physics and land on the same wrong number.

Kerala SDMA publishes the gauge readings daily as a PDF, ~79 stations, five of
them in Wayanad. That is the instrument that saw 280 mm.

WHAT IT IS GOOD FOR, AND WHAT IT IS NOT
---------------------------------------
CALIBRATION, not triggering. The bulletin covers 08:00 to 08:00 and is
published around 12:00 IST. The Chooralmala landslides were ~01:00 and ~04:10
on 30 July; the bulletin carrying that night's 280 mm went up at 12:16 -- more
than eight hours after. As a trigger it is useless, exactly like the USGS
+13 h landslide characterisation in D5.

As a CALIBRATION set it is decisive. Over 524 days at Vythiri:

    30 July 2024 is the wettest day in the record   280 mm
    next wettest                                    190 mm
    a 200 mm threshold fires                        0.7 times/year

That is inside the Phase 0 gate of <=2 alerts/year AND it catches the event.
The same exercise against ERA5 needed a threshold firing 14 times/year to
catch it -- a 7x gate failure. Gauge data is the difference between a
detector that works and one that cannot exist.

The real-time feed of these same gauges is IMD's AWS API, which answers:

    401  "Your IP/Domain needs to be whitelisted"

So the feed exists and runs; access to it is an institutional grant, not an
engineering problem. See CONSTRAINTS.md, Phase 5.

HOW THE EXTRACTION WORKS
------------------------
These PDFs carry NO ToUnicode CMap, so ordinary text extraction -- and
copy-paste -- returns mojibake. But the font is an embedded TrueType subset
with an intact `cmap` table, and the content stream addresses glyphs by GID
under Identity-H encoding. Inverting the font's own cmap recovers the text
exactly. No OCR, no guessing, no per-file tuning.

Malayalam station names come back with gaps, because conjuncts are drawn from
GSUB substitution glyphs that have no cmap entry to invert. The NUMBERS are
exact, and the labels are stable enough across the archive to key on.
"""
import re, zlib, struct


def _streams(raw):
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S):
        try:
            out.append(zlib.decompress(m.group(1)))
        except Exception:
            pass
    return out


def gid_to_unicode(ttf):
    """Invert the font's cmap: glyph id -> character."""
    n = struct.unpack(">H", ttf[4:6])[0]
    tabs = {}
    for i in range(n):
        o = 12 + 16 * i
        tag, _, off, ln = struct.unpack(">4sIII", ttf[o:o + 16])
        tabs[tag] = (off, ln)
    if b"cmap" not in tabs:
        return {}
    co = tabs[b"cmap"][0]
    ntab = struct.unpack(">H", ttf[co + 2:co + 4])[0]
    best = None
    for i in range(ntab):
        pid, eid, off = struct.unpack(">HHI", ttf[co + 4 + 8 * i:co + 12 + 8 * i])
        fmt = struct.unpack(">H", ttf[co + off:co + off + 2])[0]
        if fmt in (4, 12):
            best = (fmt, co + off)
            if (pid, eid) == (3, 10) or fmt == 12:
                break
    if not best:
        return {}
    fmt, p = best
    g2u = {}
    if fmt == 4:
        segx2 = struct.unpack(">H", ttf[p + 6:p + 8])[0]
        seg = segx2 // 2
        ends = struct.unpack(">%dH" % seg, ttf[p + 14:p + 14 + segx2])
        sp = p + 16 + segx2
        starts = struct.unpack(">%dH" % seg, ttf[sp:sp + segx2])
        dp = sp + segx2
        deltas = struct.unpack(">%dh" % seg, ttf[dp:dp + segx2])
        rp = dp + segx2
        ranges = struct.unpack(">%dH" % seg, ttf[rp:rp + segx2])
        for i in range(seg):
            for c in range(starts[i], min(ends[i], 0xFFFF) + 1):
                if ranges[i] == 0:
                    g = (c + deltas[i]) & 0xFFFF
                else:
                    gp = rp + 2 * i + ranges[i] + 2 * (c - starts[i])
                    if gp + 2 > len(ttf):
                        continue
                    g = struct.unpack(">H", ttf[gp:gp + 2])[0]
                    if g:
                        g = (g + deltas[i]) & 0xFFFF
                if g:
                    g2u.setdefault(g, chr(c))
    else:
        ngroups = struct.unpack(">I", ttf[p + 12:p + 16])[0]
        for i in range(ngroups):
            s, e, sg = struct.unpack(">III", ttf[p + 16 + 12 * i:p + 28 + 12 * i])
            for c in range(s, e + 1):
                g2u.setdefault(sg + c - s, chr(c))
    return g2u


def rows(pdf_path):
    """[(y, x, text)] -- every text run with its page position."""
    raw = open(pdf_path, "rb").read()
    sts = _streams(raw)
    ttf = next((s for s in sts if s[:4] in (b"\x00\x01\x00\x00", b"true")), None)
    # NOT the font: the font binary happens to contain the bytes "Tf" too.
    content = next((s for s in sts if not s[:4] in (b"\x00\x01\x00\x00", b"true")), None)
    if not ttf or not content:
        return []
    g2u = gid_to_unicode(ttf)
    t = content.decode("latin-1")
    out, x, y = [], 0.0, 0.0
    for m in re.finditer(
            r"([-\d.]+)\s+([-\d.]+)\s+(?:Td|Tm)|"
            r"([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+Tm|"
            r"<([0-9A-Fa-f]+)>\s*Tj|\[(.*?)\]\s*TJ", t, re.S):
        if m.group(7) is not None:          # full Tm matrix
            x, y = float(m.group(7)), float(m.group(8))
        elif m.group(1) is not None:
            x, y = float(m.group(1)), float(m.group(2))
        else:
            g = m.group(9) or m.group(10) or ""
            s = "".join("".join(g2u.get(int(h[i:i + 4], 16), "")
                                for i in range(0, len(h), 4))
                        for h in re.findall(r"<([0-9A-Fa-f]+)>", g))
            if s.strip():
                out.append((round(y, 1), round(x, 1), s))
    return out


def stations(pdf_path):
    """{station name: mm} for every gauge with a numeric reading."""
    rs = rows(pdf_path)
    lines = {}
    for y, x, s in rs:
        lines.setdefault(y, []).append((x, s))
    out = {}
    for y in sorted(lines, reverse=True):
        cells = [s for _, s in sorted(lines[y])]
        joined = [c.strip() for c in cells if c.strip()]
        # a station row is <name> <number|NA>, name to the left of the value
        for i, c in enumerate(joined[:-1]):
            v = joined[i + 1].strip()
            if re.fullmatch(r"\d+(\.\d+)?", v) and not re.fullmatch(r"[\d.]+", c):
                out[c] = float(v)
            elif v.upper() == "NA" and not re.fullmatch(r"[\d.]+", c):
                out.setdefault(c, None)
    return out


VALUE_X = 820.0     # the "Actual Rainfall (mm)" column starts here
NAME_X  = 620.0     # the station-name column starts here
Y_TOL   = 4.0       # name and value baselines differ by ~1pt; rows are ~15pt apart


def table(pdf_path):
    """
    [(station_name, mm_or_None)] in page order.

    Rows are grouped by baseline with a tolerance, because the name and the
    value in the same visual row are typeset a fraction of a point apart.
    Names come back with gaps: Malayalam conjuncts are drawn from GSUB
    substitution glyphs that have no cmap entry to invert. The NUMBERS are
    exact, and the row order is what identifies the station.
    """
    rs = rows(pdf_path)
    buckets = []
    for y, x, s in sorted(rs, key=lambda r: -r[0]):
        for b in buckets:
            if abs(b["y"] - y) <= Y_TOL:
                b["cells"].append((x, s))
                break
        else:
            buckets.append({"y": y, "cells": [(x, s)]})
    out = []
    for b in buckets:
        name = "".join(s for x, s in sorted(b["cells"])
                       if NAME_X <= x < VALUE_X).strip()
        vals = [s.strip() for x, s in sorted(b["cells"]) if x >= VALUE_X]
        if not name or not vals:
            continue
        v = vals[0]
        if re.fullmatch(r"\d+(\.\d+)?", v):
            out.append((name, float(v)))
        elif v.upper() == "NA":
            out.append((name, None))
    return out


if __name__ == "__main__":
    import sys, json
    t = table(sys.argv[1])
    for i, (n, v) in enumerate(t):
        print(f"{i:3d}  {v if v is not None else 'NA':>7}  {n}")
    print(f"\n{len(t)} rows, {sum(1 for _, v in t if v is not None)} with readings")
