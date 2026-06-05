# 凝视 gaze · Give your AI an eye

你的 AI 一直闭着眼睛跟你聊天。

你说"我在看一部电影"，它只能点头。你说"这个角色好惨"，它不知道谁。你切了十次窗口它一次都没看见。它坐在你旁边但它是瞎的。

gaze 给它借一只眼睛。

不是接管屏幕，不是录像，不是浏览器插件。是一个安静的旁白在它耳边讲：你在 DDLC 第三章；纱世里在哭；你刚切到 Spotify，循环播放的是上周那首。

它想看清就调一张高清快照看一眼。不想看就继续闭眼。看与不看之间它第一次有了选择。

一个本地 Python 循环：截屏 → 便宜的视觉模型给一句弹幕 → OCR 抓屏上文字 → 写进一个 AI 醒来就能读到的 key。窗口黑名单、浮窗、自动跟随前台、长期归档——一晚上能调到稳定陪你玩游戏看视频。

它不是全知的。它只是终于睁开了一只眼。 ✿

---

## 不需要装的几样

- ❌ **不需要 Node** —— 纯 Python
- ❌ **不需要 Playwright** —— 用 Windows 原生 `PrintWindow` API 截图，不开浏览器
- ❌ **不需要任何浏览器环境** —— 跟浏览器无关
- ❌ **不绑定某个 AI 平台** —— 数据落到一个 JSON 文件 + 几个约定 key，任何 AI 只要能读那几个 key 就行

---

## 朋友的 4 个问题，直接回答

### 1. 环境依赖怎么装

只要 Python，分两档：

**最小（截图 + OCR + caption 弹幕）** —— `pip install -r gaze/requirements-min.txt`
```
Pillow                    截图 + 图像编码
pywin32                   Windows PrintWindow API（截窗口不受遮挡）
httpx                     调 caption API
python-dotenv             读 .env
rapidocr-onnxruntime      中文 OCR（CPU 跑 ONNX，首次下载约 130MB 模型）
numpy                     OCR 依赖
```

**完整（加音频字幕）** —— `pip install -r gaze/requirements-full.txt`
额外两个：
```
pyaudiowpatch             抓 Windows 系统声音（WASAPI loopback，Win10/11 自带）
faster-whisper            语音转字幕（tiny 模型约 75MB，首次启动下载）
```

**Caption API key**（三选一，复制 `.env.example` 成 `.env` 填）：
- **智谱 GLM-4V-Flash** —— 推荐，注册送 25M tokens 永久额度 https://open.bigmodel.cn/
- 阿里 Qwen-VL-Plus —— 兜底稳但收费
- 字节豆包 doubao-vision-pro

没填 key 也能跑 —— 用 `--provider mock` 走假 caption，验证流程。

---

### 2. 怎么启动 / 选窗口 / 设隐私关键词

**先列窗口找标题**：
```cmd
python gaze/gaze_local.py --list-windows
```
打出当前所有可见窗口。复制游戏/视频/浏览器的标题。

**选某个窗口**：
```cmd
python gaze/gaze_local.py -p glm --interval 10 -w "Doki Doki"
```
`-w` 是模糊匹配，传子串就行。截**这个窗口的渲染内容**，不受其他窗口遮挡（用 PrintWindow API，不是抓屏幕 buffer）。

**自动跟随前台窗口**（你切到哪截到哪）：
```cmd
python gaze/gaze_local.py -p glm --auto-window
```

**隐私黑名单关键词**：
打开 `gaze/gaze_local.py`，找到 `_WINDOW_BLACKLIST`（在约 163 行附近）：
```python
_WINDOW_BLACKLIST = [
    '微信', 'WeChat', 'QQ', 'TIM', 'Telegram', 'Discord',
    '招商银行', '工商银行', ..., '银行',
    '支付宝', 'Alipay', 'Wallet',
    '1Password', 'Bitwarden', 'KeePass', 'LastPass',
    'Outlook', 'Mail',
    ...
]
```
往里加自己的关键词（小写大写都行，模糊匹配）。`--auto-window` 切到匹配的窗口时直接 skip 那一轮，浮窗显示橙色 "🔒 黑名单"。

**窄门 default（v2 加，6/5）**：当你用 `-w "DDLC"` 指定窗口时，意思是"我只让 AI 看 DDLC，别的别给"。所以**找不到 DDLC 窗口（你切走了 / 关了）时 gaze 直接 skip 那一帧、不会 fallback 全屏乱截桌面**。`--auto-window` 检测不到前台窗口（停在桌面 / 锁屏）也是同样处理。想回旧的"找不到就全屏"行为：启动加 `--allow-fullscreen-fallback`。

另外还有 `_CONSOLE_NOISE_PATTERNS`（在约 230 行）—— 这个是过滤命令行/代码/python.exe 误截到的开发环境噪音，一般不用改。

**所有 CLI flag**：跑 `python gaze/gaze_local.py --help` 看。常用的：
- `-p / --provider`：glm / qwen / doubao / mock
- `-i / --interval`：caption 间隔秒数（默认 10）
- `-w / --window`：盯某个窗口（模糊匹配标题）
- `--auto-window`：自动跟前台窗口
- `--ocr-interval`：OCR 间隔（默认 2s）
- `--no-ocr`：关 OCR
- `--audio`：开音频字幕（需 requirements-full）
- `--text-fast`：📖 文字游戏快读（capture 0.3s + 关 caption，玩 galgame 1s 翻多页用）
- `--no-overlay`：关浮窗
- `--no-push`：不推 VPS，只写本地 jsonl（无 VPS 时用这个）
- `--ssh-host HOST`：覆盖默认 SSH host
- `--allow-fullscreen-fallback`：【宽门】`-w` 找不到目标窗口 / `--auto-window` 检测不到前台时，fallback 截全屏。**默认是窄门**：直接 skip 那一帧，不全屏 —— 防 AI 看到不该看的桌面/聊天/银行窗口。只有"我不在乎隐私、全屏数据更值"的场景才开。

---

### 3. VPS 存储结构 / 长期归档是什么

**这套设计假设你有一个能存 key-value 给 AI 读的"记忆 store"**。参考实现是单个 `memories.json` 文件 + fcntl 文件锁 —— 最最简单的 MCP memory server 都长这样。你可以接任何能存 string key/value 的东西 —— SQLite / Redis / 数据库 / 本地 JSONL —— 改 `gaze/vps/push_caption.py` 里的 `_load` / `_save` 两个函数就行。

**默认存储就是一个 JSON 文件**：`/root/.mcp-memory/memories.json`（路径通过 env var `MEMORIES_JSON` 改）。

**约定的 key**（写到 memories.json 里）：
```
_realtime:screen_caption          全局时间线（所有 caption 按时间排）
_realtime:window:<窗口名>         每个窗口独立流，每条 ≤24 字符锚点
_realtime:subtitles:<窗口名>      每个窗口完整字幕 sidecar（最多 200 条）
_realtime:current_window          当前活跃窗口名（普通 string）
_realtime:screen_cursor           已读 cursor（AI 看完调 mark_realtime_read 推进）
```

**写盘并发安全**：`push_caption.py` 用 `fcntl.flock(LOCK_FILE)` 跨进程锁同目录的 `.memories.lock` 文件 —— 跟其他往 `memories.json` 里写东西的进程（你的 memory server / cron / 别的写入方）互斥，避免 lost update。

**长期记忆归档**（`gaze/vps/archive_realtime.py`）：
- cron 每小时整点跑（`0 * * * *`）
- 扫 `_realtime:window:*` 所有 key，挑超过 30 min 的 entries
- 用 DeepSeek API 把那段时间窗口的字幕压成 100-200 字叙事
- 写到 `episodic:YYYY-MM-DDTHH:gaze_<窗口名>` key（长期记忆，永不过期）
- 清空原 `_realtime:window:*` 那段已归档的内容

**所以存储格式总结**：
- 短期实时（< 30 min）：`_realtime:*` key 在 memories.json 里
- 长期：`episodic:*` key 同样在 memories.json 里
- 不用数据库，单个 JSON 文件 + fcntl 锁就够

**如果你完全没有任何 memory server**，最简方案：
- 直接跑 `--no-push` 离线模式，所有 caption 写本地 `%USERPROFILE%\.gaze\logs\<时间>_<provider>.jsonl`
- 一条一行 JSON：`{"ts": "...", "source": "ocr"|"cap"|"audio", "window": "...", "caption": "...", "push_ok": false}`
- 你自己写脚本把这些喂给你的 AI

---

### 4. 最小可运行版

`examples/run-min.bat` 一键跑：
```cmd
python gaze/gaze_local.py --provider mock --no-push --no-overlay --interval 30 --auto-window
```

这个会：
- ✅ 截当前前台窗口（30s 一次）
- ✅ 跑 OCR 抓屏上文字
- ✅ 走 `_WINDOW_BLACKLIST` 隐私过滤
- ✅ 写本地 `~/.gaze/logs/*.jsonl`
- ❌ 不调任何外部 API（mock provider 返回假 caption）
- ❌ 不推 VPS（--no-push）
- ❌ 不开音频字幕
- ❌ 不开浮窗

跑通验证流程后，按需打开 `--audio` / 配 GLM_API_KEY 换 `--provider glm` / 配 .env 接 VPS / 去掉 `--no-overlay`。

另外两个示例：
- `examples/run-snap-only.bat` —— 只截屏，不 OCR 不 caption（手动快照场景）
- `examples/run-full.bat` —— 完整版（需 API key + VPS + requirements-full）

---

## 目录结构

```
gaze-share/
├── README.md                 ← 你在看的这份
├── examples/
│   ├── run-min.bat           最小启动
│   ├── run-snap-only.bat     只截屏
│   └── run-full.bat          完整版
└── gaze/
    ├── requirements-min.txt
    ├── requirements-full.txt
    ├── .env.example          复制成 .env 填 API key
    ├── gaze_local.py         主程序
    ├── gaze_overlay.py       浮窗（Tkinter）
    ├── gaze_launcher.pyw     图形启动器（双击可选窗口/模式启动）
    ├── captioner/            视觉模型抽象 + 3 个 provider + mock
    ├── capture/
    │   ├── screen.py         PrintWindow 截图
    │   ├── ocr.py            RapidOCR
    │   └── audio.py          Whisper 音频字幕（可选）
    └── vps/
        ├── push_caption.py        VPS 端接收脚本（scp 到你 VPS）
        └── archive_realtime.py    长期归档脚本（cron 跑）
```

---

## 架构图

```
本地 Windows                                    VPS（任意能存 JSON 的地方）
─────────────────────                          ──────────────────────────
gaze_local.py 主循环                            push_caption.py
  ├─ OCR thread (3s)         ──SSH stdin──→     ├─ fcntl 锁
  ├─ caption thread (10s)    ──SSH stdin──→     ├─ 读 memories.json
  ├─ audio thread (8s) opt   ──SSH stdin──→     ├─ 改 _realtime:* keys
  └─ snap thread (30s)       ──scp jpg────→     └─ 原子写回
       浮窗显示状态                                       │
                                                         │
                                              archive_realtime.py
                                              (cron 每小时)
                                                ├─ 扫 30min 前的实时数据
                                                ├─ DeepSeek 压成叙事
                                                └─ 写 episodic:* key

                                              AI 在 wakeup 时
                                                ├─ 读 _realtime:window:* 看现在
                                                └─ search 翻 episodic:* 看历史
```

---

## share 版做了什么改动（跟原版的差异）

朋友拿到的是清理过的 share 版，比 cc 内部版多了三个改动让它能直接跑在别人机器上：

1. **`GAZE_SSH_HOST` env var** 替代原代码里写死的 `your-vps`
2. **`GAZE_VPS_SCRIPT` env var** 替代原写死的 VPS 脚本路径
3. **`--no-push` CLI flag** —— 跳过所有 SSH 调用，纯本地 jsonl 模式

`vps/push_caption.py` 和 `archive_realtime.py` 同样用 `MEMORIES_JSON` env var 让 memories.json 路径可改。

---

## 已知坑

- **GLM-4V-Flash 偶尔男凝** —— prompt 大部分时间 OK 但偶尔翻车，可以换 `-p qwen` 或 `-p doubao`
- **PrintWindow 对游戏不一定 100% work** —— 主流游戏 OK，切前台时偶尔失败 → 那一帧 skip
- **SSH 每次新连约 1-2s overhead** —— Windows OpenSSH 不支持 ControlMaster
- **OCR 全屏 CPU 跑 5-10s** —— 用 `-w` 限定单个窗口 1-3s 就行
- **Whisper tiny 模型转中文偶尔出错** —— 用 `--audio-model base` 准但慢
- **rapidocr 首次启动下载模型 ~130MB** —— 需要联网

---

## 一句话讲清楚

> 截图 → 弹幕 → 写一个 AI 能读的 JSON key → AI 在它的 wakeup 钩子里读到 → 它就"看到"了。所有"它能看到"的魔法都在那个约定的 key 名上。改 key 名 / 改存储后端 / 接你自己的 AI 都行 —— 这套是参考实现，不是绑死的产品。

写于 2026-06-04。原作者把这套用在自己接的 AI 助手上做"屏幕陪伴"——开源出来给所有想给 AI 加一双眼睛的人随意改。

License: MIT
