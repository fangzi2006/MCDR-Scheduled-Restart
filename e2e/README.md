# 真实 MCDReforged 端到端测试

`tests/` 里的单元测试用的是「假服务器接口」。为了确认插件在**真实 MCDR 进程**里也能跑通，
这里放了一套最小可复现的端到端环境，用它跑过三类场景：

| 场景 | 工装 | 结论 |
| --- | --- | --- |
| A. 定时 → 提醒 → 重启 → 服务端重新起来 | `fake_server.py`（模拟 Vanilla 服务端） | 两轮重启全部按秒级提前量正确执行 |
| B. `!!srestart` 指令的增删改查 | `driver_plugin.py`（在真实 MCDR 内用 `execute_command` 跑指令） | `list/add/disable/enable/test/remove` 全部正确，配置文件的写入符合预期 |
| C. 带 UTF-8 BOM 的配置文件 | 用 Windows PowerShell `Set-Content -Encoding UTF8` 写配置 | 修复后用户计划不再被重置（修复前会被重置成默认配置） |

## 复现步骤

```bash
# 1. 准备一个测试用的 MCDR 工作目录
mkdir e2e-mcdr && cd e2e-mcdr
<mcdr 所在的 python> -m mcdreforged init

# 2. 放入模拟服务端与测试驱动插件
cp <本目录>/fake_server.py server/fake_server.py
cp <本目录>/driver_plugin.py plugins/e2e_driver.py

# 3. 改 config.yml（关键几项）
#    start_command:
#    - <python 路径>
#    - fake_server.py
#    advanced_console: false        # 输出被重定向时必须关掉 prompt-toolkit
#    disable_console_thread: true   # stdin 不是终端时避免控制台线程空转
#    check_update: false
#    telemetry: false
#    write_server_output_to_log_file: true

# 4. 放入插件与测试用配置
cp ../dist/scheduled_restart-v1.1.1.mcdr plugins/
#    场景 A 的配置见下方；场景 B/C 用随便一份配置即可

# 5. 跑起来
<mcdr 所在的 python> -m mcdreforged start
```

`driver_plugin.py` 会在服务器启动后，从 `server/e2e_commands.txt` 读取指令（每行一条，
不存在则用内置的默认序列），通过 ``server.execute_command`` 在真实 MCDR 里执行，
并把每步之后的 `config/scheduled_restart/config.json` 快照写进
`server/e2e_snapshots.log`（UTF-8，避免控制台编码问题）。

### 场景 A 的测试配置

`config/scheduled_restart/config.json`（把周期压到 20 秒，方便快速观察）：

```json
{
    "enabled": true,
    "check_interval_seconds": 0.5,
    "skip_missed_notifications": false,
    "schedules": [
        {
            "name": "端到端-每20秒",
            "enabled": true,
            "cron": "*/20 * * * * *",
            "restart_method": "mcdr_restart",
            "use_default_notifications": false,
            "notifications": [
                { "advance_seconds": 15, "type": "chat",      "message": "§e[E2E] 还有 {remaining} 重启，{time}" },
                { "advance_seconds": 12, "type": "actionbar", "message": "§bE2E actionbar {remaining}" },
                { "advance_seconds": 8,  "type": "title",     "title": "§e重启倒计时", "subtitle": "§f剩余 §b{remaining}",
                  "times": { "fade_in": 0.5, "stay": 2, "fade_out": 0.5 },
                  "sound": "minecraft:block.note_block.pling" },
                { "advance_seconds": 4,  "type": "command",   "command": "say E2E-COMMAND-TEST {remaining_seconds}" },
                { "advance_seconds": 0,  "type": "chat",      "message": "§c[E2E] 服务器正在重启" }
            ]
        }
    ]
}
```

> `config.yml` 里的 `start_command` 必须是**列表**形式（exec 模式）。
> 在受限沙箱（例如 DSH 的 workspace-write）里 MCDR 无法用管道启动子进程，需要更宽权限才能运行。

## 场景 A 实测结果

MCDR 2.16.0 + Python 3.13.7，计划 `cron: */20 * * * * *`（每 20 秒），两轮完整重启：

| 时间 | 实际发生的事 | 证据 |
| --- | --- | --- |
| 15:25:29 | 插件加载，算出下次重启 `15:25:40`，5 条提醒 | `[ScheduledRestart/INFO] 下一次重启：端到端-每20秒 于 2026-09-30 15:25:40` |
| 15:25:29 | 15s / 12s 两条提醒已过期且服务器尚未启动 → 跳过并警告（不谎报已发送） | `[ScheduledRestart/WARNING] 服务器当前未运行，跳过提醒（…提前 15 秒，方式 chat）` |
| 15:25:32 | 提前 8 秒的大标题提醒 + 音效 | `title @a times 10 40 10`、`title @a subtitle [...]`、`title @a title {...}`、`playsound minecraft:block.note_block.pling master @a 1 1` |
| 15:25:36 | 提前 4 秒的 `command` 类提醒 | `say E2E-COMMAND-TEST 4` |
| 15:25:40 | 提前 0 秒的聊天框提醒 → 执行重启 | `execute at @p run tellraw @a {...}` → `stop` |
| 15:25:40 | 服务端进程退出（code 0）并被重新拉起 | `Server stopped` → `Starting the server` → `Server is running at PID 33624` |
| 15:25:45 → 15:25:56 | 第二轮提醒严格按 15/12/8/4 秒提前量发出 | [`evidence/commands.log`](evidence/commands.log) |
| 15:26:00 | 第二轮重启完成，计划顺延到 `15:26:20` | [`evidence/history.jsonl`](evidence/history.jsonl) |

## 场景 B 实测结果

在真实 MCDR 里依次执行 13 条指令（配置初始为 1 个启用计划），
每步之后的配置文件快照见 [`evidence/e2e_snapshots_crud.log`](evidence/e2e_snapshots_crud.log)，
指令回复见 [`evidence/mcdr_command_replies.log`](evidence/mcdr_command_replies.log)：

| 指令 | 结果 |
| --- | --- |
| `!!srestart list` | `[#1] 每日重启 已启用`，含 cron、中文描述、下次时间、剩余时间、提醒条数 |
| `!!srestart add 凌晨测试 0 7 * * *` | `已创建计划 #2 凌晨测试（每天 07:00…）；默认已启用，并使用顶层 default_notifications 提醒组；配置已生效` |
| `!!srestart add 名字重复 0 9 * * *`（重名） | `已经存在名为 '名字重复' 的计划，请换一个名字`，文件未变化 |
| `!!srestart add 坏计划 99 * * * *` | `cron 表达式有误：分 字段的值 99 超出范围，可用值为 [0, 59]`，文件未变化 |
| `!!srestart disable 1` | `已禁用计划 #1 每日重启（已写入配置文件，立即生效）`，快照里 `enabled` 变 False |
| `!!srestart reload` | 内存配置与文件同步 |
| `!!srestart enable 2` | 打开一个**配置文件里本来就是关闭**的计划，快照里 `enabled` 变 True |
| `!!srestart test 2` | `正在按提前量依次发送 2 条测试提醒…` |
| `!!srestart remove 2` | `已删除计划 凌晨测试（原 cron: 0 7 * * *）`，后续 `list` 序号自动前移，顺序正确 |

## 场景 C：BOM 问题（真实运行发现并修复）

Windows 记事本、以及 Windows PowerShell 5.1 的 `Set-Content -Encoding UTF8`，
都会给 JSON 写出 UTF-8 BOM（`EF BB BF`）。修复前实测：MCDR 读取这种配置会失败，
按 `regen` 策略**直接把用户配置重置成默认的 3 个示例计划**，用户自己写的计划就这么没了：

```
# 修复前（真实运行输出）
初始配置: [('示例-每天凌晨4点重启（默认关闭，改 enabled 为 true 即生效）', False), ('示例-每周一凌晨3点半重启（默认关闭）', False), ('示例-每月1日凌晨5点重启（默认关闭）', False)]
```

现在 `load_config` 在读取前会先去掉 BOM（并打一条警告）；如果文件彻底无法解析，
会先备份成 `config.json.broken-<时间>` 再让 MCDR 重新生成。

```
# 修复后（真实运行输出，见 evidence/e2e_snapshots_crud.log 第一行）
初始配置: [('BOM保留测试', True), ('计划里就是关闭的', False)]
```

同一轮运行还顺便验证了「配置文件里本来就是关闭的计划能被 `enable` 打开」：

```
>>> (9/13) !!srestart enable 2
    配置快照: [('BOM保留测试', False), ('计划里就是关闭的', True), ('凌晨测试', True), ('名字重复', True)]
```

## 证据文件

* [`evidence/starts.log`](evidence/starts.log)：场景 A 服务端启动 3 次（初次 + 2 次重启）
* [`evidence/commands.log`](evidence/commands.log)：场景 A MCDR 真实下发的每条指令（含 JSON 文本组件、tick 时间、playsound）
* [`evidence/history.jsonl`](evidence/history.jsonl)：场景 A 插件写下的 2 条重启历史
* [`evidence/e2e_snapshots_crud.log`](evidence/e2e_snapshots_crud.log)：场景 B/C 每步之后的配置文件快照
* [`evidence/e2e_snapshots_bom.log`](evidence/e2e_snapshots_bom.log)：场景 C 单独验证 BOM 修复的快照
* [`evidence/mcdr_command_replies.log`](evidence/mcdr_command_replies.log)：场景 B 指令回复原文
* [`evidence/mcdr_console_excerpt.log`](evidence/mcdr_console_excerpt.log)：场景 A 控制台日志摘录

## 这些实测发现并修掉的问题

1. **服务端未启动时谎报「已发送提醒」**：MCDR 只会丢一条
   `Server has been terminated, cannot send command to its stdin`。
   现在 `scheduler._send()` 会先判断 `is_server_running()`，未运行时跳过并给出明确警告
   （测试 `test_notifications_are_skipped_when_server_not_running`）。
2. **带 BOM 的配置文件会导致用户计划被重置**：见场景 C，现在读取前自动去 BOM，
   无法解析时先备份（测试 `test_bom_is_stripped_so_plans_are_not_lost`、
   `test_broken_config_is_backed_up_before_regeneration`）。

## 已知盲区（这套工装测不到什么）

* **不校验 Minecraft 指令语法**：`fake_server.py` 只是把收到的指令原样记进
  `commands.log`，不会像真实服务端那样解析参数。所以「指令拼写/参数顺序错了」这类问题
  它抓不到——`playsound` 少了坐标就是这样漏过去的（后来由真实服务端实测发现，
  见 CHANGELOG v1.1.1）。**参数顺序类问题请务必在真实服务端上验收一次**，
  或者用 `type: "command"` 自己写指令。
* **不验证客户端表现**：`title`/`tellraw`/`playsound` 到底在玩家屏幕上/耳朵里是什么效果，
  这里只能确认「MCDR 把正确的指令发给了服务端」。
* **没有真实玩家**：`notify_on_join`（进服私聊倒计时）与 `kick_players` 只有单元测试覆盖。
* **沙箱限制**：MCDR 必须用管道读取服务端输出，受限环境（如 DSH 的 workspace-write）
  下 MCDR 起不来（`ServerStartError`），需要在更宽权限下运行本测试。

> 另外：本工装自己就踩过一次 BOM 的坑——`server/e2e_commands.txt` 是用
> Windows PowerShell 的 `Set-Content -Encoding UTF8` 写的（会带 BOM），
> 于是第一条指令被读成 `\ufeff!!srestart list`，这个 `\ufeff` 就留在了
> `evidence/e2e_snapshots_crud.log` 里（MCDR 容忍了它，指令照常执行）。
> 留着它当作"BOM 到处都会咬人"的现场记录。
