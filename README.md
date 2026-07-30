# nonebot-plugin-cs2radar-enhanced

基于 [luojisama/nonebot-plugin-cs2radar](https://github.com/luojisama/nonebot-plugin-cs2radar)
维护的增强版 NoneBot2 CS2 插件，提供职业选手查询、近期赛事/赛果、5E 战绩、完美平台战绩、
官匹战绩、账号绑定与单局详细复盘。

本仓库保留原项目 MIT License、原作者信息和上游链接；增强内容由 fork 维护者持续集成。

## 本 fork 的主要改动

### 完美与官匹查询

- `/pw` 支持完美昵称、SteamID64、Steam 自定义ID以及完整 Steam 个人主页链接。
- `/pw <玩家> [场数]` 可指定最近 `1–10` 场，默认显示 5 场；上半部分仍为当前赛季汇总。
- 修复昵称模糊搜索将参数误匹配为其他玩家的问题，并自动还原昵称中的 HTML 实体。
- `/官匹 <玩家> [场数]` 支持 SteamID64、Steam 自定义ID和 Steam 个人主页链接。
- `/官匹` 默认汇总最近 5 场，可指定 `1–10` 场，并使用与 `/pw` 一致的统计卡片 UI。
- 官匹卡片额外获取公开玩家资料以补充昵称和头像。
- 官匹接口不提供可靠的赛季武器统计，因此官匹卡片隐藏“常用武器”板块，地图统计自动占满整行。

### 图片与数据展示

- 最近比赛列表中的 Rating、K/D、WE 使用固定列宽和等宽数字，保证纵向对齐。
- 汇总最近官匹的胜负、平均 Rating、ADR、RWS、总击杀/死亡、高光及地图数据。
- 保留原有单场详细战绩、队伍数据、回合走势和 LLM 复盘卡片。

### 安全与稳定性

- 沿用并扩展 hardening 分支中的凭据保护、CSP、图片域名校验、命令并发限制和隐私控制。
- 完美平台 Session 推荐通过服务器环境变量提供，QQ 内登录默认关闭且仅允许超级用户私聊。
- 官匹/完美查询复用受保护的 Session，不在图片、消息或日志中展示 Token。

## 安装

### 安装增强版（推荐）

```shell
pip install "nonebot-plugin-cs2radar @ git+https://github.com/wzacolemak/nonebot-plugin-cs2radar-enhanced.git"
```

> PyPI 上的 `nonebot-plugin-cs2radar` 可能仍指向上游发布版本；需要本 fork 的增强功能时请使用上述 Git 地址。

### 安装 Playwright 浏览器

本插件依赖 `playwright` 渲染图片，请额外执行：

```bash
playwright install chromium
```

## 适配器

- `OneBot V11`

## 命令

- `cs查询 <选手名>`: 查询职业选手资料卡
- `cs赛事`: 查询近期赛事与比赛
- `赛果`: 查询近几日赛果
- `5e <ID/昵称>`: 查询 5E 战绩
- `pwlogin <手机号> <验证码>`: 可选的应急登录命令，仅限超级用户私聊且默认关闭
- `pw <昵称/SteamID/Steam自定义ID> [场数]`: 查询完美当前赛季统计与最近 1–10 场
- `官匹 <SteamID/Steam自定义ID> [场数]`: 汇总最近 1–10 场官匹，默认 5 场
- `bind <5e|pw> <玩家名>`: 绑定常用查询对象
- `match [5e|pw|mm] [@群友] [局数]`: 查询最近第 N 把详细对局并生成复盘

## 配置

环境变量统一使用 `cs2radar_*` 前缀。旧的 `cs_pro_*` 配置默认忽略；仅迁移期间显式设置
`cs2radar_allow_legacy_config=true` 时才会临时读取，并会打印弃用警告。

```env
cs2radar_llm_enabled=false
cs2radar_llm_api_type=openai
cs2radar_llm_api_url=https://api.openai.com/v1
cs2radar_llm_api_key=
cs2radar_llm_model=gpt-4o-mini

# 推荐：通过服务器环境变量提供完美平台 Session，不经过 QQ 消息
cs2radar_pw_token=
cs2radar_pw_steam_id=

# 默认不把 token 写入磁盘
cs2radar_pw_session_persist=false

# QQ 登录默认关闭；确需使用时仍只允许 SUPERUSER 私聊
cs2radar_pw_login_enabled=false

cs2radar_max_concurrency=2
cs2radar_cooldown_seconds=10
cs2radar_allow_query_others=false
cs2radar_allow_legacy_config=false

```

说明：

- LLM 默认关闭；启用后仅使用本插件自己的 `cs2radar_llm_*` 配置，不会复用其他插件的 Key。
- LLM API URL 必须使用 HTTPS，且不得在 URL 中嵌入用户名或密码。
- `pw` / `match pw` / `match mm` 依赖完美平台登录态，请优先通过服务器环境变量配置。
- 推荐在服务器环境变量中设置 `cs2radar_pw_token` 与 `cs2radar_pw_steam_id`。不要在群聊发送手机号和验证码。
- `cs2radar_allow_query_others` 默认关闭，避免群成员查询他人的绑定战绩。
- Linux 下敏感文件会收紧为仅当前用户可读写；Windows 部署应额外通过目录 ACL 或容器隔离保护数据目录。

## 数据存储与迁移

插件默认把数据存储到 `nonebot-plugin-localstore` 的插件数据目录下，并会尝试自动迁移旧版本的以下文件：

- `data/cs_pro/user_bindings.db`
- `data/cs_pro/user_data.db`
- `data/cs_pro/user_data.json`
- `data/cs_pro/pw_session.json`

## 常见问题

### 1. 提示找不到浏览器或渲染失败

请确认已经执行过：

```bash
playwright install chromium
```

### 2. `pw` 查询提示先登录

这是正常行为。推荐在服务器环境变量中配置 token 与 Steam ID。

如确需临时通过 QQ 登录，必须同时满足：

- 发送者在 NoneBot `SUPERUSERS` 中；
- 使用机器人私聊；
- 管理员显式设置 `cs2radar_pw_login_enabled=true`；
- 已确认 NapCat、NoneBot 和容器日志不会记录敏感消息。

之后在私聊执行：

```bash
pwlogin <手机号> <验证码>
```

### 3. AI 复盘没有返回

未配置 LLM Key、模型返回非 JSON、接口超时都会触发降级。此时插件仍会返回战绩图片，只是不附带 AI 结论。

## License

MIT

## 发布

推送语义化标签后会自动触发 GitHub Actions 发布到 PyPI，例如：

```bash
git tag v0.1.0
git push origin v0.1.0
```

工作流使用 PyPI Trusted Publishing，并将第三方 Actions 固定到具体提交。
