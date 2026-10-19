"""BP_MJBuilding 의 에디터 버튼이 실행하는 스크립트.

  py mj_ue.py <액터 이름> <gen|last|preview|clear>

  gen      새로 생성 (Seed = 0 이면 매번 랜덤, 아니면 그 Seed)
  last     LastSeed 로 똑같이 다시 생성
  preview  배치하지 않고 평면도(방 번호 포함)만 출력 로그에 표시
  clear    건물/방/조명/아이템 데이터 비우기
  gui      Dear PyGui 편집 창(mj_gui.py) 열기

버튼(체크박스)이 Construction Script 안에서 호출되므로, 실제 작업은 다음 에디터 틱에 한다.
"""
import importlib
import os
import random
import sys

import unreal

import sys as _sys
_sys.dont_write_bytecode = True   # 도구 폴더에 __pycache__ 를 남기지 않음

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import mj_core  # noqa: E402

importlib.reload(mj_core)   # 스크립트 수정이 에디터 재시작 없이 반영되도록

BUTTONS = ("BtnGenerate", "BtnRebuildLastSeed", "BtnPreviewPlan", "BtnClear", "BtnOpenGUI")
LOG = "[MJBuilding] "


def find_actor(name):
    for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors():
        if a.get_name() == name:
            return a
    raise RuntimeError("액터를 찾을 수 없습니다: " + name)


def to_transforms(items):
    return [unreal.Transform(location=unreal.Vector(*loc),
                             rotation=unreal.Rotator(roll=rot[2], pitch=rot[0], yaw=rot[1]),
                             scale=unreal.Vector(*scl)) for loc, rot, scl in items]


def setp(actor, name, value, notify=True):
    mode = unreal.PropertyAccessChangeNotifyMode.ALWAYS if notify else unreal.PropertyAccessChangeNotifyMode.NEVER
    actor.set_editor_property(name, value, mode)


def run(name, mode):
    actor = find_actor(name)
    gp = actor.get_editor_property
    label = actor.get_actor_label()

    if mode == "clear":
        r = {"walls": [], "floors": [], "stairs": [], "lamps": [], "lights": [],
             "items": [], "item_rooms": [], "room_floor": []}
    else:
        if mode == "last":
            seed = gp("LastSeed")
            if not seed:
                raise RuntimeError("LastSeed 가 없습니다. 먼저 Generate 하세요.")
        else:
            seed = gp("Seed") or random.randrange(1, 2**31 - 1)      # 0 = 매번 랜덤
        r = mj_core.build(gp("RoomsX") or 5, gp("RoomsY") or 5, max(1, gp("NumFloors")), seed,
                          gp("RoomCount") or None, gp("ItemDensity"))
        for f, s, plan in r["plans"]:
            unreal.log(LOG + "%s %d층 (floor seed %d)\n%s" % (label, f + 1, s, plan))
        unreal.log(LOG + "%s seed=%d  벽 %d / 바닥 %d / 경사로 %d / 방 %d / 조명 %d / 아이템 지점 %d"
                   % (label, seed, len(r["walls"]), len(r["floors"]), len(r["stairs"]),
                      len(r["room_floor"]), len(r["lights"]), len(r["items"])))
        if mode == "preview":
            return

    with unreal.ScopedEditorTransaction("MJ 건물 " + {"clear": "지우기"}.get(mode, "생성")):
        actor.modify()
        setp(actor, "ItemSpawnPoints", to_transforms(r["items"]), False)
        setp(actor, "ItemSpawnRoomIds", r["item_rooms"], False)
        setp(actor, "RoomFloors", r["room_floor"], False)
        if mode != "clear":
            setp(actor, "LastSeed", r["seed"], False)
        setp(actor, "WallTransforms", to_transforms(r["walls"]), False)
        setp(actor, "FloorTransforms", to_transforms(r["floors"]), False)
        setp(actor, "LampTransforms", to_transforms(r["lamps"]), False)
        setp(actor, "LightTransforms", to_transforms(r["lights"]), False)
        setp(actor, "StairTransforms", to_transforms(r["stairs"]))      # 마지막 한 번만 알려 Construction Script 재실행
    unreal.log(LOG + "%s %s 완료" % (label, "비움" if mode == "clear" else "생성"))


def reset_buttons(name):
    try:
        actor = find_actor(name)
        for b in BUTTONS:
            if actor.get_editor_property(b):
                setp(actor, b, False)
    except Exception:
        pass


def open_gui(name):
    import shutil
    import subprocess
    exe = shutil.which("pythonw") or shutil.which("python")
    if not exe:
        raise RuntimeError("시스템 Python(pythonw)을 PATH 에서 찾지 못했습니다.")
    subprocess.Popen([exe, os.path.join(HERE, "mj_gui.py"), "--actor", name], cwd=HERE,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    unreal.log(LOG + "GUI 실행: " + name)


def deferred(name, mode):
    handle = []

    def tick(_dt):
        unreal.unregister_slate_post_tick_callback(handle[0])
        reset_buttons(name)
        try:
            open_gui(name) if mode == "gui" else run(name, mode)
        except Exception as e:
            unreal.log_error(LOG + "실패: %s" % e)

    handle.append(unreal.register_slate_post_tick_callback(tick))


if len(sys.argv) >= 3:
    deferred(sys.argv[1], sys.argv[2])
else:
    unreal.log_error(LOG + "사용법: py mj_ue.py <액터 이름> <gen|last|preview|clear>")
