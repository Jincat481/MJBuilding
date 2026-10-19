"""MarkovJunior 기반 모듈식 건물 생성 (UE 비의존 순수 로직).

층마다 다른 seed로 MarkovJunior 평면도를 만들고, 계단 칸은 전 층 같은 자리에 고정한 뒤
동선이 끊기면 문을 추가해 반드시 이어지게 한다. 방은 '열린 경계(O)로 이어진 칸 묶음'이다.

평면은 '2배 격자': 방 W x H 칸 -> 격자 (2W+1) x (2H+1)
  (홀수,홀수)          방 칸 (소문자 방 기호, S = 계단)
  (짝수,홀수)/(홀수,짝수) 경계: O 열림  W 내벽  D 문  X 외벽  N 창문  F 정문
  (짝수,짝수)          기둥 자리 (사용 안 함)

단독 실행 (평면도/방 확인용):  python mj_core.py 5 5 3 [seed]
"""
import math
import os
import random
import shutil
import subprocess
import tempfile
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
MJ_DIR = os.path.join(HERE, "mj")
MJ_EXE = os.path.join(MJ_DIR, "MarkovJunior.exe")
MODEL = "MJBuildingPlan"
ROOMS = "ACGHKL"   # 미도달 방 기호 -> 도달하면 소문자

# ---- 치수 (cm) ----
C, FH, T = 400, 300, 20          # 칸 크기, 층 높이, 벽/슬래브 두께
WH = FH - T
DOOR_W, DOOR_H = 120, 220
WIN_W, WIN_SILL, WIN_H = 160, 90, 120
RAMP_W = 360
PASS = "OD"
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))


# ================================================================== MarkovJunior
def model_xml(seed_count, window_p=0.45, extra_door_p=0.08):
    lo = ROOMS.lower()
    L = ['<sequence values="BREXOWDNFS%s%s" origin="True">' % (ROOMS, lo),
         '  <all in="RBB" out="RER"/>',            # 중앙에서 방 칸(R)/경계(E) 격자 전개
         '  <all in="RBR" out="RER"/>',
         '  <all in="RB" out="RX"/>']              # 바깥 경계 = 외벽
    counts = {c: 0 for c in ROOMS}
    for i in range(seed_count):
        counts[ROOMS[i % len(ROOMS)]] += 1
    L += ['  <one in="R" out="%s" steps="%d"/>' % (c, n) for c, n in counts.items() if n]   # 방 씨앗
    L += ['  <one>'] + ['    <rule in="%sER" out="%sO%s"/>' % (c, c, c) for c in ROOMS] + ['  </one>']  # 영역 성장
    L += ['  <all>'] + ['    <rule in="%sE%s" out="%sO%s"/>' % (c, c, c, c) for c in ROOMS] + ['  </all>']
    L += ['  <one steps="1">'] + ['    <rule in="X%s" out="F%s"/>' % (c, c) for c in ROOMS] + ['  </one>']  # 정문
    L += ['  <all>'] + ['    <rule in="F%s" out="F%s"/>' % (c, c.lower()) for c in ROOMS] + ['  </all>']
    # 정문에서 도달한 방부터 퍼지고, 막히면 이웃 방으로 문 하나
    L += ['  <markov>', '    <all>']
    L += ['      <rule in="%sO%s" out="%sO%s"/>' % (c.lower(), c, c.lower(), c.lower()) for c in ROOMS]
    L += ['    </all>', '    <one>']
    L += ['      <rule in="%sE%s" out="%sD%s"/>' % (a.lower(), b, a.lower(), b.lower())
          for a in ROOMS for b in ROOMS if a != b]
    L += ['    </one>', '  </markov>']
    if extra_door_p > 0:                                                     # 가끔 추가 문
        L += ['  <prl steps="1">']
        L += ['    <rule in="%sE%s" out="%sD%s" p="%.3f"/>' % (a, b, a, b, extra_door_p)
              for a in lo for b in lo if a != b]
        L += ['  </prl>']
    L += ['  <prl in="E" out="W"/>',
          '  <prl in="X" out="N" p="%.3f" steps="1"/>' % window_p,
          '</sequence>']
    return "\n".join(L)


def run_markov(rooms_x, rooms_y, seeds, rooms=None):
    """seeds 마다 평면도 하나씩. MarkovJunior 한 번 실행으로 전부 만든다. 반환: [grid, ...]"""
    if rooms_x % 2 == 0 or rooms_y % 2 == 0 or rooms_x < 3 or rooms_y < 3:
        raise ValueError("RoomsX, RoomsY 는 3 이상의 홀수여야 합니다 (3, 5, 7 ...)")
    if rooms is None or rooms <= 0:
        rooms = max(2, round(rooms_x * rooms_y / 4))
    work = tempfile.mkdtemp(prefix="mjb_")
    try:
        os.makedirs(os.path.join(work, "models"))
        shutil.copytree(os.path.join(MJ_DIR, "res"), os.path.join(work, "resources"))   # palette/settings/fonts
        with open(os.path.join(work, "models", MODEL + ".xml"), "w", encoding="utf-8") as f:
            f.write(model_xml(rooms))
        with open(os.path.join(work, "models.xml"), "w", encoding="utf-8") as f:
            f.write('<models><model name="%s" length="%d" width="%d" amount="%d" seeds="%s" pixelsize="4"/></models>'
                    % (MODEL, rooms_x * 2 + 1, rooms_y * 2 + 1, len(seeds), " ".join(map(str, seeds))))
        r = subprocess.run([MJ_EXE], cwd=work, capture_output=True, text=True, errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        grids = []
        for s in seeds:
            path = os.path.join(work, "output", "%s_%d.txt" % (MODEL, s))
            if not os.path.exists(path):
                raise RuntimeError("MarkovJunior 실행 실패 (seed %d):\n%s%s" % (s, r.stdout, r.stderr))
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
            fx = int(lines[0].split()[0])
            grids.append([list(lines[1][y * fx:(y + 1) * fx]) for y in range(len(lines[1]) // fx)])
        return grids
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ================================================================== 평면 가공
def dims(grid):
    return (len(grid[0]) - 1) // 2, (len(grid) - 1) // 2


def edge(i, j, di, dj):
    """방 칸 (i,j) 의 (di,dj) 방향 경계 격자 좌표 (x, y)."""
    return 2 * i + 1 + di, 2 * j + 1 + dj


def components(grid, ok, skip=None):
    """경계 문자가 ok 에 속하면 이어진 것으로 보고 칸을 묶는다. 반환: {(i,j): comp_id}"""
    W, H = dims(grid)
    comp, n = {}, 0
    for j in range(H):
        for i in range(W):
            if (i, j) in comp or (i, j) == skip:
                continue
            comp[(i, j)] = n
            q = deque([(i, j)])
            while q:
                ci, cj = q.popleft()
                for di, dj in DIRS:
                    ni, nj = ci + di, cj + dj
                    if not (0 <= ni < W and 0 <= nj < H) or (ni, nj) == skip or (ni, nj) in comp:
                        continue
                    ex, ey = edge(ci, cj, di, dj)
                    if grid[ey][ex] in ok:
                        comp[(ni, nj)] = n
                        q.append((ni, nj))
            n += 1
    return comp


def pick_stair(W, H, rng):
    """계단 칸과 오르는 방향 (i, j, di, dj): (i-di, j-dj) 쪽에서 타서 (i+di, j+dj) 쪽으로 오른다."""
    cands = [(i, j, di, dj) for j in range(H) for i in range(W) for di, dj in DIRS
             if 0 <= i - di < W and 0 <= i + di < W and 0 <= j - dj < H and 0 <= j + dj < H]
    return rng.choice(cands)


def fix_floor(grid, f, floors, stair, rng):
    """한 층 평면에 계단 제약을 넣고 동선을 보정한다."""
    W, H = dims(grid)
    if f > 0:                                   # 정문은 1층만, 위층은 창문
        for row in grid:
            for x, ch in enumerate(row):
                if ch == "F":
                    row[x] = "N"
    if stair is None:
        return
    si, sj, di, dj = stair
    bx, by = edge(si, sj, -di, -dj)     # 타는 쪽
    tx, ty = edge(si, sj, di, dj)       # 도착 쪽
    grid[by][bx] = "D" if f < floors - 1 else "W"      # 이 층에서 위로 오르는 경사로 입구
    grid[ty][tx] = "D" if f > 0 else "W"               # 아래층에서 올라온 경사로 출구
    for sx, sy in (edge(si, sj, dj, di), edge(si, sj, -dj, -di)):   # 옆면은 막는다
        if grid[sy][sx] in PASS:
            grid[sy][sx] = "W"
        elif grid[sy][sx] == "F":
            grid[sy][sx] = "X"
    grid[2 * sj + 1][2 * si + 1] = "S"
    if f == 0 and not any("F" in row for row in grid):        # 정문이 계단 옆이라 사라졌으면 다시
        ext = [(x, y) for y, row in enumerate(grid) for x, ch in enumerate(row)
               if ch in "XN" and not _touches(x, y, si, sj)]
        x, y = rng.choice(ext)
        grid[y][x] = "F"
    # 계단 칸을 뺀 나머지가 하나로 이어지도록 문 추가
    while True:
        comp = components(grid, PASS, skip=(si, sj))
        if len(set(comp.values())) <= 1:
            break
        cand = []
        for (i, j), c in comp.items():
            for ddi, ddj in ((1, 0), (0, 1)):
                n = (i + ddi, j + ddj)
                if n in comp and comp[n] != c:
                    cand.append(edge(i, j, ddi, ddj))
        x, y = rng.choice(cand)
        grid[y][x] = "D"


def _touches(x, y, i, j):
    return abs(x - (2 * i + 1)) + abs(y - (2 * j + 1)) == 1


def find_rooms(grid, stair):
    """방 = 열린 경계(O)로 이어진 칸 묶음 (계단 칸 제외). 반환: [[(i,j), ...], ...]"""
    skip = (stair[0], stair[1]) if stair else None
    comp = components(grid, "O", skip=skip)
    rooms = {}
    for cell, c in comp.items():
        rooms.setdefault(c, []).append(cell)
    return [sorted(v) for _, v in sorted(rooms.items())]


# ================================================================== 조명
CELLS_PER_LIGHT = 3        # 큰 방은 칸 3개당 조명 1개


def light_cells(cells):
    """방 칸 목록 -> 조명 위치(칸 좌표, 실수). 작은 직사각형 방은 중앙 1개,
    큰 방은 서로 가장 멀리 떨어지도록(최원점 샘플링) 여러 개."""
    n = max(1, math.ceil(len(cells) / CELLS_PER_LIGHT))
    cx = sum(i for i, _ in cells) / len(cells)
    cy = sum(j for _, j in cells) / len(cells)
    xs = [i for i, _ in cells]
    ys = [j for _, j in cells]
    rect = len(cells) == (max(xs) - min(xs) + 1) * (max(ys) - min(ys) + 1)
    if n == 1 and rect:
        return [(cx, cy)]
    pts = [min(cells, key=lambda c: (c[0] - cx) ** 2 + (c[1] - cy) ** 2)]
    while len(pts) < n:
        pts.append(max(cells, key=lambda c: min((c[0] - p[0]) ** 2 + (c[1] - p[1]) ** 2 for p in pts)))
    return [(float(i), float(j)) for i, j in pts]


# ================================================================== 트랜스폼
def xf(x, y, z, sx, sy, sz, yaw=0.0, pitch=0.0):
    """(위치, 회전(pitch,yaw,roll), 스케일) — 기본 Cube(100cm) 기준 스케일."""
    return ((x, y, z), (pitch, yaw, 0.0), (sx / 100, sy / 100, sz / 100))


def stair_rim(cx, cy, floors):
    """계단 칸은 위층 바닥(슬래브)이 빠지므로, 벽 윗단~다음 층 바닥 사이(두께 T)가 뚫린다.
    계단 칸 네 변을 따라 층마다 슬래브 두께의 테두리 보를 넣어 막는다."""
    out = []
    for f in range(1, floors):
        z = f * FH - T / 2
        out += [xf(cx - C / 2, cy, z, C + T, T, T, 90), xf(cx + C / 2, cy, z, C + T, T, T, 90),
                xf(cx, cy - C / 2, z, C + T, T, T, 0), xf(cx, cy + C / 2, z, C + T, T, T, 0)]
    return out


def wall_pieces(cx, cy, along_x, z0, kind):
    L = C + T
    yaw = 0 if along_x else 90
    ux, uy = (1, 0) if along_x else (0, 1)

    def seg(off, length, zb, h):
        return xf(cx + ux * off, cy + uy * off, zb + h / 2, length, T, h, yaw)

    if kind == "W":
        return [seg(0, L, z0, WH)]
    ow = DOOR_W if kind == "D" else WIN_W
    side = (L - ow) / 2
    out = [seg(-(ow + side) / 2, side, z0, WH), seg((ow + side) / 2, side, z0, WH)]
    if kind == "D":
        out.append(seg(0, ow, z0 + DOOR_H, WH - DOOR_H))
    else:
        out.append(seg(0, ow, z0, WIN_SILL))
        out.append(seg(0, ow, z0 + WIN_SILL + WIN_H, WH - WIN_SILL - WIN_H))
    return out


def plan(rooms_x, rooms_y, floors, seed, rooms=None):
    """층별 평면(격자)만 만든다. GUI 에서 이 격자를 직접 편집한 뒤 assemble() 한다."""
    rng = random.Random(seed)
    floor_seeds = rng.sample(range(1, 2**31 - 1), floors)          # 층마다 다른 seed
    grids = run_markov(rooms_x, rooms_y, floor_seeds, rooms)
    stair = pick_stair(rooms_x, rooms_y, rng) if floors > 1 else None
    for f, g in enumerate(grids):
        fix_floor(g, f, floors, stair, rng)
    return {"grids": grids, "stair": stair, "seed": seed, "floor_seeds": floor_seeds,
            "size": (rooms_x, rooms_y)}


def stair_ok(W, H, stair):
    i, j, di, dj = stair
    return 0 <= i - di < W and 0 <= i + di < W and 0 <= j - dj < H and 0 <= j + dj < H


def move_stair(p, new):
    """계단 칸/방향을 바꾼다. 편집한 벽·문은 유지하고 계단 주변과 동선만 다시 맞춘다."""
    old = p["stair"]
    rng = random.Random(p["seed"] + 1)
    for f, g in enumerate(p["grids"]):
        if old:
            g[2 * old[1] + 1][2 * old[0] + 1] = "r"          # 예전 계단 칸은 일반 칸으로
        fix_floor(g, f, len(p["grids"]), new, rng)
    p["stair"] = new


def unreachable(p, f):
    """f층에서 입구(1층=정문, 위층=계단 출구)로부터 갈 수 없는 칸 목록."""
    g, stair = p["grids"][f], p["stair"]
    W, H = p["size"]
    skip = stair[:2] if stair else None
    comp = components(g, PASS, skip=skip)
    if f == 0:
        starts = {(i, j) for j in range(H) for i in range(W)
                  for di, dj in DIRS if (i, j) != skip
                  and g[edge(i, j, di, dj)[1]][edge(i, j, di, dj)[0]] == "F"}
    else:
        si, sj, di, dj = stair
        starts = {(si + di, sj + dj)}
    ok = {comp[c] for c in starts if c in comp}
    return [c for c, k in comp.items() if k not in ok]


def cell_of(p, x, y):
    """액터 기준 좌표(cm) -> 방 칸 (i, j). 건물 밖이면 None."""
    W, H = p["size"]
    i, j = math.floor((x + W * C / 2) / C), math.floor((y + H * C / 2) / C)
    return (i, j) if 0 <= i < W and 0 <= j < H else None


def build(rooms_x, rooms_y, floors, seed, rooms=None, item_density=0.5):
    """건물 전체 생성 (plan + assemble)."""
    return assemble(plan(rooms_x, rooms_y, floors, seed, rooms), item_density)


def assemble(p, item_density=0.5):
    """평면 -> 트랜스폼/방/조명/아이템. 반환 dict: walls/floors/stairs/lamps/lights/items(트랜스폼),
    item_rooms, room_floor, room_maps, plans + plan 의 값들"""
    grids, stair, seed, floor_seeds = p["grids"], p["stair"], p["seed"], p["floor_seeds"]
    W, H = p["size"]
    floors = len(grids)
    rng = random.Random(seed * 7 + 1)      # 조명/아이템 배치용 (같은 평면이면 같은 결과)
    ox, oy = -W * C / 2, -H * C / 2        # 액터 원점 = 건물 1층 바닥 중앙
    walls, slabs, ramps, items, item_rooms, room_floor, plans = [], [], [], [], [], [], []
    lamps, lights, room_maps, auto, final = [], [], [], [], []   # final = 층별 실제 조명/아이템 위치

    for f, g in enumerate(grids):
        z0 = f * FH
        for gy, row in enumerate(g):
            for gx, ch in enumerate(row):
                if (gx + gy) % 2 == 0:
                    continue
                kind = {"W": "W", "X": "W", "D": "D", "N": "N", "F": "D"}.get(ch)
                if kind:
                    walls += wall_pieces(ox + gx * C / 2, oy + gy * C / 2, gy % 2 == 0, z0, kind)
        # 방 구분 + 자동 조명/아이템 위치 (아이템: 방마다 칸 수 x 밀도, 최소 1개)
        room_list = find_rooms(g, stair)
        ids = {}
        auto_lights, auto_items = [], []
        for cells in room_list:
            rid = len(room_floor)
            room_floor.append(f)
            for cell in cells:
                ids[cell] = rid
            for li, lj in light_cells(cells):
                auto_lights.append((ox + (li + 0.5) * C, oy + (lj + 0.5) * C))
            n = max(1, round(len(cells) * item_density))
            for (i, j) in rng.sample(cells, min(n, len(cells))):
                auto_items.append((ox + (i + 0.5) * C + rng.uniform(-110, 110),
                                   oy + (j + 0.5) * C + rng.uniform(-110, 110), rng.uniform(0, 360)))
        room_maps.append(ids)
        # GUI 에서 손으로 고친 층은 그 위치를 그대로 쓴다 (계단 칸/건물 밖은 버림)
        fl = [(x, y) for x, y in p.get("manual_lights", {}).get(f, auto_lights) if cell_of(p, x, y) in ids]
        fi = [(x, y, a) for x, y, a in p.get("manual_items", {}).get(f, auto_items) if cell_of(p, x, y) in ids]
        for lx, ly in fl:                               # 천장등: 등기구 메시 + 그 아래 포인트 라이트
            lamps.append(xf(lx, ly, z0 + WH - 3, 60, 60, 6))
            lights.append(xf(lx, ly, z0 + WH - 40, 100, 100, 100))
        for px, py, yaw in fi:
            items.append(xf(px, py, z0 + 20, 100, 100, 100, yaw))
            item_rooms.append(ids[cell_of(p, px, py)])
        auto.append({"lights": auto_lights, "items": auto_items})
        final.append({"lights": fl, "items": fi})
        plans.append((f, floor_seeds[f], pretty(g, ids)))

    for f in range(floors + 1):          # 바닥 슬래브: 1층 ~ 지붕, 계단 칸은 위층 바닥에 구멍
        for j in range(H):
            for i in range(W):
                if stair and (i, j) == stair[:2] and 1 <= f <= floors - 1:
                    continue
                x0 = ox + i * C - (T / 2 if i == 0 else 0)
                x1 = ox + (i + 1) * C + (T / 2 if i == W - 1 else 0)
                y0 = oy + j * C - (T / 2 if j == 0 else 0)
                y1 = oy + (j + 1) * C + (T / 2 if j == H - 1 else 0)
                slabs.append(xf((x0 + x1) / 2, (y0 + y1) / 2, f * FH - T / 2, x1 - x0, y1 - y0, T))

    if stair:                            # 경사로: 층마다 같은 칸에 겹쳐 쌓임
        si, sj, di, dj = stair
        ang = math.degrees(math.atan2(FH, C))
        yaw = {(1, 0): 0, (-1, 0): 180, (0, 1): 90, (0, -1): -90}[(di, dj)]
        drop = (T / 2) / math.cos(math.radians(ang))
        for f in range(floors - 1):
            ramps.append(xf(ox + (si + 0.5) * C, oy + (sj + 0.5) * C, f * FH + FH / 2 - drop,
                            math.hypot(C, FH), RAMP_W, T, yaw, ang))
        walls += stair_rim(ox + (si + 0.5) * C, oy + (sj + 0.5) * C, floors)

    return {"walls": walls, "floors": slabs, "stairs": ramps, "items": items,
            "lamps": lamps, "lights": lights, "auto": auto, "final": final,
            "item_rooms": item_rooms, "room_floor": room_floor, "plans": plans,
            "seed": seed, "floor_seeds": floor_seeds,
            "grids": grids, "room_maps": room_maps, "stair": stair, "size": (W, H)}


def pretty(grid, room_ids=None):
    """평면도 문자열. 방 칸은 방 번호(0-9a-z), 계단은 S."""
    sym = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = []
    for y, row in enumerate(grid):
        s = ""
        for x, ch in enumerate(row):
            if x % 2 == 0 and y % 2 == 0:
                s += "+"
            elif x % 2 == 1 and y % 2 == 1:
                rid = room_ids.get(((x - 1) // 2, (y - 1) // 2)) if room_ids else None
                s += "S" if ch == "S" else (sym[rid % 36] if rid is not None else ch)
            else:
                horiz = y % 2 == 0
                s += {"O": " ", "W": "-" if horiz else "|", "X": "=" if horiz else "#",
                      "D": "d", "N": "n", "F": "F"}.get(ch, "?")
        out.append(s)
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    a = [int(v) for v in sys.argv[1:]] + [5, 5, 3, 0][len(sys.argv) - 1:]
    seed = a[3] or random.randrange(1, 2**31 - 1)
    r = build(a[0], a[1], a[2], seed)
    for f, s, p in r["plans"]:
        print(f"--- {f + 1}층 (seed {s})")
        print(p)
    print(f"seed={seed}  벽 {len(r['walls'])} / 바닥 {len(r['floors'])} / 경사로 {len(r['stairs'])} / "
          f"방 {len(r['room_floor'])} / 조명 {len(r['lights'])} / 아이템 지점 {len(r['items'])}")
