"""MJ 건물 생성기 GUI (Dear PyGui).

평면도를 층별로 미리 보고 직접 고친 뒤, [UE에 적용] 으로 레벨의 BP_MJBuilding 에 반영한다 (MCP HTTP).

평면도 편집
  흰 네모 핸들 드래그      가로/세로 칸 수 변경 (홀수로 맞춤, 같은 seed 로 다시 생성)
  경계 클릭                안쪽: 열림 <-> 벽, 문 -> 벽      바깥: 벽 <-> 창문, 정문 -> 벽
  경계 Ctrl + 클릭         문 <-> 벽 (안쪽 문 / 바깥은 1층 정문, 여러 개 가능), 창문 -> 벽
  계단 드래그              다른 칸에 놓으면 계단 이동 (전 층), 제자리 클릭 = 방향 회전
  조명/아이템 드래그       이동          우클릭 = 삭제
  Shift + 클릭 / Alt + 클릭  그 자리에 조명 / 아이템 스폰 지점 추가 (방 안에만)
  Ctrl+Z / [되돌리기]      편집 취소
  방/조명/아이템은 편집할 때마다 다시 계산되고, 갈 수 없는 칸은 빨간 X 로 표시된다.

  python mj_gui.py [--actor <BP_MJBuilding 액터 이름>] [--offline]
  (BP_MJBuilding 디테일 패널의 'Btn Open GUI' 로도 열린다)
"""
import argparse
import copy
import json
import math
import queue
import random
import threading
import urllib.request

import dearpygui.dearpygui as dpg

import sys as _sys
_sys.dont_write_bytecode = True   # 도구 폴더에 __pycache__ 를 남기지 않음

import mj_core  # noqa: E402

BP_CLASS = "/Game/_Jinoh/BluePrint/BP_MJBuilding.BP_MJBuilding_C"
MCP = "http://127.0.0.1:8000/mcp"
FONT = "C:/Windows/Fonts/malgun.ttf"
CANVAS = 620
PAD = 24                 # 평면도 왼쪽/위 여백 (오른쪽/아래는 드래그로 키울 공간)
SIZES = ["3", "5", "7", "9", "11"]
CTRL_KEYS = [getattr(dpg, n) for n in ("mvKey_LControl", "mvKey_RControl", "mvKey_ModCtrl") if hasattr(dpg, n)]
SHIFT_KEYS = [getattr(dpg, n) for n in ("mvKey_LShift", "mvKey_RShift", "mvKey_ModShift") if hasattr(dpg, n)]
ALT_KEYS = [getattr(dpg, n) for n in ("mvKey_LAlt", "mvKey_RAlt", "mvKey_ModAlt") if hasattr(dpg, n)]


def keys_down(keys):
    return any(dpg.is_key_down(k) for k in keys)

PALETTE = [(244, 169, 131), (140, 200, 232), (170, 222, 150), (242, 212, 120), (200, 170, 230),
           (240, 150, 180), (130, 215, 200), (230, 190, 150), (175, 190, 240), (210, 230, 140),
           (250, 200, 210), (160, 210, 170)]
INK = (35, 35, 40)


# ================================================================== Unreal MCP
class UE:
    def __init__(self):
        self.sid = None

    def _post(self, payload):
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.sid:
            h["Mcp-Session-Id"] = self.sid
        req = urllib.request.Request(MCP, json.dumps(payload).encode(), h)
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read().decode("utf-8", "replace")
            self.sid = self.sid or r.headers.get("Mcp-Session-Id")
        if body.startswith(("event:", "data:")):
            body = "\n".join(l[5:] for l in body.splitlines() if l.startswith("data:"))
        return json.loads(body) if body.strip() else None

    def connect(self):
        if self.sid:
            return
        self._post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "mj-gui", "version": "1"}}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def tool(self, toolset, name, args):
        self.connect()
        full = toolset if toolset.startswith("EditorToolset") else "editor_toolset.toolsets." + toolset
        r = self._post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "call_tool", "arguments": {
                            "toolset_name": full, "tool_name": name, "arguments": args}}})["result"]
        text = "\n".join(c.get("text", "") for c in r.get("content", []))
        if r.get("isError"):
            raise RuntimeError(f"{name}: {text[:400]}")
        try:
            return json.loads(text).get("returnValue")
        except (json.JSONDecodeError, AttributeError):
            return text

    def buildings(self):
        acts = self.tool("scene.SceneTools", "find_actors", {
            "name": "", "tag": "", "collision_channels": [], "actor_type": {"refPath": BP_CLASS}}) or []
        return [(self.tool("actor.ActorTools", "get_label", {"actor": a}), a["refPath"]) for a in acts]

    def get(self, path, props):
        return json.loads(self.tool("object.ObjectTools", "get_properties",
                                    {"instance": {"refPath": path}, "properties": props}))

    def set(self, path, values):
        # 배열은 크기와 내용이 동시에 바뀌면 set_properties 가 거부하므로 먼저 비운다
        arrays = {k: [] for k, v in values.items() if isinstance(v, list)}
        if arrays:
            self.tool("object.ObjectTools", "set_properties",
                      {"instance": {"refPath": path}, "values": json.dumps(arrays)})
        return self.tool("object.ObjectTools", "set_properties",
                         {"instance": {"refPath": path}, "values": json.dumps(values)})


def tjson(items):
    return [{"location": {"x": round(l[0], 2), "y": round(l[1], 2), "z": round(l[2], 2)},
             "rotation": {"pitch": round(r[0], 4), "yaw": round(r[1], 4), "roll": 0},
             "scale": {"x": round(s[0], 4), "y": round(s[1], 4), "z": round(s[2], 4)}} for l, r, s in items]


def lerp(a, b, t):
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


# ================================================================== 앱
class App:
    def __init__(self):
        self.ue = UE()
        self.plan = None           # mj_core.plan() 결과 (편집 대상)
        self.result = None         # mj_core.assemble() 결과 (표시/적용 대상)
        self.history = []          # 되돌리기용 plan 사본
        self.edited = False
        self.floor = 0
        self.targets = []          # [(label, path)]
        self.geo = None            # (ox, oy, cs, W, H)
        self.drag = None
        self.lock = threading.Lock()
        self.q = queue.Queue()

    # ---------------------------------------------------------- 스레드/상태
    def post(self, fn):
        """UI 변경은 메인 루프에서만 한다 (Dear PyGui 는 스레드 안전하지 않음)."""
        self.q.put(fn)

    def pump(self):
        while not self.q.empty():
            self.q.get()()

    def status(self, msg, err=False):
        def ui():
            dpg.set_value("status", msg)
            dpg.configure_item("status", color=(255, 110, 110) if err else (200, 230, 200))
        self.post(ui)

    def run_bg(self, fn, done_msg=None, popup=None):
        """UE 통신은 백그라운드 스레드에서 (창이 멈추지 않도록). popup = 결과 알림창 제목."""
        def work():
            with self.lock:
                try:
                    fn()
                    if done_msg:
                        self.status(done_msg)
                    if popup:
                        self.post(lambda: self.popup(popup + " 완료", done_msg))
                except Exception as e:  # noqa: BLE001
                    err = str(e)
                    self.status("오류: %s" % err, True)
                    if popup:
                        self.post(lambda: self.popup(popup + " 실패", err, True))
        threading.Thread(target=work, daemon=True).start()

    def popup(self, title, msg, err=False):
        """(메인 스레드) 가운데에 뜨는 알림창."""
        if dpg.does_item_exist("popup"):
            dpg.delete_item("popup")
        w = 380
        x = (dpg.get_viewport_client_width() - w) // 2
        y = dpg.get_viewport_client_height() // 2 - 90
        with dpg.window(tag="popup", label=title, modal=True, no_resize=True, no_collapse=True,
                        no_move=False, width=w, pos=(x, y)):
            dpg.add_text(msg, wrap=w - 24, color=(255, 120, 120) if err else (190, 235, 200))
            dpg.add_spacer(height=6)
            dpg.add_button(label="확인", width=-1, height=30, callback=lambda: dpg.delete_item("popup"))

    # ---------------------------------------------------------- 생성 / 조립
    def params(self):
        return dict(rx=int(dpg.get_value("rx")), ry=int(dpg.get_value("ry")),
                    floors=max(1, dpg.get_value("floors")), seed=dpg.get_value("seed"),
                    rooms=dpg.get_value("rooms"), density=dpg.get_value("density"))

    def generate(self, new_seed):
        if new_seed or not dpg.get_value("seed"):
            dpg.set_value("seed", random.randrange(1, 2**31 - 1))
        p = self.params()
        try:
            self.plan = mj_core.plan(p["rx"], p["ry"], p["floors"], p["seed"], p["rooms"] or None)
        except Exception as e:  # noqa: BLE001
            self.status("생성 실패: %s" % e, True)
            return
        self.history.clear()
        self.edited = False
        self.floor = min(self.floor, p["floors"] - 1)
        self.rebuild()
        self.status("생성 완료 (seed %d)" % p["seed"])

    def rebuild(self):
        """편집된 평면으로 방/조명/아이템/트랜스폼을 다시 계산하고 다시 그린다."""
        self.result = mj_core.assemble(self.plan, dpg.get_value("density"))
        r = self.result
        dpg.set_value("stats", "seed %d%s   방 %d   조명 %d   아이템 지점 %d\n벽 %d   바닥 %d   경사로 %d"
                      % (r["seed"], " (편집됨)" if self.edited else "", len(r["room_floor"]), len(r["lights"]),
                         len(r["items"]), len(r["walls"]), len(r["floors"]), len(r["stairs"])))
        self.refresh_floor_buttons()
        self.draw()
        blocked = {f + 1: len(mj_core.unreachable(self.plan, f)) for f in range(len(self.plan["grids"]))}
        blocked = {f: n for f, n in blocked.items() if n}
        if blocked:
            self.status("갈 수 없는 칸이 있습니다: " + ", ".join("%d층 %d칸" % kv for kv in blocked.items())
                        + " (빨간 X)", True)
        if dpg.get_value("auto_apply"):
            self.apply()

    # ---------------------------------------------------------- 편집
    def push_undo(self):
        self.history.append(copy.deepcopy(self.plan))
        del self.history[:-50]

    def undo(self):
        if not self.history:
            self.status("되돌릴 편집이 없습니다.")
            return
        self.plan = self.history.pop()
        self.edited = bool(self.history)
        self.rebuild()
        self.status("되돌렸습니다.")

    def edit_edge(self, gx, gy, ctrl=False):
        W, H = self.plan["size"]
        stair = self.plan["stair"]
        outer = gx in (0, 2 * W) or gy in (0, 2 * H)
        cur = self.plan["grids"][self.floor][gy][gx]
        floors = range(len(self.plan["grids"])) if dpg.get_value("all_floors") else [self.floor]
        if stair and mj_core._touches(gx, gy, stair[0], stair[1]):
            if not outer:
                self.status("계단 칸의 안쪽 경계는 계단을 드래그해 옮기거나 클릭해 돌려서 바꾸세요.", True)
                return
            if ctrl and cur != "N":
                self.status("계단 칸에는 정문을 둘 수 없습니다. 클릭하면 벽 <-> 창문이 바뀝니다.", True)
                return
            new = "X" if cur == "N" else "N"        # 계단 칸 바깥 벽: 벽 <-> 창문
        elif ctrl:                                 # Ctrl + 클릭 = 문 <-> 벽 (바깥 문은 정문, 창문은 벽으로)
            if not outer:
                new = "W" if cur == "D" else "D"
            elif cur in "NF":
                new = "X"
            else:
                if self.floor != 0:
                    self.status("바깥 문(정문)은 1층에만 둘 수 있습니다.", True)
                    return
                new, floors = "F", [0]
        elif outer:                                # 클릭: 벽 <-> 창문, 정문 -> 벽
            new = {"X": "N", "N": "X", "F": "X"}[cur]
        else:                                      # 클릭: 열림 <-> 벽, 문 -> 벽
            new = {"O": "W", "W": "O", "D": "W"}[cur]
        if all(self.plan["grids"][f][gy][gx] == new for f in floors):
            return
        self.push_undo()
        for f in floors:
            self.plan["grids"][f][gy][gx] = new
        self.edited = True
        self.rebuild()

    def stair_target(self, i, j):
        """(i,j) 에 계단을 놓을 때의 (i, j, di, dj). 같은 칸이면 다음 방향으로 회전. 불가능하면 None."""
        W, H = self.plan["size"]
        si, sj, di, dj = self.plan["stair"]
        dirs = list(mj_core.DIRS)
        if (i, j) == (si, sj):          # 같은 칸 = 방향 회전
            k = dirs.index((di, dj))
            order = dirs[k + 1:] + dirs[:k + 1]
        else:                           # 이동: 지금 방향 유지, 안 되면 되는 방향
            order = [(di, dj)] + dirs
        new = next(((i, j, a, b) for a, b in order if mj_core.stair_ok(W, H, (i, j, a, b))), None)
        return None if new == self.plan["stair"] else new

    def edit_stair(self, i, j):
        if len(self.plan["grids"]) < 2:
            self.status("1층 건물은 계단이 없습니다.", True)
            return
        new = self.stair_target(i, j)
        if new is None:
            self.status("여기에는 계단을 둘 수 없습니다 (앞뒤로 칸이 있어야 함).", True)
            return
        self.push_undo()
        mj_core.move_stair(self.plan, new)
        self.edited = True
        self.rebuild()

    # ---------------------------------------------------------- 조명 / 아이템 편집
    def to_px(self, x, y):
        ox, oy, cs, W, H = self.geo
        return ox + (x + W * mj_core.C / 2) / mj_core.C * cs, oy + (y + H * mj_core.C / 2) / mj_core.C * cs

    def to_local(self, px, py):
        ox, oy, cs, W, H = self.geo
        return (px - ox) / cs * mj_core.C - W * mj_core.C / 2, (py - oy) / cs * mj_core.C - H * mj_core.C / 2

    def markers(self, kind):
        """현재 층에 실제로 놓인 조명('lights') / 아이템('items') 위치."""
        return self.result["final"][self.floor][kind]

    def marker_ok(self, x, y):
        return mj_core.cell_of(self.plan, x, y) in self.result["room_maps"][self.floor]

    def edit_marker(self, kind, op, idx=None, pos=None):
        """op: 'add' | 'move' | 'del'. 고친 층은 자동 배치 대신 이 목록을 쓴다."""
        name = "조명" if kind == "lights" else "아이템"
        if op in ("add", "move") and not self.marker_ok(*pos):
            self.status("%s은(는) 방 안에만 둘 수 있습니다 (계단 칸, 건물 밖 불가)." % name, True)
            self.draw()
            return
        self.push_undo()
        lst = [tuple(v) for v in self.markers(kind)]
        if op == "add":
            lst.append(pos if kind == "lights" else (pos[0], pos[1], random.uniform(0, 360)))
        elif op == "move":
            lst[idx] = pos if kind == "lights" else (pos[0], pos[1], lst[idx][2])
        else:
            lst.pop(idx)
        self.plan.setdefault("manual_" + kind, {})[self.floor] = lst
        self.edited = True
        self.rebuild()
        self.status("%d층 %s %s - 이 층 %s %d개"
                    % (self.floor + 1, name, {"add": "추가", "move": "이동", "del": "삭제"}[op], name, len(lst)))

    def hit_marker(self, mx, my):
        """마우스 아래의 조명/아이템 -> (kind, index) | None"""
        if not self.result:
            return None
        for kind, show in (("items", "show_items"), ("lights", "show_lights")):
            if not dpg.get_value(show):
                continue
            for k, v in enumerate(self.markers(kind)):
                px, py = self.to_px(v[0], v[1])
                if abs(mx - px) <= 8 and abs(my - py) <= 8:
                    return kind, k
        return None

    def on_right_click(self):
        if not self.plan or not dpg.is_item_hovered("canvas_bg"):
            return
        m = self.hit_marker(*dpg.get_drawing_mouse_pos())
        if m:
            self.edit_marker(m[0], "del", idx=m[1])

    # ---------------------------------------------------------- 마우스
    def hit(self, mx, my):
        """마우스 위치 -> ('handle', kind) | ('stair', i, j) | ('edge', gx, gy) | ('cell', i, j) | None"""
        if not self.geo:
            return None
        ox, oy, cs, W, H = self.geo
        for kind, (hx, hy) in self.handles().items():
            if abs(mx - hx) <= 10 and abs(my - hy) <= 10:
                return ("handle", kind)
        u, v = (mx - ox) / cs, (my - oy) / cs
        if not (-0.2 <= u <= W + 0.2 and -0.2 <= v <= H + 0.2):
            return None
        st = self.plan["stair"] if self.plan else None
        if (st and 0 <= u < W and 0 <= v < H and (int(u), int(v)) == st[:2]
                and min(u % 1, 1 - u % 1, v % 1, 1 - v % 1) > 0.12):
            return ("stair", st[0], st[1])
        best = None
        xi, j = round(u), math.floor(v)
        if 0 <= xi <= W and 0 <= j < H:
            best = (abs(u - xi), ("edge", 2 * xi, 2 * j + 1))
        yj, i = round(v), math.floor(u)
        if 0 <= yj <= H and 0 <= i < W and (best is None or abs(v - yj) < best[0]):
            best = (abs(v - yj), ("edge", 2 * i + 1, 2 * yj))
        if best and best[0] < 0.2:
            return best[1]
        if 0 <= u < W and 0 <= v < H:
            return ("cell", int(u), int(v))
        return None

    def handles(self):
        ox, oy, cs, W, H = self.geo
        return {"x": (ox + W * cs, oy + H * cs / 2), "y": (ox + W * cs / 2, oy + H * cs),
                "xy": (ox + W * cs, oy + H * cs)}

    def on_click(self):
        if not self.plan or not dpg.is_item_hovered("canvas_bg"):
            return
        mx, my = dpg.get_drawing_mouse_pos()
        h = self.hit(mx, my)
        if not h:
            return
        if h[0] != "handle" and (keys_down(SHIFT_KEYS) or keys_down(ALT_KEYS)):
            self.edit_marker("lights" if keys_down(SHIFT_KEYS) else "items", "add", pos=self.to_local(mx, my))
            return
        m = self.hit_marker(mx, my) if h[0] != "handle" else None
        if m:
            self.drag = {"kind": "marker", "what": m[0], "idx": m[1], "start": (mx, my)}
        elif h[0] == "handle":
            ox, oy, cs, W, H = self.geo
            self.drag = {"kind": h[1], "W": W, "H": H}
        elif h[0] == "stair":
            if len(self.plan["grids"]) > 1:
                self.drag = {"kind": "stair", "from": (h[1], h[2])}
        elif h[0] == "edge":
            self.edit_edge(h[1], h[2], ctrl=any(dpg.is_key_down(k) for k in CTRL_KEYS))

    def cell_at(self, mx, my):
        ox, oy, cs, W, H = self.geo
        return (min(W - 1, max(0, int((mx - ox) // cs))), min(H - 1, max(0, int((my - oy) // cs))))

    def drag_size(self, mx, my):
        ox, oy, cs, W, H = self.geo
        max_w = int((CANVAS - 8 - ox) // cs)
        max_h = int((CANVAS - 8 - oy) // cs)

        def snap(val, lo_hi):
            n = int(round(val))
            n = max(3, min(11, lo_hi, n))
            return n if n % 2 else (n - 1 if n - 1 >= 3 else n + 1)
        nw = snap((mx - ox) / cs, max_w) if "x" in self.drag["kind"] else W
        nh = snap((my - oy) / cs, max_h) if "y" in self.drag["kind"] else H
        return nw, nh

    def on_move(self):
        if not self.geo:
            return
        dpg.delete_item("overlay", children_only=True)
        mx, my = dpg.get_drawing_mouse_pos()
        ox, oy, cs, W, H = self.geo
        if self.drag and self.drag["kind"] == "marker":
            ok = self.marker_ok(*self.to_local(mx, my))
            col = (120, 230, 140) if ok else (240, 80, 80)
            if self.drag["what"] == "lights":
                dpg.draw_circle((mx, my), 9, color=col, fill=(255, 230, 90, 200), thickness=2, parent="overlay")
            else:
                dpg.draw_rectangle((mx - 6, my - 6), (mx + 6, my + 6), color=col, fill=(40, 170, 70, 200),
                                   thickness=2, parent="overlay")
            return
        if self.drag and self.drag["kind"] == "stair":
            i, j = self.cell_at(mx, my)
            tgt = self.stair_target(i, j) if (i, j) != self.drag["from"] else self.plan["stair"]
            x0, y0 = ox + i * cs, oy + j * cs
            col = (120, 230, 140) if tgt else (240, 80, 80)
            dpg.draw_rectangle((x0 + 2, y0 + 2), (x0 + cs - 2, y0 + cs - 2), color=col, fill=col + (70,),
                               thickness=3, parent="overlay")
            if tgt:
                cx, cy, L = x0 + cs / 2, y0 + cs / 2, cs * 0.33
                dpg.draw_arrow((cx + tgt[2] * L, cy + tgt[3] * L), (cx - tgt[2] * L, cy - tgt[3] * L),
                               color=(255, 255, 255), thickness=3, size=9, parent="overlay")
            return
        if self.drag:
            nw, nh = self.drag_size(mx, my)
            dpg.draw_rectangle((ox, oy), (ox + nw * cs, oy + nh * cs), color=(255, 255, 255), thickness=2,
                               parent="overlay")
            dpg.draw_text((ox + nw * cs - 70, oy + nh * cs + 4), "%d x %d" % (nw, nh), size=18,
                          color=(255, 255, 255), parent="overlay")
            return
        if not dpg.is_item_hovered("canvas_bg"):
            return
        h = self.hit(mx, my)
        if not h:
            return
        m = self.hit_marker(mx, my) if h[0] != "handle" else None
        if m:
            v = self.markers(m[0])[m[1]]
            dpg.draw_circle(self.to_px(v[0], v[1]), 11, color=(255, 255, 255), thickness=2, parent="overlay")
        elif h[0] == "handle":
            x, y = self.handles()[h[1]]
            dpg.draw_rectangle((x - 9, y - 9), (x + 9, y + 9), color=(255, 220, 90), thickness=2, parent="overlay")
        elif h[0] == "edge":
            gx, gy = h[1], h[2]
            horiz = gy % 2 == 0
            a = (ox + (gx - 1 if horiz else gx) * cs / 2, oy + (gy if horiz else gy - 1) * cs / 2)
            b = (ox + (gx + 1 if horiz else gx) * cs / 2, oy + (gy if horiz else gy + 1) * cs / 2)
            dpg.draw_line(a, b, color=(255, 220, 90), thickness=7, parent="overlay")
        elif h[0] == "cell":
            x0, y0 = ox + h[1] * cs, oy + h[2] * cs
            dpg.draw_rectangle((x0 + 3, y0 + 3), (x0 + cs - 3, y0 + cs - 3), color=(255, 220, 90), thickness=3,
                               parent="overlay")

    def on_release(self):
        if not self.drag:
            return
        mx, my = dpg.get_drawing_mouse_pos()
        if self.drag["kind"] == "marker":
            d, self.drag = self.drag, None
            dpg.delete_item("overlay", children_only=True)
            if abs(mx - d["start"][0]) + abs(my - d["start"][1]) > 3:     # 그냥 클릭이면 무시
                self.edit_marker(d["what"], "move", idx=d["idx"], pos=self.to_local(mx, my))
            return
        if self.drag["kind"] == "stair":
            self.drag = None
            dpg.delete_item("overlay", children_only=True)
            self.edit_stair(*self.cell_at(mx, my))       # 제자리면 회전, 다른 칸이면 이동
            return
        nw, nh = self.drag_size(mx, my)
        changed = (nw, nh) != (self.drag["W"], self.drag["H"])
        self.drag = None
        dpg.delete_item("overlay", children_only=True)
        if changed:
            dpg.set_value("rx", str(nw))
            dpg.set_value("ry", str(nh))
            had_edits = self.edited
            self.generate(False)
            self.status("크기를 %d x %d 로 바꿨습니다 (같은 seed)%s" % (nw, nh, " - 이전 편집은 초기화됨" if had_edits else ""))

    # ---------------------------------------------------------- UE 연동
    def target_path(self):
        label = dpg.get_value("target")
        return next((p for l, p in self.targets if l == label), None)

    def set_targets(self, targets, pick=None):
        self.targets = targets
        labels = [l for l, _ in targets]
        dpg.configure_item("target", items=labels)
        if pick is None and labels and dpg.get_value("target") not in labels:
            pick = labels[0]
        if pick:
            dpg.set_value("target", pick)

    def refresh_targets(self, select_name=None):
        def work():
            targets = self.ue.buildings()
            pick = next((l for l, p in targets if select_name and p.endswith("." + select_name)), None)

            def ui():
                self.set_targets(targets, pick)
                if pick:                      # --actor 로 열렸으면 그 건물 설정을 불러온다
                    self.load_from_target()
            self.post(ui)
            self.status("UE 연결됨 - 건물 %d개" % len(targets))
        self.run_bg(work)

    def use_selected(self):
        def work():
            sel = [a["refPath"] for a in
                   (self.ue.tool("EditorToolset.EditorAppToolset", "GetSelectedActors", {}) or [])]
            targets = self.ue.buildings()
            hit = next((l for l, p in targets if p in sel), None)
            self.post(lambda: self.set_targets(targets, hit))
            self.status("선택된 건물: " + hit if hit else "선택된 액터 중 BP_MJBuilding 이 없습니다.", not hit)
        self.run_bg(work)

    def load_from_target(self):
        path, label = self.target_path(), dpg.get_value("target")

        def work():
            if not path:
                raise RuntimeError("대상 건물이 없습니다.")
            v = self.ue.get(path, ["RoomsX", "RoomsY", "NumFloors", "Seed", "RoomCount", "ItemDensity", "LastSeed"])

            def ui():
                dpg.set_value("rx", str(v["RoomsX"] or 5))
                dpg.set_value("ry", str(v["RoomsY"] or 5))
                dpg.set_value("floors", v["NumFloors"] or 3)
                dpg.set_value("rooms", v["RoomCount"])
                dpg.set_value("density", v["ItemDensity"] or 0.5)
                dpg.set_value("seed", v["LastSeed"] or v["Seed"])
                if v["LastSeed"]:
                    self.generate(False)
                self.status("'%s' 설정을 불러왔습니다 (편집 내용은 UE 에만 저장되어 있어 seed 기준으로 다시 그림)." % label)
            self.post(ui)
        self.run_bg(work)

    def apply(self):
        if not self.result:
            self.status("먼저 평면을 생성하세요.", True)
            return
        r, p, path = self.result, self.params(), self.target_path()
        W, H = r["size"]

        def work():
            if not path:
                raise RuntimeError("대상 건물이 없습니다. [새 건물 배치] 또는 [새로고침] 하세요.")
            self.ue.set(path, {
                "RoomsX": W, "RoomsY": H, "NumFloors": len(r["grids"]),
                "RoomCount": p["rooms"], "ItemDensity": p["density"], "LastSeed": r["seed"],
                "ItemSpawnPoints": tjson(r["items"]), "ItemSpawnRoomIds": r["item_rooms"],
                "RoomFloors": r["room_floor"], "WallTransforms": tjson(r["walls"]),
                "FloorTransforms": tjson(r["floors"]), "StairTransforms": tjson(r["stairs"]),
                "LampTransforms": tjson(r["lamps"]), "LightTransforms": tjson(r["lights"])})
        self.status("UE에 적용 중...")
        label = dpg.get_value("target") or "?"
        msg = ("'%s' 에 적용했습니다.\n\nseed %d%s · %d x %d · %d층\n방 %d · 조명 %d · 아이템 지점 %d"
               % (label, r["seed"], " (편집 포함)" if self.edited else "", W, H, len(r["grids"]),
                  len(r["room_floor"]), len(r["lights"]), len(r["items"])))
        # 자동 적용은 편집할 때마다 불리므로 알림창 없이 상태 줄에만 표시
        self.run_bg(work, msg, None if dpg.get_value("auto_apply") else "UE 적용")

    def clear_target(self):
        path = self.target_path()

        def work():
            if not path:
                raise RuntimeError("대상 건물이 없습니다.")
            self.ue.set(path, {k: [] for k in ("ItemSpawnPoints", "ItemSpawnRoomIds", "RoomFloors",
                                               "WallTransforms", "FloorTransforms", "StairTransforms",
                                               "LampTransforms", "LightTransforms")})
        self.run_bg(work, "건물을 비웠습니다.")

    def spawn_new(self):
        n = len(self.targets) + 1

        def work():
            cam = self.ue.tool("EditorToolset.EditorAppToolset", "GetCameraTransform", {})
            loc, yaw = cam["location"], math.radians(cam["rotation"]["yaw"])
            x, y = loc["x"] + math.cos(yaw) * 3000, loc["y"] + math.sin(yaw) * 3000
            d = self.ue.tool("scene.SceneTools", "trace_world", {
                "start": {"x": x, "y": y, "z": loc["z"] + 5000}, "end": {"x": x, "y": y, "z": loc["z"] - 20000}})
            try:
                z = loc["z"] + 5000 - float(d)
            except (TypeError, ValueError):
                z = 0.0
            label = "MJBuilding_%d" % n
            a = self.ue.tool("scene.SceneTools", "add_to_scene_from_class", {
                "actor_type": {"refPath": BP_CLASS}, "name": label, "xform": {"location": {"x": x, "y": y, "z": z}}})
            self.ue.tool("actor.ActorTools", "set_label", {"actor": a, "label": label})
            self.ue.tool("scene.SceneTools", "set_actor_folder", {"actor": a, "folder_path": "MJBuildings"})
            targets = self.ue.buildings()
            self.post(lambda: self.set_targets(targets, label))
            self.status("카메라 앞에 '%s' 배치 - 이제 [UE에 적용] 하세요." % label)
        self.run_bg(work)

    # ---------------------------------------------------------- 그리기
    def refresh_floor_buttons(self):
        n = len(self.plan["grids"]) if self.plan else 0
        dpg.delete_item("floor_bar", children_only=True)
        for f in range(n):
            dpg.add_button(label=("[%d층]" if f == self.floor else "%d층") % (f + 1), parent="floor_bar", width=58,
                           callback=lambda s, a, u: self.set_floor(u), user_data=f)

    def set_floor(self, f):
        self.floor = f
        self.refresh_floor_buttons()
        self.draw()

    def draw(self):
        dpg.delete_item("canvas", children_only=True)
        dpg.delete_item("overlay", children_only=True)
        if not self.result:
            return
        r = self.result
        W, H = r["size"]
        g = self.plan["grids"][self.floor]
        ids = r["room_maps"][self.floor]
        cs = min(72.0, (CANVAS - PAD - 40) / W, (CANVAS - PAD - 40) / H)
        ox = oy = PAD
        self.geo = (ox, oy, cs, W, H)

        def P(gx, gy):                     # 2배 격자 좌표 -> 픽셀
            return ox + gx * cs / 2, oy + gy * cs / 2

        for (i, j), rid in ids.items():    # 방 칸
            dpg.draw_rectangle(P(2 * i, 2 * j), P(2 * i + 2, 2 * j + 2), fill=PALETTE[rid % len(PALETTE)] + (255,),
                               color=(0, 0, 0, 0), parent="canvas")
        seen = set()                       # 방 번호
        for (i, j), rid in sorted(ids.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            if rid not in seen:
                seen.add(rid)
                x0, y0 = P(2 * i, 2 * j)
                dpg.draw_text((x0 + 4, y0 + 2), "방 %d" % rid, size=14, color=(40, 40, 40), parent="canvas")
        if self.plan["stair"]:             # 계단
            si, sj, di, dj = self.plan["stair"]
            (x0, y0), (x1, y1) = P(2 * si, 2 * sj), P(2 * si + 2, 2 * sj + 2)
            dpg.draw_rectangle((x0, y0), (x1, y1), fill=(120, 120, 130, 255), color=(0, 0, 0, 0), parent="canvas")
            cx, cy, L = (x0 + x1) / 2, (y0 + y1) / 2, cs * 0.33
            dpg.draw_arrow((cx + di * L, cy + dj * L), (cx - di * L, cy - dj * L), color=(255, 255, 255),
                           thickness=3, size=9, parent="canvas")
            up = self.floor < len(self.plan["grids"]) - 1
            dpg.draw_text((x0 + 4, y0 + 2), "계단" if up else "도착", size=14, color=(255, 255, 255), parent="canvas")
        for gy, row in enumerate(g):       # 경계
            for gx, ch in enumerate(row):
                if (gx + gy) % 2 == 0 or ch == "O":
                    continue
                horiz = gy % 2 == 0
                a = P(gx - 1, gy) if horiz else P(gx, gy - 1)
                b = P(gx + 1, gy) if horiz else P(gx, gy + 1)
                if ch in "WX":
                    dpg.draw_line(a, b, color=INK, thickness=4 if ch == "X" else 3, parent="canvas")
                elif ch in "DF":
                    dpg.draw_line(a, lerp(a, b, 0.35), color=INK, thickness=3, parent="canvas")
                    dpg.draw_line(lerp(a, b, 0.65), b, color=INK, thickness=3, parent="canvas")
                    if ch == "F":
                        dpg.draw_line(lerp(a, b, 0.35), lerp(a, b, 0.65), color=(220, 60, 60), thickness=6,
                                      parent="canvas")
                elif ch == "N":
                    dpg.draw_line(a, b, color=INK, thickness=4, parent="canvas")
                    dpg.draw_line(lerp(a, b, 0.3), lerp(a, b, 0.7), color=(80, 160, 255), thickness=6, parent="canvas")
        wx0, wy0 = -W * mj_core.C / 2, -H * mj_core.C / 2   # 조명 / 아이템 (월드 좌표 -> 픽셀)

        def world(x, y):
            return ox + (x - wx0) / mj_core.C * cs, oy + (y - wy0) / mj_core.C * cs

        fin = r["final"][self.floor]
        if dpg.get_value("show_lights"):
            for x, y in fin["lights"]:
                dpg.draw_circle(world(x, y), 6, fill=(255, 230, 90, 255), color=(160, 120, 0), parent="canvas")
        if dpg.get_value("show_items"):
            for x, y, _ in fin["items"]:
                c = world(x, y)
                dpg.draw_rectangle((c[0] - 4, c[1] - 4), (c[0] + 4, c[1] + 4), fill=(40, 170, 70, 255),
                                   color=(0, 90, 30), parent="canvas")
        for i, j in mj_core.unreachable(self.plan, self.floor):   # 갈 수 없는 칸
            (x0, y0), (x1, y1) = P(2 * i, 2 * j), P(2 * i + 2, 2 * j + 2)
            dpg.draw_line((x0 + 8, y0 + 8), (x1 - 8, y1 - 8), color=(230, 40, 40), thickness=3, parent="canvas")
            dpg.draw_line((x1 - 8, y0 + 8), (x0 + 8, y1 - 8), color=(230, 40, 40), thickness=3, parent="canvas")
        for x, y in self.handles().values():                        # 크기 조절 핸들
            dpg.draw_rectangle((x - 6, y - 6), (x + 6, y + 6), fill=(245, 245, 245, 255), color=(90, 90, 90),
                               parent="canvas")
        dpg.set_value("floor_label", "%d층 / %d층   (floor seed %d)   가로 %d x 세로 %d"
                      % (self.floor + 1, len(self.plan["grids"]), r["floor_seeds"][self.floor], W, H))


# ================================================================== UI
def build_ui(app):
    with dpg.font_registry():
        font = dpg.add_font(FONT, 17)
    dpg.bind_font(font)

    with dpg.window(tag="main"):
        with dpg.group(horizontal=True):
            with dpg.child_window(width=330, border=False):
                dpg.add_text("건물 설정", color=(150, 200, 255))
                dpg.add_combo(SIZES, default_value="5", label="가로 칸 (X)", tag="rx", width=120)
                dpg.add_combo(SIZES, default_value="5", label="세로 칸 (Y)", tag="ry", width=120)
                dpg.add_slider_int(label="층 수", tag="floors", default_value=3, min_value=1, max_value=8, width=160)
                dpg.add_input_int(label="방 개수 (0=자동)", tag="rooms", default_value=0, min_value=0,
                                  min_clamped=True, width=120)
                dpg.add_slider_float(label="아이템 밀도", tag="density", default_value=0.5, min_value=0.0,
                                     max_value=1.5, width=160, format="%.2f",
                                     callback=lambda: app.plan and app.rebuild())
                with dpg.group(horizontal=True):
                    dpg.add_input_int(label="Seed", tag="seed", default_value=0, width=150, step=0)
                    dpg.add_button(label="랜덤", callback=lambda: dpg.set_value("seed", random.randrange(1, 2**31 - 1)))
                dpg.add_spacer(height=4)
                dpg.add_button(label="재생성 (새 랜덤 시드)", width=300, height=34, callback=lambda: app.generate(True))
                dpg.add_button(label="현재 설정/Seed 로 다시 생성", width=300, callback=lambda: app.generate(False))
                dpg.add_separator()
                dpg.add_text("언리얼 에디터", color=(150, 200, 255))
                dpg.add_combo([], label="대상", tag="target", width=200)
                with dpg.group(horizontal=True):
                    dpg.add_button(label="새로고침", callback=lambda: app.refresh_targets())
                    dpg.add_button(label="선택된 액터", callback=app.use_selected)
                    dpg.add_button(label="설정 불러오기", callback=app.load_from_target)
                dpg.add_button(label="UE에 적용", width=300, height=34, callback=app.apply)
                dpg.add_text("", tag="status", wrap=310)
                dpg.add_checkbox(label="바꿀 때마다 자동 적용", tag="auto_apply")
                with dpg.group(horizontal=True):
                    dpg.add_button(label="새 건물 배치 (카메라 앞)", callback=app.spawn_new)
                    dpg.add_button(label="비우기", callback=app.clear_target)
                dpg.add_separator()
                dpg.add_text("", tag="stats", wrap=310)
                dpg.add_spacer(height=4)
                dpg.add_text("범례", color=(150, 200, 255))
                dpg.add_text("색 = 방   회색 = 계단 (화살표: 오르는 방향)\n"
                             "검은 선 = 벽   틈 = 문   빨강 = 정문   파랑 = 창문\n"
                             "노란 점 = 조명   초록 = 아이템   빨간 X = 갈 수 없음", wrap=310)
            with dpg.group():
                with dpg.group(horizontal=True):
                    dpg.add_group(horizontal=True, tag="floor_bar")
                    dpg.add_checkbox(label="조명", tag="show_lights", default_value=True, callback=lambda: app.draw())
                    dpg.add_checkbox(label="아이템", tag="show_items", default_value=True, callback=lambda: app.draw())
                with dpg.group(horizontal=True):
                    dpg.add_checkbox(label="벽/문 편집을 모든 층에", tag="all_floors")
                    dpg.add_button(label="되돌리기", callback=app.undo)
                dpg.add_text("", tag="floor_label", color=(170, 170, 180))
                with dpg.drawlist(width=CANVAS, height=CANVAS, tag="canvas_bg"):
                    dpg.draw_rectangle((0, 0), (CANVAS, CANVAS), fill=(30, 32, 38, 255), color=(60, 60, 70))
                    dpg.add_draw_node(tag="canvas")
                    dpg.add_draw_node(tag="overlay")
                dpg.add_text("핸들(흰 네모) 드래그 = 크기   경계 클릭 = 열림/벽, 바깥은 벽/창문   "
                             "Ctrl+클릭 = 문 <-> 벽 (바깥 벽은 1층 정문, 창문은 벽)   "
                             "계단 드래그 = 이동, 계단 클릭 = 회전   조명/아이템 드래그 = 이동, 우클릭 = 삭제   "
                             "Shift+클릭 = 조명 추가   Alt+클릭 = 아이템 추가   Ctrl+Z = 되돌리기",
                             color=(150, 150, 160), wrap=CANVAS)

    with dpg.handler_registry():
        dpg.add_mouse_click_handler(button=dpg.mvMouseButton_Left, callback=lambda: app.on_click())
        dpg.add_mouse_move_handler(callback=lambda: app.on_move())
        dpg.add_mouse_release_handler(button=dpg.mvMouseButton_Left, callback=lambda: app.on_release())
        dpg.add_mouse_click_handler(button=dpg.mvMouseButton_Right, callback=lambda: app.on_right_click())
        dpg.add_key_press_handler(key=dpg.mvKey_Z,
                                  callback=lambda: any(dpg.is_key_down(k) for k in CTRL_KEYS) and app.undo())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--actor", help="대상 BP_MJBuilding 액터 이름")
    ap.add_argument("--offline", action="store_true", help="UE 에 연결하지 않음 (평면만)")
    ap.add_argument("--shot", help="(테스트용) 화면을 PNG 로 저장하고 종료")
    ap.add_argument("--selftest", action="store_true", help="(테스트용) 편집 기능을 자동으로 한 번씩 실행")
    a = ap.parse_args()

    dpg.create_context()
    app = App()
    build_ui(app)
    # 창 제목에 한글을 넣으면 Dear PyGui 2.x 가 Windows 에서 크래시한다
    dpg.create_viewport(title="MJ Building Generator", width=1010, height=850)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window("main", True)

    if not a.actor:
        app.generate(True)
    if not a.offline:
        app.refresh_targets(a.actor)  # --actor 면 목록을 받은 뒤 그 건물 설정을 불러온다

    frame = 0
    while dpg.is_dearpygui_running():
        app.pump()
        dpg.render_dearpygui_frame()
        frame += 1
        if a.selftest and frame == 5:
            selftest(app)
        if a.shot and frame == 240:
            dpg.output_frame_buffer(file=a.shot)
        if a.shot and frame == 250:
            break
    dpg.destroy_context()


def selftest(app):
    """편집 기능 자동 점검 (마우스 대신 같은 함수 호출)."""
    W, H = app.plan["size"]
    inner = next((gx, gy) for gy, row in enumerate(app.plan["grids"][0]) for gx, ch in enumerate(row)
                 if (gx + gy) % 2 and ch == "W" and 0 < gx < 2 * W and 0 < gy < 2 * H
                 and not mj_core._touches(gx, gy, *app.plan["stair"][:2]))
    g0 = app.plan["grids"][0]
    app.edit_edge(*inner, ctrl=True)                       # Ctrl+클릭: 벽 -> 문
    assert g0[inner[1]][inner[0]] == "D"
    app.edit_edge(*inner, ctrl=True)                       # Ctrl+클릭: 문 -> 벽
    assert app.plan["grids"][0][inner[1]][inner[0]] == "W"
    app.edit_edge(*inner, ctrl=True)                       # 다시 문
    app.edit_edge(*inner)                                  # 클릭: 문 -> 벽
    assert app.plan["grids"][0][inner[1]][inner[0]] == "W"
    app.edit_edge(*inner)                                  # 클릭: 벽 -> 열림
    assert app.plan["grids"][0][inner[1]][inner[0]] == "O"
    app.plan["grids"][0][1][2 * W] = "N"
    app.edit_edge(2 * W, 1, ctrl=True)                     # Ctrl+클릭: 창문 -> 벽
    assert app.plan["grids"][0][1][2 * W] == "X"
    app.edit_edge(2 * W, 1, ctrl=True)                     # Ctrl+클릭: 벽 -> 정문
    assert app.plan["grids"][0][1][2 * W] == "F"
    app.edit_edge(2 * W, 1)                                # 클릭: 정문 -> 벽
    assert app.plan["grids"][0][1][2 * W] == "X"
    app.edit_edge(2 * W, 1)                                # 클릭: 벽 -> 창문
    assert app.plan["grids"][0][1][2 * W] == "N"
    app.set_floor(1)
    app.plan["grids"][1][1][2 * W] = "X"
    app.edit_edge(2 * W, 1, ctrl=True)                     # 2층 바깥 벽 Ctrl+클릭 -> 정문 거부
    assert app.plan["grids"][1][1][2 * W] == "X"
    app.plan["grids"][1][1][2 * W] = "N"
    app.edit_edge(2 * W, 1, ctrl=True)                     # 2층 창문 Ctrl+클릭 -> 벽 (허용)
    assert app.plan["grids"][1][1][2 * W] == "X"
    app.set_floor(0)
    app.edit_stair(0, H // 2)                              # 계단을 왼쪽 바깥 벽 쪽으로
    si, sj, sdi, sdj = app.plan["stair"]                   # 계단 칸 바깥 벽: 창문 <-> 벽
    for sgx, sgy in (mj_core.edge(si, sj, sdj, sdi), mj_core.edge(si, sj, -sdj, -sdi)):
        if sgx in (0, 2 * W) or sgy in (0, 2 * H):
            c0 = app.plan["grids"][0][sgy][sgx]
            app.edit_edge(sgx, sgy)
            assert app.plan["grids"][0][sgy][sgx] == ("X" if c0 == "N" else "N")
            app.edit_edge(sgx, sgy, ctrl=True)             # 정문은 거부, 창문이면 벽
            assert app.plan["grids"][0][sgy][sgx] in "XN"
            print("계단 바깥 벽 창문 토글 ok")
    ox, oy, cs, _, _ = app.geo
    free = next(c for c in app.result["room_maps"][0])             # 방 칸 하나
    pos = ((free[0] + 0.5) * mj_core.C - W * mj_core.C / 2, (free[1] + 0.5) * mj_core.C - H * mj_core.C / 2)
    n0 = len(app.markers("lights"))
    app.edit_marker("lights", "add", pos=pos)
    assert len(app.markers("lights")) == n0 + 1
    px, py = app.to_px(*pos)
    assert app.hit_marker(px, py) is not None
    app.edit_marker("lights", "move", idx=n0, pos=(pos[0] + 50, pos[1]))
    assert abs(app.markers("lights")[n0][0] - (pos[0] + 50)) < 1e-6
    app.edit_marker("lights", "del", idx=n0)
    assert len(app.markers("lights")) == n0
    i0 = len(app.markers("items"))
    app.edit_marker("items", "add", pos=pos)
    app.edit_marker("items", "add", pos=(99999.0, 0.0))            # 건물 밖 -> 거부
    assert len(app.markers("items")) == i0 + 1
    app.edit_edge(*inner)                                         # 벽 편집 후에도 수동 조명/아이템 유지
    assert len(app.markers("items")) == i0 + 1
    print("조명/아이템 편집 ok")
    app.popup("UE 적용 완료", "테스트 알림창")
    assert dpg.does_item_exist("popup")
    si, sj, _, _ = app.plan["stair"]
    ox, oy, cs, _, _ = app.geo
    assert app.hit(ox + (si + 0.5) * cs, oy + (sj + 0.5) * cs)[0] == "stair"
    d0 = app.plan["stair"][2:]
    app.edit_stair(si, sj)                                 # 제자리 클릭 = 회전
    assert app.plan["stair"][2:] != d0
    app.drag = {"kind": "stair", "from": (si, sj)}         # 드래그로 가운데 칸에 놓기
    app.drag = None
    app.edit_stair(*app.cell_at(ox + (W // 2 + 0.5) * cs, oy + (H // 2 + 0.5) * cs))
    assert app.plan["stair"][:2] == (W // 2, H // 2)
    app.undo()
    app.drag = {"kind": "xy", "W": W, "H": H}              # 드래그로 크기 변경 흉내
    ox, oy, cs, _, _ = app.geo
    nw, nh = app.drag_size(ox + (W + 2) * cs, oy + (H + 2) * cs)
    app.drag = None
    dpg.set_value("rx", str(nw))
    dpg.set_value("ry", str(nh))
    app.generate(False)
    print("selftest ok:", app.plan["size"], "stair", app.plan["stair"], "rooms", len(app.result["room_floor"]))
    app.apply()                                                   # 대상 없음 -> 실패 알림창 (스레드 -> 메인)


if __name__ == "__main__":
    main()
