"""OMORI 自动练级脚本（远程调试注入版）。

工作原理：
1. 用 `--remote-debugging-port` 启动 OMORI，通过 Chromium DevTools Protocol
   读取游戏运行时数据（当前场景、战斗阶段、队伍血量、敌人血量、地图事件位置）。
2. 战斗中直接调用游戏自身的 Scene_Battle / BattleManager 方法提交指令：
   血量低于阈值就吃回复道具，否则普通攻击。
3. 地图上继续用键盘输入移动来触发遭遇。

用法：
    python omori_auto_level.py --launch   # 由脚本启动带调试端口的 OMORI
    python omori_auto_level.py            # 连接已在调试端口上运行的 OMORI

热键：F8 暂停/恢复，F9 急停，F10 退出。
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.request
from ctypes import wintypes

# ---------------------------------------------------------------- 游戏路径配置

GAME_DIR = r"E:\BaiduNetdiskDownload\OMORI(1)"
GAME_EXE = os.path.join(GAME_DIR, "OMORI.exe")
GAME_TOKEN_ARG = "--6bdb2e585882fbd48826ef9cffd4c511"

# ---------------------------------------------------------------- Win32 输入

USER32 = ctypes.WinDLL("user32", use_last_error=True)

VK_F8 = 0x77
VK_F9 = 0x78
VK_F10 = 0x79
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_Z = 0x5A
KEYEVENTF_KEYUP = 0x0002

USER32.GetForegroundWindow.restype = wintypes.HWND
USER32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
USER32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
USER32.GetWindowTextW.restype = ctypes.c_int
USER32.GetAsyncKeyState.argtypes = [wintypes.INT]
USER32.GetAsyncKeyState.restype = wintypes.SHORT
USER32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]


def omori_window_focused() -> bool:
    """当前前台窗口是否是 OMORI 主窗口。"""
    hwnd = USER32.GetForegroundWindow()
    if not hwnd:
        return False
    title = ctypes.create_unicode_buffer(256)
    USER32.GetWindowTextW(hwnd, title, len(title))
    return title.value.strip().upper() == "OMORI"


def held(vk: int, duration: float, should_stop) -> None:
    USER32.keybd_event(vk, 0, 0, 0)
    end = time.monotonic() + duration
    try:
        while time.monotonic() < end:
            if should_stop():
                break
            time.sleep(0.02)
    finally:
        USER32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def edge_pressed(vk: int, previous: dict[int, bool]) -> bool:
    down = bool(USER32.GetAsyncKeyState(vk) & 0x8000)
    result = down and not previous.get(vk, False)
    previous[vk] = down
    return result


# ---------------------------------------------------------------- CDP 客户端


class WebSocket:
    """仅实现 CDP 需要的最小子集：握手、文本帧、ping/pong、关闭。"""

    def __init__(self, url: str, timeout: float = 10.0) -> None:
        if not url.startswith("ws://"):
            raise ValueError(f"只支持 ws:// 地址：{url}")
        hostport, _, path = url[5:].partition("/")
        host, _, port = hostport.partition(":")
        self.host = host
        self.port = int(port or 80)
        self.path = "/" + path
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self._buffer = b""

    def connect(self) -> None:
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("WebSocket 握手时连接被关闭")
            raw += chunk
        head, _, self._buffer = raw.partition(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0].decode("latin-1")
        if "101" not in status:
            raise ConnectionError(f"WebSocket 握手失败：{status}")

    def _read_exact(self, size: int) -> bytes:
        data = b""
        if self._buffer:
            take = min(size, len(self._buffer))
            data, self._buffer = self._buffer[:take], self._buffer[take:]
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise ConnectionError("连接已关闭")
            data += chunk
        return data

    def _read_frame(self) -> tuple[bool, int, bytes]:
        first, second = self._read_exact(2)
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if masked else None
        payload = self._read_exact(length)
        if mask:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return fin, opcode, payload

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = os.urandom(4)
        header += mask
        body = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + body)

    def send_text(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def recv_text(self) -> str:
        parts: list[bytes] = []
        while True:
            fin, opcode, payload = self._read_frame()
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x8:
                raise ConnectionError("服务端要求关闭连接")
            parts.append(payload)
            if fin:
                break
        return b"".join(parts).decode("utf-8", errors="replace")

    def close(self) -> None:
        if self.sock is None:
            return
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        finally:
            self.sock = None


class CDP:
    """对单个调试目标的最小 DevTools Protocol 封装。"""

    def __init__(self, ws_url: str) -> None:
        self._ws = WebSocket(ws_url)
        self._ws.connect()
        self._next_id = 0

    def evaluate(self, expression: str):
        self._next_id += 1
        message_id = self._next_id
        self._ws.send_text(json.dumps({
            "id": message_id,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True},
        }))
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            message = json.loads(self._ws.recv_text())
            if message.get("id") != message_id:
                continue
            if "error" in message:
                raise RuntimeError(f"CDP 调用失败：{message['error']}")
            result = message.get("result", {})
            if result.get("exceptionDetails"):
                detail = json.dumps(result["exceptionDetails"], ensure_ascii=False)
                raise RuntimeError(f"JS 执行异常：{detail[:300]}")
            return result.get("result", {}).get("value")
        raise TimeoutError("CDP 调用超时")

    def close(self) -> None:
        self._ws.close()


def http_json(url: str, timeout: float = 2.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def find_page_target(port: int) -> str | None:
    """返回调试端口中第一个页面目标的 websocket 地址。"""
    try:
        targets = http_json(f"http://127.0.0.1:{port}/json/list")
    except (urllib.error.URLError, OSError, ValueError):
        return None
    for target in targets:
        if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
            return target["webSocketDebuggerUrl"]
    return None


def launch_game(port: int) -> None:
    if not os.path.isfile(GAME_EXE):
        raise SystemExit(f"找不到游戏可执行文件：{GAME_EXE}")
    subprocess.Popen(
        [GAME_EXE, GAME_TOKEN_ARG, f"--remote-debugging-port={port}"],
        cwd=GAME_DIR,
    )


# ---------------------------------------------------------------- 注入的 JS

SNAPSHOT_JS = r"""
(function () {
    var out = { ok: false, scene: null, phase: null, canInput: false,
                actors: [], enemies: [], map: null, healItems: [], error: null };
    try {
        out.scene = SceneManager._scene ? SceneManager._scene.constructor.name : null;
        out.phase = BattleManager._phase;
        out.canInput = BattleManager.isInputting();

        var party = $gameParty ? $gameParty.members() : [];
        for (var i = 0; i < party.length; i++) {
            var a = party[i];
            out.actors.push({ index: a.index(), name: a.name(), hp: a.hp,
                              mhp: a.mhp, alive: a.isAlive() });
        }
        var troop = $gameTroop ? $gameTroop.members() : [];
        for (var j = 0; j < troop.length; j++) {
            var e = troop[j];
            out.enemies.push({ index: e.index(), name: e.name(), hp: e.hp,
                               mhp: e.mhp, alive: e.isAlive() });
        }
        for (var k = 1; k < $dataItems.length; k++) {
            var item = $dataItems[k];
            if (!item || !item.name || !item.effects || !item.consumable) continue;
            var hasHeal = false;
            var flat = 0;
            for (var m = 0; m < item.effects.length; m++) {
                if (item.effects[m].code === 11) {
                    hasHeal = true;
                    flat += item.effects[m].value2;
                }
            }
            if (!hasHeal) continue;
            var count = $gameParty.numItems(item);
            if (count > 0) {
                out.healItems.push({ id: item.id, name: item.name, count: count, heal: flat });
            }
        }
        if ($gamePlayer && $gameMap) {
            var events = [];
            var list = $gameMap.events();
            for (var n = 0; n < list.length; n++) {
                var ev = list[n];
                if (!ev) continue;
                var page = null;
                try {
                    var data = ev.event();
                    page = data ? data.pages[ev._pageIndex] : null;
                } catch (err) { page = null; }
                events.push({ id: ev.eventId(),
                              name: ev.event() ? String(ev.event().name) : "",
                              x: ev.x, y: ev.y,
                              dx: ev.x - $gamePlayer.x,
                              dy: ev.y - $gamePlayer.y,
                              trigger: page ? page.trigger : -1 });
            }
            out.map = { x: $gamePlayer.x, y: $gamePlayer.y, events: events };
        }
        out.ok = true;
    } catch (err) {
        out.error = String(err);
    }
    return out;
})()
"""

# 战斗中提交一次指令：血量低于阈值吃回复道具，否则普通攻击。
# 走的是游戏自身的方法（与按下确认键等价的调用路径）。
ACT_JS = r"""
(function () {
    var result = { acted: false, reason: "" };
    try {
        var scene = SceneManager._scene;
        if (!scene || scene.constructor.name !== "Scene_Battle") {
            result.reason = "not-battle";
            return result;
        }
        if (!BattleManager.isInputting()) {
            result.reason = "phase=" + BattleManager._phase;
            return result;
        }
        if ($gameMessage.isBusy()) {
            result.reason = "message-busy";
            return result;
        }
        var actor = BattleManager.actor();
        if (!actor) {
            scene.selectNextCommand();          // 等同在“战斗”指令上按确认
            result.acted = true;
            result.reason = "party-fight";
            return result;
        }
        if (!scene._actorCommandWindow || !scene._actorCommandWindow.active) {
            result.reason = "window-not-ready";
            return result;
        }
        var action = BattleManager.inputtingAction();
        if (!action) {
            result.reason = "no-action";
            return result;
        }

        var heal = null;
        if (actor.hp < __HP_THRESHOLD__) {
            var keyword = __ITEM_KEYWORD__;
            for (var i = 1; i < $dataItems.length; i++) {
                var item = $dataItems[i];
                if (!item || !item.name || !item.effects || !item.consumable) continue;
                if (!$gameParty.numItems(item)) continue;
                if (keyword && item.name.indexOf(keyword) < 0) continue;
                var heals = false;
                for (var j = 0; j < item.effects.length; j++) {
                    if (item.effects[j].code === 11) heals = true;
                }
                if (!heals) continue;
                if (!actor.canUse(item)) continue;
                heal = item;
                break;
            }
        }

        if (heal) {
            action.setItem(heal.id);
            action.setTarget(actor.index());
            scene.selectNextCommand();
            result.acted = true;
            result.reason = "heal";
            result.actor = actor.name();
            result.hp = actor.hp;
            result.item = heal.name;
            return result;
        }

        var target = -1;
        var members = $gameTroop.members();
        for (var k = 0; k < members.length; k++) {
            if (members[k].isAlive()) { target = members[k].index(); break; }
        }
        if (target < 0) {
            result.reason = "no-living-enemy";
            return result;
        }
        action.setAttack();
        action.setTarget(target);
        scene.selectNextCommand();
        result.acted = true;
        result.reason = "attack";
        result.actor = actor.name();
        result.hp = actor.hp;
        result.target = target;
        return result;
    } catch (err) {
        result.reason = "error: " + String(err);
        return result;
    }
})()
"""


def build_act_js(hp_threshold: int, item_keyword: str) -> str:
    return ACT_JS.replace("__HP_THRESHOLD__", str(hp_threshold)).replace(
        "__ITEM_KEYWORD__", json.dumps(item_keyword, ensure_ascii=False)
    )


# ---------------------------------------------------------------- 主流程


def nearest_threat(snapshot: dict, keyword: str) -> dict | None:
    """在地图事件里找最近的目标事件；keyword 为空时只看接触触发的事件。"""
    game_map = snapshot.get("map")
    if not game_map:
        return None
    best = None
    best_distance = None
    for event in game_map["events"]:
        if keyword:
            if keyword.lower() not in str(event.get("name", "")).lower():
                continue
        elif event.get("trigger") != 1:      # 1 = 事件接触触发
            continue
        distance = abs(event["dx"]) + abs(event["dy"])
        if distance == 0:
            continue
        if best_distance is None or distance < best_distance:
            best, best_distance = event, distance
    return best


def describe(snapshot: dict) -> str:
    scene = snapshot.get("scene")
    if scene == "Scene_Battle":
        enemies = ", ".join(
            f"{e['name']}({e['hp']}/{e['mhp']})" for e in snapshot.get("enemies", [])
        )
        actors = ", ".join(
            f"{a['name']}:{a['hp']}/{a['mhp']}" for a in snapshot.get("actors", [])
        )
        return f"战斗 phase={snapshot.get('phase')} | 我方 {actors} | 敌方 {enemies}"
    game_map = snapshot.get("map")
    if not game_map:
        return f"场景={scene}"
    events = [e for e in game_map["events"] if e.get("name")]
    events.sort(key=lambda e: abs(e["dx"]) + abs(e["dy"]))
    nearby = ", ".join(f"{e['name']}({e['dx']:+d},{e['dy']:+d})" for e in events[:3])
    return (f"场景={scene} 坐标({game_map['x']},{game_map['y']}) "
            f"附近事件: {nearby or '无'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="OMORI 自动练级脚本")
    parser.add_argument("--launch", action="store_true", help="由脚本带调试端口启动游戏")
    parser.add_argument("--port", type=int, default=9222, help="远程调试端口")
    parser.add_argument("--hp", type=int, default=10, help="低于该血量吃回复道具")
    parser.add_argument("--item", default="", help="回复道具名称关键字，留空自动挑选")
    parser.add_argument("--enemy", default="", help="地图敌人事件名称关键字，留空只监控不追踪")
    parser.add_argument("--walk", type=float, default=2.4, help="单方向持续移动秒数")
    args = parser.parse_args()

    if os.name != "nt":
        raise SystemExit("该脚本只能在 Windows 上运行。")

    act_js = build_act_js(args.hp, args.item)

    ws_url = find_page_target(args.port)
    if ws_url is None and args.launch:
        print("未发现调试端口，正在启动带调试参数的 OMORI……")
        launch_game(args.port)
        for _ in range(60):
            time.sleep(0.5)
            ws_url = find_page_target(args.port)
            if ws_url:
                break
    if ws_url is None:
        raise SystemExit(
            "没有可用的调试端口。当前运行中的 OMORI 未开启远程调试，无法读取运行时数据。\n"
            "请先存档并退出游戏，然后运行：python omori_auto_level.py --launch"
        )

    cdp = CDP(ws_url)
    print(f"已连接调试目标：{ws_url}")

    try:
        first = cdp.evaluate(SNAPSHOT_JS)
    except (ConnectionError, OSError, TimeoutError, RuntimeError, ValueError) as error:
        raise SystemExit(f"连接成功但无法读取游戏状态：{error}") from error
    if not first or not first.get("ok"):
        raise SystemExit(f"读取游戏状态失败：{first.get('error') if first else '空结果'}")

    items = first.get("healItems", [])
    if items:
        summary = "，".join(f"{i['name']}(x{i['count']}, 回复{i['heal']})" for i in items)
        print(f"当前可用回复道具：{summary}")
        print("如需固定使用某一种，用 --item 名称关键字 指定。")
    else:
        print("没有检测到可用的回复道具，血量过低时只会普通攻击。")

    paused = True
    running = True
    previous: dict[int, bool] = {}
    direction = VK_LEFT
    direction_deadline = 0.0
    last_act = 0.0
    last_report = 0.0
    walk_seconds = args.walk

    def process_hotkeys() -> bool:
        nonlocal paused, running
        if edge_pressed(VK_F10, previous):
            running = False
        elif edge_pressed(VK_F9, previous):
            paused = True
            print("已按 F9 暂停。")
        elif edge_pressed(VK_F8, previous):
            paused = not paused
            print("继续运行。" if not paused else "已暂停。")
        return not running or paused

    print("准备就绪。F8 暂停/恢复，F9 急停，F10 退出。切到 OMORI 窗口后按 F8 开始。")

    try:
        while running:
            process_hotkeys()
            if not running:
                break
            if paused:
                time.sleep(0.05)
                continue

            try:
                snapshot = cdp.evaluate(SNAPSHOT_JS)
            except (ConnectionError, OSError, TimeoutError, RuntimeError, ValueError) as error:
                print(f"调试连接中断：{error}")
                break

            if not snapshot or not snapshot.get("ok"):
                print(f"读取运行时状态失败：{snapshot.get('error') if snapshot else '空结果'}")
                time.sleep(0.5)
                continue

            now = time.monotonic()
            if now - last_report >= 5.0:
                last_report = now
                print(describe(snapshot))

            if snapshot.get("scene") == "Scene_Battle":
                if snapshot.get("canInput") and now - last_act >= 0.25:
                    last_act = now
                    try:
                        action = cdp.evaluate(act_js)
                    except (ConnectionError, OSError, TimeoutError, RuntimeError, ValueError) as error:
                        print(f"提交战斗指令失败：{error}")
                        time.sleep(0.5)
                        continue
                    if action and action.get("acted"):
                        print(f"战斗指令：{action.get('reason')} "
                              f"{action.get('actor', '')} hp={action.get('hp', '')} "
                              f"{action.get('item', action.get('target', ''))}")
                time.sleep(0.2)
                continue

            # 地图阶段：按按键移动来刷怪；只有游戏窗口在前台才发按键。
            if not omori_window_focused():
                time.sleep(0.3)
                continue

            target = nearest_threat(snapshot, args.enemy)
            if target is not None:
                if abs(target["dx"]) >= abs(target["dy"]):
                    direction = VK_RIGHT if target["dx"] > 0 else VK_LEFT
                else:
                    direction = VK_DOWN if target["dy"] > 0 else VK_UP
                direction_deadline = 0.0
            elif now >= direction_deadline:
                direction = VK_RIGHT if direction == VK_LEFT else VK_LEFT
                direction_deadline = now + walk_seconds

            held(direction, 0.3, process_hotkeys)
    finally:
        for vk in (VK_LEFT, VK_RIGHT, VK_UP, VK_DOWN, VK_Z):
            USER32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        cdp.close()

    print("脚本已退出。")


if __name__ == "__main__":
    main()
