# 更新日志

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
