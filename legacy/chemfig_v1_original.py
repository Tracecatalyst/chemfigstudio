#!/usr/bin/env python3
"""课本风格化学图生成器（示性式 + 氧上孤对电子）

用法：
  python3 chemfig.py alcohol --n 4 --name butan-1-ol --dh -36.1 -o butanol.png
  python3 chemfig.py series     -o 图3_醇系列.png
  python3 chemfig.py reaction   -o 图1_丁醇燃烧反应.png
  python3 chemfig.py chcount    -o 图4_丁醇的CH键.png

规则（见 ../图规范_字体与配色.md）：Arial；氧红色；孤对电子画点；
上下标手绘（不用 Unicode，避免 Word 里变 □）；自动裁边。
"""
import argparse, math
from PIL import Image, ImageDraw, ImageFont, ImageChops

FD = "/System/Library/Fonts/Supplemental/"
INK=(17,24,39); GREY=(120,130,145); ORED=(214,31,31); WHITE=(255,255,255); ARED=(190,18,60); CGR=(55,65,81)
DX, DY = 92, 50            # 锯齿链：水平/垂直步长

def F(sz, bold=False): return ImageFont.truetype(FD + ("Arial Bold.ttf" if bold else "Arial.ttf"), sz)

def parse(t):
    o=[]; i=0
    while i < len(t):
        if t[i] in "_^" and i+1 < len(t) and t[i+1] == "{":
            j=t.index("}", i); o.append((t[i], t[i+2:j])); i=j+1
        else:
            j=len(t)
            for k in ("_{", "^{"):
                p=t.find(k, i)
                if p != -1: j=min(j, p)
            o.append(("n", t[i:j])); i=j
    return o

def w_of(d, t, f, sf): return sum(d.textlength(s, font=(f if k=="n" else sf)) for k, s in parse(t))

def rich(d, x, y, t, f, sf, fill=INK):
    for k, s in parse(t):
        if k == "_":   d.text((x, y+f.size*0.26), s, font=sf, fill=fill); x += d.textlength(s, font=sf)
        elif k == "^": d.text((x, y-f.size*0.34), s, font=sf, fill=fill); x += d.textlength(s, font=sf)
        else:          d.text((x, y), s, font=f, fill=fill); x += d.textlength(s, font=f)
    return x

def rich_c(d, y, t, f, sf, fill=INK, cx=0): return rich(d, cx-w_of(d,t,f,sf)/2, y, t, f, sf, fill)

def formula(d, x, y, t, f, sf, oc=ORED, cc=INK):
    """化学式：O 红色，其余黑色"""
    last = cc
    for k, s in parse(t):
        if k == "_":   d.text((x, y+f.size*0.26), s, font=sf, fill=last); x += d.textlength(s, font=sf)
        elif k == "^": d.text((x, y-f.size*0.34), s, font=sf, fill=last); x += d.textlength(s, font=sf)
        else:
            for ch in s:
                col = oc if ch == "O" else cc; last = col
                d.text((x, y), ch, font=f, fill=col); x += d.textlength(ch, font=f)
    return x

def lone_pairs(d, ox, oy, fs, col=ORED):
    """氧上下各一对孤对电子（共 4 点）"""
    r = 3
    cx = ox + 9                      # 与参考图一致：每对孤对上下叠两个点
    for y in (oy-19, oy-11, oy+fs+5, oy+fs+13):
        d.ellipse([cx-r, y-r, cx+r, y+r], fill=col)

def alcohol(d, x0, ymid, n, f_lab=None, sf_lab=None, note=None, f_note=None, label_style="h2", dy=None):
    """画一个示性式醇 CH3-(CH2)n-2-OH，返回右端 x。
    label_style: "h2" = 转角只写 H2（课本骨架式，默认）；"ch2" = 转角写 CH2（更直白）"""
    f_lab = f_lab or F(27, True); sf_lab = sf_lab or F(19)
    dy = DY if dy is None else dy
    v = [(x0+i*DX, ymid+(-dy if i % 2 else dy)) for i in range(n)]
    for a, b in zip(v, v[1:]): d.line([a, b], fill=INK, width=5)
    tail = (x0+n*DX, ymid+(-dy if n % 2 else dy))
    wch3 = d.textlength("CH", font=f_lab) + d.textlength("3", font=sf_lab)
    rich(d, v[0][0]-wch3-16, v[0][1]-f_lab.size*0.55, "CH_{3}", f_lab, sf_lab)
    for i in range(1, n):
        x, y = v[i]; up = y < ymid
        ty = y-f_lab.size-14 if up else y+16
        rich_c(d, ty, "CH_{2}" if label_style == "ch2" else "H_{2}", f_lab, sf_lab, cx=x)
        if note: rich_c(d, (ty-f_note.size-4) if up else (ty+sf_lab.size+8), "2 C–H", f_note, F(13), fill=GREY, cx=x)
    ox, oy = tail[0]+16, tail[1]-f_lab.size*0.55
    # 键直接连到 O 上（与课本参考图一致）
    d.line([v[-1], (ox-5, oy+f_lab.size*0.36)], fill=INK, width=5)
    rich(d, ox, oy, "OH", f_lab, sf_lab, fill=ORED)
    lone_pairs(d, ox, oy, f_lab.size)
    if note: rich_c(d, v[0][1]+f_lab.size+8, "3 C–H", f_note, F(13), fill=GREY, cx=v[0][0]-wch3/2-16)
    return ox + d.textlength("OH", font=f_lab)

def crop(path, m=18):
    im = Image.open(path).convert("RGB")
    bb = ImageChops.difference(im, Image.new("RGB", im.size, WHITE)).getbbox()
    if bb:
        x0, y0, x1, y1 = bb
        im.crop((max(0,x0-m), max(0,y0-m), min(im.width,x1+m), min(im.height,y1+m))).save(path)

def save(im, path, crop_it=True):
    # 裁切前先检查内容是否碰到画布边缘（碰到 = 被切掉了）
    bb = ImageChops.difference(im.convert("RGB"), Image.new("RGB", im.size, WHITE)).getbbox()
    if bb:
        x0, y0, x1, y1 = bb
        hit = []
        if x0 <= 1: hit.append("左")
        if y0 <= 1: hit.append("上")
        if x1 >= im.width-1: hit.append("右")
        if y1 >= im.height-1: hit.append("下")
        if hit:
            print("  ⚠ 内容碰到画布边缘（%s），可能被裁切：请把画布调大" % "、".join(hit))
    im.save(path)
    if crop_it: crop(path, m=26)
    img = Image.open(path); print("已生成", path, img.size)

# ---------- 各图 ----------
def fig_alcohol(n, name, dh, out, label_style="h2"):
    W, H = 1500, 420
    im = Image.new("RGB", (W,H), WHITE); d = ImageDraw.Draw(im)
    rich_c(d, 34, name, F(31, True), F(21), cx=560)
    alcohol(d, 130, 200, n, label_style=label_style)
    if dh is not None:
        rich_c(d, 330, f"ΔH_{{c}} = {dh} kJ g^{{−1}}", F(27, True), F(19), cx=560, fill=ARED)
    save(im, out)

def fig_series(out):
    data = [("ethanol","C_{2}H_{5}OH",2,"−29.7"), ("propan-1-ol","C_{3}H_{7}OH",3,"−33.6"),
            ("butan-1-ol","C_{4}H_{9}OH",4,"−36.1"), ("pentan-1-ol","C_{5}H_{11}OH",5,"−37.8"),
            ("hexan-1-ol","C_{6}H_{13}OH",6,"−39.0")]
    W, H = 1800, 1080
    im = Image.new("RGB", (W,H), WHITE); d = ImageDraw.Draw(im)
    rich(d, 40, 30, "The alcohol series, from the shortest chain to the longest", F(31, True), F(21))
    d.text((1000, 64), "ΔHc (literature)", font=F(19), fill=GREY)
    rich(d, 1000, 88, "kJ g^{−1}", F(19), F(13), fill=GREY)
    PITCH, DY_S = 168, 36
    centers = []
    for i, (nm, fo, n, dh) in enumerate(data):
        ym = 130 + i*PITCH
        centers.append(ym)
        alcohol(d, 90, ym, n, f_lab=F(25, True), sf_lab=F(17), dy=DY_S)
        rich(d, 760, ym-34, nm, F(25, True), F(17))
        rich(d, 760, ym+2, fo, F(20), F(14), fill=GREY)
        rich(d, 1040, ym-18, dh + " kJ g^{−1}", F(21, True), F(14), fill=ARED)
    # 重叠自检：相邻两行之间必须存在一条全白的水平线
    for a, b in zip(centers, centers[1:]):
        mid = (a+b)//2
        blank = False
        for y in range(a+DY_S+6, b):
            if all(im.getpixel((x, y)) == WHITE for x in range(60, im.width-60)):
                blank = True; break
        if not blank:
            print("  ⚠ 第 %d 行与第 %d 行的分子可能重叠（找不到全白分隔线）" % (centers.index(a)+1, centers.index(b)+1))
    save(im, out)

def fig_reaction(out):
    W, H = 2100, 560
    im = Image.new("RGB", (W,H), WHITE); d = ImageDraw.Draw(im)
    rich(d, 40, 26, "Complete combustion of butan-1-ol in excess oxygen", F(31, True), F(21))
    ym = 210
    xr = alcohol(d, 90, ym, 4)
    d.text((xr+44, ym-58), "+", font=F(34, True), fill=INK)
    xo = xr + 104
    formula(d, xo, ym-58, "6 O=O", F(30), F(21))
    d.line([xo+230, ym, xo+330, ym], fill=INK, width=5)
    d.polygon([(xo+342, ym), (xo+318, ym-11), (xo+318, ym+11)], fill=INK)
    d.text((xo+240, ym-58), "ignite", font=F(19), fill=GREY)
    xc = xo + 380
    formula(d, xc, ym-58, "4 O=C=O", F(30), F(21))
    d.text((xc+250, ym-58), "+", font=F(34, True), fill=INK)
    formula(d, xc+300, ym-58, "5 H–O–H", F(30), F(21))
    rich_c(d, ym+120, "C_{4}H_{9}OH  +  6 O_{2}   →   4 CO_{2}  +  5 H_{2}O", F(42, True), F(27), cx=700)
    rich_c(d, ym+186, "ΔH = −2676 kJ mol^{−1}", F(27, True), F(19), cx=700, fill=ARED)
    rich(d, 40, H-70, "Condensed structures (Lewis-style): oxygen atoms are red and their lone pairs are shown as dots.", F(19), F(13), fill=GREY)
    save(im, out)

def fig_chcount(out):
    W, H = 1700, 520
    im = Image.new("RGB", (W,H), WHITE); d = ImageDraw.Draw(im)
    rich(d, 40, 28, "Where the C–H bonds are in butan-1-ol", F(31, True), F(21))
    alcohol(d, 120, 250, 4, note=True, f_note=F(19))
    rich(d, 120, 430, "9 C–H bonds   ·   3 C–C bonds   ·   1 O–H bond", F(26, True), F(18))
    rich(d, 120, 478, "Every carbon forms four bonds; the bonds that do not join another carbon join hydrogen, and those are the C–H bonds.", F(19), F(13), fill=GREY)
    save(im, out)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("alcohol"); a.add_argument("--n", type=int, required=True)
    a.add_argument("--name", default=""); a.add_argument("--dh", default=None); a.add_argument("-o", required=True)
    a.add_argument("--vertex-labels", choices=["h2", "ch2"], default="h2", help="转角标注：h2（课本骨架式）或 ch2（直白）")
    for c in ("series", "reaction", "chcount"):
        p = sub.add_parser(c); p.add_argument("-o", required=True)
    args = ap.parse_args()
    if args.cmd == "alcohol": fig_alcohol(args.n, args.name or f"C{args.n} alcohol", args.dh, args.o, args.vertex_labels)
    elif args.cmd == "series": fig_series(args.o)
    elif args.cmd == "reaction": fig_reaction(args.o)
    elif args.cmd == "chcount": fig_chcount(args.o)
