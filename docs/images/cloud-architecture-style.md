# 手机云控架构图

采用 `diagram-design` 技能设计。用户已确认沿用原生 App 的灰黑与黄色；项目配色保存在本文，不改动个人全局技能配置。

## 文件与用途

| 文件 | 用途 |
|---|---|
| [浅色 HTML](cloud-architecture.html) | 可编辑图源；用浏览器打开 |
| [深色 HTML](cloud-architecture-dark.html) | 与 App 配色一致的可编辑图源 |
| [完整说明 HTML](cloud-architecture-full.html) | 图与上下文说明，适合单独展示 |
| [浅色 SVG](cloud-architecture.svg) / [深色 SVG](cloud-architecture-dark.svg) | README 的主题自适应图片；可离线显示 |

画布为 1280 × 720，类型为 Architecture，面向使用者和开发者，保留核心组件与通信方向。HTML 在窄窗口内横向滚动，SVG 在 README 中按比例缩放。图没有脚本或交互。

## 项目样式

原始色值来自 [`Appearance.kt`](../../android-app/app/src/main/java/com/cyberbrick/remote/Appearance.kt)。背景为灰黑，黄色仅突出云端节点与“无需电脑”的结论；浅色版将黄色文字和线条加深，避免低对比度。

| 角色 | 深色 | 浅色 |
|---|---|---|
| 背景 | `#12151A` | `#F5F5F2` |
| 面板 | `#1D222A` | `#EBEDEB` |
| 正文 | `#F2F3F5` | `#171A20` |
| 次级文字 | `#B5BECC` | `#4B5563` |
| 弱化标签 | `#AAB2BF` | `#596270` |
| 边框 | `#7F8A99` | `#737D89` |
| 强调文字与边框 | `#E8C547` | `#806400` |
| 强调背景 | `#29281F` | `#F7F0D1` |
| 品牌黄色 | `#E8C547` | `#E8C547` |

标题 40 px，节点名 16 px，中文说明与箭头标签 12 px；英文眉题 8 px。正文用 Roboto / Noto Sans SC，标题用 Instrument Serif / Noto Serif SC，技术名称用 Geist Mono；均提供系统中文字体回退。HTML 可从 Google Fonts 加载字体，断网仍可显示。SVG 使用系统字体回退，不加载远程资源；不同系统的字形可能略有不同。

几何采用 4 px 网格，节点圆角 6 px，无阴影。云端是唯一重点节点；六条单向箭头各有独立端点，实线表示指令与语音上传，虚线表示回复与状态返回。标签与连线至少留 8 px 空隙。

## 内容保真记录

原 README Mermaid 图有 **4 个节点、3 组双向关系**。重绘保留全部 4 个节点，将每组双向关系拆为两条单向箭头，共 **6 条箭头**；没有合并、折叠或删除组件，也没有增加摄像头。

| 原关系 | 重绘说明 |
|---|---|
| 手机 ↔ 云服务器 | 操作、语音上传；状态与语音回复返回 |
| 云服务器 ↔ 灵矽 | 语音上传；回复与工具调用返回 |
| 小车 ↔ 云服务器 | 动作指令下发；上线与执行应答返回，注明由小车主动建连 |

方向表示业务数据流，非握手顺序，也不是完整协议时序。手动操作直接进入云端执行器；灵矽仅参与语音理解与回复。Device-Id 留在服务器、车端看门狗和热点信息来自当前实现；图中没有部署地址或凭据。

## 编辑与导出

修改 HTML 图源后，同步浅色、深色和完整说明版中的内容，再重新导出两个 SVG。不要只编辑导出文件。

在已安装 `diagram-design` 的环境中，将下面的 `<skill-dir>` 替换为该技能目录，从仓库根目录运行：

```bash
python3 <skill-dir>/scripts/self_check.py docs/images/cloud-architecture.html
python3 <skill-dir>/scripts/self_check.py docs/images/cloud-architecture-dark.html
python3 <skill-dir>/scripts/self_check.py docs/images/cloud-architecture-full.html
python3 <skill-dir>/scripts/export_svg.py docs/images/cloud-architecture.html docs/images/cloud-architecture.svg --system-fonts
python3 <skill-dir>/scripts/export_svg.py docs/images/cloud-architecture-dark.html docs/images/cloud-architecture-dark.svg --system-fonts
```

没有安装技能也可直接编辑 HTML 和用浏览器查看；页面本身不依赖技能运行时。旧 `architecture.html` / `architecture.png` 用于历史电脑模式文档，本次不替换。

## 验证记录

2026-11-02：三个 HTML 均通过技能自检；两个 SVG 由技能脚本导出，已在浏览器检查浅色、深色 SVG 及完整说明 HTML，没有发现文字遮挡或连线交叉。文字颜色组合的最低对比度分别为浅色 4.90:1、深色 6.93:1。仅验证文档图片，不代表新增 App、云端或实车功能验收。
