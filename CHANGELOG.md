# 更新日志

## v1.2.1

### 新增

* 自定义简化指令别名 `command_alias`：配置 `"!!sr"` 后，`!!sr` 与 `!!srestart` 共存且完全等价
* 提醒字段统一为 `advance_time`：支持数字秒数或 `"5m"` / `"1h30m"` / `"1天2小时"` 等时长文本

### 变更

* **破坏性**：移除冗余的 `advance_seconds` 与 `advance` 字段，统一使用 `advance_time`

### 修复

* 权限不足时的提示不明确；现在会明确告知玩家需要 MCDR admin 权限，并给出 `!!MCDR permission set` 设置指令
* Minecraft 的 `/op` 不等于 MCDR admin，无 admin 权限时变更类指令会提示正确的赋权方式

## v1.2.0

### 新增

* 游戏内指令权限细化：`!!srestart list` 与 `!!srestart next` 现在允许**所有玩家**使用
* 配置文件自动升级：插件加载时会对比当前配置与默认配置，自动补齐新增字段、更新 `_readme` 说明、移除废弃键，并写回文件

### 修复

* 配置文件在插件更新后不会自动同步新增/删除的配置项（之前只有产生警告时才会回写）
* 重启历史文件 `history.jsonl` 只在大小超过 256KB 时才裁剪，导致小文件可能无限增长；现在改为按 `history_size` 条数即时裁剪
* 帮助文本中的参数说明与实际情况不一致：`test` / `enable` / `disable` / `remove` 均支持序号或计划名
* 权限不足时的提示不明确；现在会明确告知玩家需要 MCDR admin 权限，并给出 `!!MCDR permission set` 设置指令

## v1.1.1

### 修复

* **音效指令少了坐标（会导致指令报错）**：Java 的 `playsound` 语法是
  `… <目标> [<坐标>] [<音量>] [<音调>]`，**坐标在音量前面**。之前生成的是
  `playsound <音效> master @a 1 1`，其中 `1 1` 会被当成坐标（坐标需要 x y z 三个分量）而报错，
  配了 `sound` 的提醒实际上发不出音效。现在默认改用「每个玩家在自己位置播放」的写法：
  `execute as @a at @s run playsound <音效> master @s ~ ~ ~ <音量> <音调>`（需要 1.13+）

### 新增

* `sound_position` 配置项：`"@s"`（默认，逐玩家原地播放）、坐标文本
  （`"~ ~ ~"`、`"100 64 100"`，1.8+ 都能用，位置锚定在指令执行位置/世界出生点）、
  或 `""`（省略坐标与音量音调，交给服务端默认值）
* 音效指令的回归测试：断言「音量/音调永远排在坐标之后」以及「不给坐标时音量音调一起省略」

### 变更

* 默认生成的配置更清爽：`_readme` 说明改成纯文本描述，不再出现需要 JSON 转义的引号
  （`advance` 字段用法、`times` 的 `fade_in`/`stay`/`fade_out`、`sound_position` 的取值都改写过了）；
  示例计划与示例提醒各只保留 1 个（示例计划默认仍是关闭的）

## v1.1.0

### 新增

* `!!srestart add <计划名> <cron>`：命令行新建计划（默认启用、使用顶层默认提醒组）
* `!!srestart remove <序号|计划名>`：按序号删除计划
* 计划统一用 **序号** 指代（`!!srestart list` 里显示的 `[#1]`、`[#2]`…），也兼容计划名，支持 Tab 补全
* `!!srestart add` 新建的计划立刻参与调度，无需重启 MCDR

### 变更

* `!!srestart enable / disable` 改为**直接读写配置文件**（`schedules[i].enabled`）并立即热重载：
  配置文件里本来就是关闭的计划也能用指令打开，且开关是持久的
* **移除** `!!srestart trigger`：它只是借用计划的重启方式、提醒全部跳过，与计划本身关系很弱。
  需要立刻重启服务器请使用 MCDR 自带的 `!!MCDR server restart`
* `!!srestart list` 不再显示"运行时被禁用"，开关状态只有配置文件一个来源

### 修复

* **带 UTF-8 BOM 的配置文件会导致用户计划被重置**：Windows 记事本 / PowerShell
  `Set-Content -Encoding UTF8` 写出的配置带 BOM，MCDR 读取失败后会按 `regen` 策略把配置
  重置成默认的示例计划。现在读取前会自动去掉 BOM
* 配置文件彻底无法解析时，先备份成 `config.json.broken-<时间>` 再重新生成，避免内容找不回来
* 服务器未运行时不再把提醒记成"已发送"（MCDR 实际只会丢掉这些指令），改为跳过并给出明确警告

## v1.0.0

* 首个版本：Linux cron 表达式（5/6 段、宏、别名）驱动的定时重启
* 数组套对象的提醒配置，支持聊天框 / 大标题 / 物品栏 / 执行指令四种通知方式
* 支持提前量、占位符、颜色、音效、进服私聊提醒、重启历史、配置热重载
* `!!srestart list / next / status / history / reload / cancel / test / enable / disable` 指令
