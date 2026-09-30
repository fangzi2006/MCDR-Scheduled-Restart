# scheduled_restart · MCDReforged 定时重启插件

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![MCDReforged](https://img.shields.io/badge/MCDReforged-%3E%3D2.12.0-green.svg)](https://github.com/MCDReforged/MCDReforged)

按 **Linux cron 表达式** 定时重启 Minecraft 服务器，并在重启前按你设定的多个时间点，
向全服玩家发送 **聊天框消息** 或 **大标题（title/subtitle）** 提醒。

* 主需求：定时自动重启（默认调用 MCDR 的 `restart`，也可以只停服 / 停服并退出 / 执行自定义指令）
* 按月 / 周 / 日 / 时 / 分任意组合：`0 4 * * *`、`30 3 * * 1`、`0 5 1 * *`、`@daily`……
* 提醒条数不限，**用「数组里放对象」的写法**，每条提醒自由设置提前时间与通知方式
* 通知方式：`chat` 聊天框（tellraw）、`title` 大标题、`actionbar` 物品栏上方、`command` 执行任意指令
* 支持 `§` 旧版颜色代码与 `color` 字段，可选音效，支持占位符
* 玩家进服时私聊告知倒计时；重启历史落盘；配置热重载；单条坏配置只跳过它自己
* 纯 Python 实现，**不依赖任何第三方库**，自带 cron 解析器（不装 `croniter`）
* 计划可直接用指令管理：`list` / `add` / `remove` / `enable` / `disable` / `test`

> English summary is at the [bottom of this README](#english-summary).

---

## 下载与安装

**方式一：下载插件包（推荐）**

到 [Releases](../../releases/latest) 页面下载 `scheduled_restart-v<版本>.mcdr`，放进 MCDR 的
`plugins/` 目录，重启 MCDR 或执行 `!!MCDR plugin load <文件名>`。

**方式二：用源码自己打包**

```bash
git clone https://github.com/fangzi2006/mcdr-scheduled-restart.git
cd mcdr-scheduled-restart
python tools/build_plugin.py          # 生成 dist/scheduled_restart-v1.1.0.mcdr
```

**方式三：目录插件**

把 `src/` 里的内容整体放到 `plugins/scheduled_restart/` 下，最终形如：

```
plugins/scheduled_restart/mcdreforged.plugin.json
plugins/scheduled_restart/scheduled_restart/__init__.py
```

首次加载后会自动生成配置：`config/scheduled_restart/config.json`（内容见 [examples/config.json](examples/config.json)）。

> 默认配置里 3 个示例计划都是 `"enabled": false`，**开箱不会自己重启**，改完再用 `!!srestart reload` 生效，
> 或者直接用 `!!srestart add <名字> <cron>` 新建一个计划。

---

## 快速开始

1. 编辑 `config/scheduled_restart/config.json`，把示例计划的 `enabled` 改成 `true`，或照抄一份改 `cron`：

   ```json
   {
     "name": "每天凌晨4点重启",
     "enabled": true,
     "cron": "0 4 * * *",
     "restart_method": "mcdr_restart",
     "use_default_notifications": true
   }
   ```

2. 在游戏里或控制台执行 `!!srestart reload`（需要 admin 权限）。

3. 执行 `!!srestart list` 确认「下次重启时间」，执行 `!!srestart test <序号>`
   可以立刻预览这套提醒在游戏里长什么样（不会真的重启）。

---

## 目录结构

```
mcdr-scheduled-restart/
├── src/                            # 插件本体（打包成 .mcdr 或直接放进 plugins/）
│   ├── mcdreforged.plugin.json     # 插件元数据
│   └── scheduled_restart/
│       ├── __init__.py             # MCDR 入口（on_load / on_unload / on_player_joined）
│       ├── cron.py                 # cron 解析、下次触发时间、中文描述
│       ├── config.py               # 配置默认值、校验、容错、读取前处理（去 BOM / 坏文件备份）
│       ├── config_store.py         # 配置文件读改写（enable / disable / add / remove 指令用）
│       ├── notify.py               # 提醒渲染与发送（聊天框 / 大标题 / 音效）
│       ├── scheduler.py            # 调度线程、倒计时状态机、重启执行、历史记录
│       └── command.py              # !!srestart 指令树
├── examples/config.json            # 生成出来的默认配置（与实际运行完全一致）
├── tests/                          # 216 个单元测试
├── e2e/                            # 真实 MCDR 端到端测试（模拟服务端、测试驱动插件、实测证据）
├── tools/build_plugin.py           # 打包 .mcdr
├── tools/generate_example_config.py
├── CHANGELOG.md                    # 更新日志
└── LICENSE                         # GPL-3.0
```

---

## 配置文件详解

### 顶层字段

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `enabled` | bool | `true` | 插件总开关，`false` 时完全不调度 |
| `timezone` | string \| null | `null` | 时区，如 `"Asia/Shanghai"`；`null` 表示跟随 MCDR 进程的本地时区 |
| `check_interval_seconds` | number | `1.0` | 调度线程轮询间隔（秒），一般不用改 |
| `skip_missed_notifications` | bool | `true` | 计划生成时已经过期的提醒是否跳过（避免重载后补发一堆过期提醒） |
| `notify_on_join` | bool | `true` | 玩家进服时是否私聊告知重启倒计时 |
| `join_message` | string | 见示例 | 进服私聊内容，支持占位符 |
| `log_history` | bool | `true` | 是否把每次重启写入 `config/scheduled_restart/history.jsonl` |
| `history_size` | int | `100` | 历史文件过大时保留的最近条数 |
| `default_notifications` | array | 5 条 | **默认提醒组**，计划里 `use_default_notifications: true` 时使用 |
| `schedules` | array | 3 个示例 | 重启计划列表 |
| `_readme` | array | — | 写在配置文件里的说明，仅供阅读，可随意修改 |

### `schedules[]`（计划）

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `name` | string | `计划N` | 计划名，指令里也可以用序号代替；重名会自动加 `#2` |
| `enabled` | bool | `true` | 该计划是否启用 |
| `cron` | string | 必填 | cron 表达式，见下节 |
| `restart_method` | string | `mcdr_restart` | `mcdr_restart` / `stop` / `stop_exit` / `custom` / `none` |
| `custom_command` | string | `stop` | `restart_method=custom` 时下发的服务端指令 |
| `custom_auto_start` | bool | `false` | `custom` 模式下，等服务器停止后是否自动 `start()` |
| `restart_delay_seconds` | number | `0` | 到点后再延迟多少秒真正重启（支持 `"30s"`、`"1m"` 写法）；**提醒以真正重启的时刻为基准** |
| `kick_players` | bool | `false` | 重启前是否先 `kick @a` 踢人（1.20.3+ 才支持 `@a` 选择器） |
| `kick_message` | string | 见示例 | 踢人提示语 |
| `use_default_notifications` | bool | `true` | `true` 用顶层 `default_notifications`；`false` 用本计划的 `notifications` |
| `notifications` | array | `[]` | 本计划专属提醒；写了这个数组但没写 `use_default_notifications` 时，自动视为 `false` |

`restart_method` 说明：

* `mcdr_restart`：调用 MCDR 的 `server.restart()`（软停服 → 等待 → 重新启动），MCDR 进程保持运行
* `stop`：只下发停服指令，MCDR 继续运行（适合自己有用进程守护/编排重启的场景）
* `stop_exit`：停服后让 MCDR 一起退出（适合交给 systemd 等守护进程拉起）
* `custom`：执行 `custom_command`，可选 `custom_auto_start`
* `none`：只发提醒不重启（可用于「提前预告维护」）

### `notifications[]`（提醒，数组里放对象）

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `enabled` | bool | `true` | 是否启用这条提醒 |
| `advance_seconds` | number \| string | `60` | 提前多少秒发送；支持 `"5m"`、`"1h30m"`、`"1天2小时"`，也可写成同义的 `advance` |
| `type` | string | `chat` | `chat`（聊天框）/ `title`（大标题）/ `actionbar` / `command` |
| `message` | string | `""` | `chat` / `actionbar` 的文本 |
| `title` | string | `""` | `title` 的主标题 |
| `subtitle` | string | `""` | `title` 的副标题 |
| `times` | object | `{"fade_in":1,"stay":4,"fade_out":1}` | 大标题的淡入/停留/淡出时间，**单位秒**（内部换算成 tick） |
| `color` | string \| null | `null` | 颜色名（`yellow`、`red`、`gold`…）或 `#RRGGBB`；也可以直接写在文本里用 `§e` |
| `sound` | string \| null | `null` | 音效 ID，如 `minecraft:block.note_block.pling`，会在提醒后播放 |
| `sound_source` | string | `master` | 音效频道；老版本（1.8~1.12）服务端可设为 `""` 以省略该参数 |
| `sound_volume` / `sound_pitch` | number | `1.0` | 音量 / 音调 |
| `command` | string | `""` | `type=command` 时下发的指令，支持占位符 |

一个「多条提醒」的完整例子（也支持简写 `advance`）：

```json
{
  "name": "每天凌晨4点重启",
  "enabled": true,
  "cron": "0 4 * * *",
  "restart_method": "mcdr_restart",
  "use_default_notifications": false,
  "notifications": [
    { "advance": "30m", "type": "chat",  "message": "§e[维护] §f服务器将在 §b{remaining} §f后重启（§b{time}§f）" },
    { "advance": "10m", "type": "title", "title": "§e重启倒计时", "subtitle": "§f剩余 §b{remaining}",
      "times": { "fade_in": 0.5, "stay": 3, "fade_out": 0.5 },
      "sound": "minecraft:block.note_block.pling" },
    { "advance_seconds": 60, "type": "actionbar", "message": "§c60 秒后重启" },
    { "advance_seconds": 10, "type": "title", "title": "§c马上重启！", "subtitle": "§f快找地方下线" },
    { "advance_seconds": 0,  "type": "chat",  "message": "§c[维护] §f服务器正在重启，请稍后重新连接" }
  ]
}
```

---

## cron 写法

沿用 Linux cron：**5 个字段** `分 时 日 月 周`；也支持 **6 个字段** `秒 分 时 日 月 周`。

| 写法 | 含义 |
| --- | --- |
| `*` / `?` | 任意值 |
| `5` | 具体值 |
| `1-10` | 范围 |
| `1,3,5` | 列表 |
| `*/15` | 步长（每 15 分钟） |
| `1-10/2` | 范围内步长 |
| `5/10` | 从 5 开始、步长 10 直到最大值（cronie 扩展） |
| `JAN`~`DEC` | 月份英文缩写 |
| `SUN`~`SAT` | 星期英文缩写；`0` 与 `7` 都表示周日 |
| `@yearly` / `@monthly` / `@weekly` / `@daily`(`@midnight`) / `@hourly` / `@minutely` | 常用宏 |

常用示例：

| cron | 含义 |
| --- | --- |
| `0 4 * * *` | 每天 04:00 |
| `30 3 * * 1` | 每周一 03:30 |
| `0 5 1 * *` | 每月 1 日 05:00 |
| `0 0 1 1 *` | 每年 1 月 1 日 00:00 |
| `0 */6 * * *` | 每 6 小时（0、6、12、18 点） |
| `0 4 * * 1,4` | 每周一、周四 04:00 |
| `0 4 1 * 1` | 每月 1 日 **或** 每周一 04:00（日与周同时限定时取「或」，与 cron 惯例一致） |
| `30 0 4 * * *` | 6 字段写法：每天 04:00:30 |
| `@daily` | 每天 00:00 |

`!!srestart list` 会把每个表达式翻译成中文（例如 `每天 04:00`、`每周一 03:30`），方便核对。

---

## 占位符

写在 `message` / `title` / `subtitle` / `command` / `join_message` 里，发送时替换：

| 占位符 | 含义 | 示例 |
| --- | --- | --- |
| `{remaining}` | 中文剩余时长 | `5分`、`1小时2分3秒` |
| `{remaining_seconds}` | 剩余秒数（整数） | `300` |
| `{remaining_minutes}` | 剩余分钟数（向上取整） | `5` |
| `{time}` | 重启时刻 | `04:00:00` |
| `{date}` | 重启日期 | `2026-07-22` |
| `{datetime}` | 完整时刻 | `2026-07-22 04:00:00` |
| `{schedule}` | 计划名 | `每天凌晨4点重启` |
| `{cron}` | cron 表达式 | `0 4 * * *` |
| `{index}` / `{total}` | 这是第几条提醒 / 共几条 | `2` / `5` |

---

## 指令与权限

根指令：`!!srestart`（`!!srestart help` 查看帮助）。查看类需要 **helper**，操作类需要 **admin**；控制台默认为最高权限。

计划统一用 **序号** 指代（`!!srestart list` 里显示的 `[#1]`、`[#2]`…），也兼容直接写计划名；
序号就是配置文件 `schedules` 数组的下标 +1，即使某条计划写错被跳过也不会错位。

| 指令 | 权限 | 说明 |
| --- | --- | --- |
| `!!srestart list` | helper | 所有计划：序号、开关状态、cron、中文描述、下次重启时间与剩余时间、提醒条数 |
| `!!srestart next` | helper | 下一次重启的时间与倒计时 |
| `!!srestart status` | helper | 总开关、调度线程、时区、当前计划、配置警告/错误 |
| `!!srestart history [条数]` | helper | 最近的重启记录（时间 / 计划 / 方式 / 自动或手动） |
| `!!srestart test <序号\|计划名>` | admin | 按提前量依次发送该计划的提醒，**不会重启**，用于预览 |
| `!!srestart enable [<序号\|计划名>\|all]` | admin | 启用计划并**写入配置文件**（立即生效） |
| `!!srestart disable [<序号\|计划名>\|all]` | admin | 禁用计划并**写入配置文件**（立即生效）；不带参数表示全部 |
| `!!srestart add <计划名> <cron>` | admin | 新建计划：默认启用、使用顶层 `default_notifications` 提醒组 |
| `!!srestart remove <序号\|计划名>` | admin | 删除计划 |
| `!!srestart reload` | admin | 重新读取配置文件并重建调度 |
| `!!srestart cancel` | admin | 取消当前待执行的重启（本次不重启，下次照常） |

几个例子：

```
!!srestart list                                    # 看序号
!!srestart add 每日重启 0 4 * * *                   # 新建：每天 04:00，立刻生效
!!srestart add "每周一 凌晨" 30 3 * * 1              # 名字带空格用引号
!!srestart test 2                                  # 预览 2 号计划的提醒（不会重启）
!!srestart disable 2                               # 关掉 2 号计划并写进配置文件
!!srestart enable all                              # 打开全部计划
!!srestart remove 2                                # 删除 2 号计划
```

序号和计划名都支持 Tab 补全。**想立刻重启服务器请用 MCDR 自带的 `!!MCDR server restart`**
（本插件专注于"按时重启"，不再重复提供立刻重启的指令）。

> `add` 只要求「名字 + cron」：新建出来的计划默认启用、使用顶层默认提醒组；
> 想让某条计划用自己的提醒，编辑配置文件里的 `notifications` 并把
> `use_default_notifications` 改成 `false` 即可。
>
> `enable` / `disable` 是**改配置文件**的（持久），不是只对本次运行生效；
> 它直接操作 `schedules` 数组里对应条目的 `enabled` 字段，其它内容（你自己加的字段、
> 注释性的 `_readme` 等）原样保留。所以配置文件里本来就是关闭的计划也能被打开。

---

## 常见问题

**Q：为什么默认配置里所有计划都是关的？**
避免装上插件后服务器在你没准备好的时候突然重启。改完 `enabled` 再 `reload` 即可。

**Q：`{remaining}` 显示的时间和实际差几秒？**
调度精度是 `check_interval_seconds`（默认 1 秒），提醒按「到点即发」处理，正常误差在 1 秒内。

**Q：插件重载 / 服务器重启后，过去的提醒会补发吗？**
默认不会（`skip_missed_notifications: true`）。例如 3:59 才启动插件而计划 4:00 重启，
5 分钟和 1 分钟的提醒会被标记为「已过期」跳过，只发还来得及的那几条；设为 `false` 则会立刻补发。

**Q：`timezone` 在 Windows 上无效？**
Windows 自带的时区数据库 Python 读不到，需要 `pip install tzdata` 才能使用 `"Asia/Shanghai"` 这类名字；
没装时会打印一条警告并自动回退到跟随 MCDR 进程的本地时区。

**Q：`kick_players` 没生效？**
`kick @a` 需要 Minecraft 1.20.3+；老版本请保持 `false`，让服务器关服时自然踢人，或用
`type: "command"` 的提醒自己下发 `tellraw`/`kickall` 之类的指令。

**Q：`stop` 和 `mcdr_restart` 怎么选？**
由 MCDR 负责重启选 `mcdr_restart`；如果用 systemd/screen/宝塔等外部守护负责拉起，选 `stop`
（或 `stop_exit`，让 MCDR 也退出，避免卡住）。

**Q：配置写错了会怎样？**
* 单个字段类型写错 → 使用默认值，并记一条警告，插件照常运行（会顺手把规范化后的配置写回文件）
* 整个计划的 `cron` 写错 → 只跳过该计划，记一条错误，**不写回文件**（保留你写的内容方便修正）
* 用 `!!srestart status` 或看控制台日志都能看到这些警告/错误

**Q：能同时配多条计划吗？**
可以。调度器每次只执行「最近的一次」重启，避免两个计划挨得太近互相打架；被取消的那一次不会再触发。

**Q：怎么立刻重启服务器？**
用 MCDR 自带的 `!!MCDR server restart`（admin）。本插件的 `trigger` 指令已按需求移除——
它当时只是借用计划的重启方式、提醒全部跳过，和计划本身关系很弱。想让玩家先收到提醒再重启，
就把计划的 cron 设到最近的整分/整点，或用 `!!srestart test <序号>` 先预览提醒文案。

**Q：`enable` / `disable` 是改配置文件还是只对本次运行生效？**
改配置文件（持久）：直接写 `schedules[i].enabled` 并立刻热重载生效。所以配置文件里本来就是
关闭的计划也能用 `!!srestart enable <序号>` 打开；写错了 cron 的计划用序号同样能开关，
插件会顺带提醒你「cron 有误，修正后才会真正生效」。配置文件里其它内容（你自己加的字段等）不会被动。

**Q：服务器没在运行时，提醒会怎样？**
会跳过并记一条警告（`服务器当前未运行，跳过提醒`）。因为服务器进程不在时 MCDR 无法把
`tellraw` / `title` 送进游戏，只会丢一句 `Server has been terminated, cannot send command to its stdin`；
计划本身会保留，等服务器起来后照常执行。重启动作同理——服务器没运行时会跳过而不是假装执行成功。

**Q：插件自己的日志在哪里看？**
打在 MCDR 控制台（`[scheduled_restart]` 前缀）。MCDR 的 `logs/MCDR.log` 主要记录 MCDR 自身消息，
所以插件另外把每次重启写进 `config/scheduled_restart/history.jsonl`，可用 `!!srestart history` 查看。

---

## 开发与测试

本项目在**虚拟环境**里开发和验证（MCDReforged 2.16.0 + Python 3.13）：

```bash
# 创建虚拟环境并安装 MCDR / pytest
uv venv --python 3.13 .venv
uv pip install --python .venv/Scripts/python.exe mcdreforged pytest     # Windows
# uv pip install --python .venv/bin/python mcdreforged pytest           # Linux

# 跑测试（216 个用例：cron 解析、配置容错、配置文件读改写、提醒渲染、调度时序、指令树、打包结构、插件生命周期）
.venv/Scripts/python.exe -m pytest -q

# 重新生成示例配置 / 打包插件
.venv/Scripts/python.exe tools/generate_example_config.py
.venv/Scripts/python.exe tools/build_plugin.py
```

单元测试用假服务器接口（`tests/fakes.py`）驱动真实的调度逻辑与 **MCDR 自己的指令解析器**，
并用 MCDR 的元数据校验器、`zipimport` 验证 `.mcdr` 包的合法性；
调度部分的时间全部来自可注入的时钟，测试是确定性的（不依赖真实等待）。

### 真实 MCDR 端到端验证

除单元测试外，还用**真实的 MCDReforged 2.16.0 进程**跑了三类场景（工装、原始证据与复现步骤见
[`e2e/`](e2e/README.md)）。场景 ① 是定时重启全链路（`cron: */20 * * * * *`，两轮重启）：

* 插件在 15:25:29 加载并算出下次重启 `15:25:40`，两条已过期且服务器尚未启动的提醒被正确跳过并给出警告
* 15:25:32（提前 8 秒）真实下发 `title @a times 10 40 10` → `title @a subtitle [...]` → `title @a title {...}` → `playsound ...`
* 15:25:36（提前 4 秒）`command` 类提醒真实下发 `say E2E-COMMAND-TEST 4`
* 15:25:40（到点）下发 `tellraw` 后执行 `stop`，服务端进程退出（code 0）并被 MCDR 重新拉起（新 PID），
  计划顺延到 `15:26:00`；第二轮提醒同样严格按 15/12/8/4/0 秒提前量发出
* 插件写下 2 条 `history.jsonl` 记录，`starts.log` 记录到 3 次服务端启动（初次 + 2 次重启）

场景 ② **指令增删改查**——在真实 MCDR 内用 `execute_command` 执行 13 条 `!!srestart` 指令，
每步之后比对配置文件快照：

* `add` 真的追加计划并立刻参与调度（`已创建计划 #2 凌晨测试（每天 07:00…）`）；重名与坏 cron 都被拒绝且文件不变
* `disable 1` / `enable 2` 真的改写了 `schedules[i].enabled`，其中 2 号是**配置文件里本来就是关闭**的计划
* `remove 2` 按序号删对条目，后续 `list` 序号自动前移；`test` 只发提醒不重启

场景 ③ **带 UTF-8 BOM 的配置文件**（Windows 记事本、PowerShell `Set-Content -Encoding UTF8` 的默认行为）：

* 修复前实测：MCDR 读取失败 → 按 `regen` 策略把**用户配置重置成默认的 3 个示例计划**（计划直接丢失）
* 修复后：读取前自动去 BOM，用户计划完整保留（`初始配置: [('BOM保留测试', True), ('计划里就是关闭的', False)]`）；
  文件彻底坏掉时会先备份成 `config.json.broken-<时间>` 再重新生成

> 小提示：MCDR 的日志文件 `logs/MCDR.log` 主要记录 MCDR 自身的消息，插件日志打在控制台；
> 想事后查证「哪次重启、什么时候、手动还是自动」，看 `config/scheduled_restart/history.jsonl`
> 或执行 `!!srestart history` 更直接。

---

## 设计说明

* **单计划调度**：调度线程每 `check_interval_seconds` 醒一次，算出「所有启用计划里最近的一次重启」，
  然后只维护这一个计划；每秒检查有没有到某条提醒的发送时刻。
* **提醒时刻 = 真正重启时刻 − `advance_seconds`**；`restart_delay_seconds` 会同时推迟提醒基准，
  保证「还有 5 分钟」这类文案始终准确。
* **重启在独立工作线程里执行**：`server.restart()` 会阻塞到服务器重启完成，
  放在工作线程里不会卡住调度循环；重启期间暂停发送提醒。
* **防重复触发**：已经触发或已被取消的「某个计划 + 某个时刻」会被记下来，同一时刻绝不会重启两次。
* **cron 计算效率**：按天跳着找匹配日期，再在当天挑最近的时分秒，不用逐分钟暴力扫描。
* **序号对齐配置文件**：每个计划记住自己在 `schedules` 数组里的下标（`source_index`），
  `list` 显示的 `[#N]` 就是它 +1；即使某条计划 cron 写错被跳过，序号也不会错位，
  `enable/disable/remove <序号>` 永远指向你看到的那一条。
* **改配置的指令直接改文件**：`enable / disable / add / remove` 只改 `schedules` 数组里对应条目的字段，
  你自己加的字段与注释性内容原样保留；写入采用「先写临时文件再替换」；
  读取前会去掉 UTF-8 BOM，文件彻底损坏时先备份成 `config.json.broken-<时间>` 再重新生成。

---

## English summary

**scheduled_restart** is an [MCDReforged](https://github.com/MCDReforged/MCDReforged) plugin that
restarts your Minecraft server on a **Linux cron schedule** and warns players beforehand with
**chat messages** and/or **on-screen titles**.

* Cron syntax: 5 fields `min hour day month weekday`, or 6 fields with leading seconds.
  Supports `*` `?` `a-b` `a,b` `*/n` `a-b/n` `a/n`, `JAN`-`DEC`, `SUN`-`SAT`, and macros like `@daily`.
* Unrestricted number of reminders per schedule, written as an **array of objects**:

  ```json
  {
    "name": "Daily restart",
    "enabled": true,
    "cron": "0 4 * * *",
    "use_default_notifications": false,
    "notifications": [
      { "advance": "10m", "type": "chat",  "message": "§eServer restarts in §b{remaining}" },
      { "advance_seconds": 30, "type": "title", "title": "§eRestarting soon", "subtitle": "§f{remaining}" },
      { "advance_seconds": 0, "type": "chat", "message": "§cRestarting now, please reconnect later" }
    ]
  }
  ```

* Notification types: `chat` / `title` / `actionbar` / `command`. Placeholders:
  `{remaining}` `{remaining_seconds}` `{remaining_minutes}` `{time}` `{date}` `{datetime}`
  `{schedule}` `{cron}` `{index}` `{total}`.
* Restart methods: `mcdr_restart` (default), `stop`, `stop_exit`, `custom`, `none`.
* In-game commands (view = helper, changes = admin):
  `!!srestart list|next|status|history`, `!!srestart add <name> <cron>`,
  `!!srestart remove|enable|disable|test <#index>`, `!!srestart reload|cancel`.
  To restart the server *right now*, use MCDR's own `!!MCDR server restart`.
* No third-party Python dependencies; the cron parser is built in.
* Tested with unit tests (216) plus a real MCDReforged end-to-end run — see [`e2e/`](e2e/README.md).
* Requires MCDReforged >= 2.12.0 and Python >= 3.8.

Runtime messages and the documentation are currently in Chinese; the plugin itself is
locale-agnostic and every user-facing text is configurable.

---

## 许可证

Copyright (C) 2026 fangzi2006 <1439885013@qq.com>

本项目以 **GNU General Public License v3.0 or later** 发布，完整条款见 [LICENSE](LICENSE)。
你可以自由使用、修改、再发布，但衍生作品需要以同样的许可证开源。
更新记录见 [CHANGELOG.md](CHANGELOG.md)。
