# JSON Forge

版本号由项目根目录的 `VERSION` 文件统一管理。

一个使用 Python + PySide6 编写的本地 JSON 桌面工具。项目目录名为
`json-forge`。

## 功能

- 从日志或其他混杂文本中提取首个完整 JSON 对象/数组并格式化
- 工具栏“压缩JSON”一键紧凑压缩
- 键名切换为双引号、单引号或无引号（不适合裸写的特殊键名会保留引号）
- 光标所在位置实时显示 JSONPath，可复制带 `$` 或不带 `$` 的路径
- JSON / JSON-like 语法高亮，支持对象和数组路径
- “设置”菜单可切换浅色/深色主题，并自动记住选择
- 支持双引号、单引号和无引号属性名混用的 JSON-like 输入；转换前会提示是否净化
- Chrome 风格多标签编辑，可新增、关闭、拖动排序，双击或右键标签可重命名
- “设置 → 关于”显示软件版本及当前 Python 版本
- 非空标签关闭前确认；“设置 → 更多设置 → General”可控制退出确认提示
- “Tab样式”可在默认的“实用模式”和经典“扁平模式”之间切换并记住选择
- General 中可切换中文/English 界面并自动记住选择，快捷键提示会适配运行平台
- General 中可使用“恢复默认配置”，从 `config/settings.default.ini` 恢复全部设置
- General 中可配置“禁止多实例”（默认开启），避免重复启动多个 JSON Forge 窗口
- General 中可配置“收尾连接虚线”（默认开启），用于显示括号结束符对齐引导线
- 编辑器默认显示行号，可在 General 中关闭；对象与数组支持点击 gutter 箭头折叠/展开
- 工具栏“折叠/展开”按钮可一次收起或展开当前标签的全部节点
- 工具栏“换行”按钮可切换过长行是否自动换行，默认开启并保存到 `settings.ini`
- 支持按 Tab 独立设置行书签：点击行号区书签栏或使用 `Ctrl/Cmd + F2` 切换，`F2` / `Shift + F2` 循环跳转
- 退出后自动恢复上次会话：Tab 内容、标题、当前 Tab、光标、滚动位置、折叠状态和书签保存在 `cache/session.json`
- `Ctrl/Cmd + F` 浮动搜索，支持大小写、Whole Word、选区搜索、属性名/属性值范围和匹配导航
- 光标位于 `{}`、`[]` 或 `()` 任一端时，以红色同步高亮对应符号，并忽略字符串内括号

## 运行

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

现有开发环境可直接运行：

```bash
.venv/bin/python app.py
```

## 制作免安装 Python 的发布包

### macOS `.app`

在 Mac 上可生成 Finder 中直接双击运行的原生应用包（产物架构与构建机器一致）：

```bash
.venv/bin/python -m pip install pyinstaller
.venv/bin/python packaging/build_macos_app.py --clean
open "dist/JSON-Forge-v$(tr -d '\n' < VERSION)-macos-$(uname -m).zip"
```

如果 `.venv` 是由 `uv` 创建且没有 `pip`，第一条命令改为：

```bash
uv pip install --python .venv/bin/python pyinstaller
```

最终文件名为 `dist/JSON-Forge-v<version>-macos-<architecture>.zip`，其中版本号读取自
根目录 `VERSION`；解压后得到 `JSON Forge.app`。
构建目录中的 `.app` 中间产物会在 ZIP 完整性验证后删除。应用内置 Python 和 PySide6，
目标 Mac 无需安装 Python。未使用 Apple Developer ID 签名的本地构建采用 ad-hoc 签名；复制到其他 Mac
后若被 Gatekeeper 阻止，可在 Finder 中右键应用并选择“打开”。打包版的设置与会话存放在
`~/Library/Application Support/JSON Forge/`。

### 便携目录

发布包会把独立 Python 运行时和 PySide6 一起放进目录，因此目标电脑不需要预先安装 Python。目录内的 `start.sh`（macOS）和 `start.bat`（Windows）会自动调用随包附带的运行时。

在 macOS Apple Silicon 上制作发布包：

```bash
.venv/bin/python packaging/build_release.py --target macos-arm64
```

其他目标可使用同一个脚本：

```bash
python packaging/build_release.py --target macos-x86_64
python packaging/build_release.py --target windows-x86_64
```

已有 Windows 便携 ZIP 后，也可以在安装了 Go 的 macOS、Linux 或 Windows 上把它封装成
单个自解压 GUI 程序：

```bash
.venv/bin/python packaging/build_windows_exe.py
```

产物名为 `dist/JSON-Forge-v<version>-windows-x86_64.exe`。它首次运行时会把内置运行环境解压至
`%LOCALAPPDATA%\JSON Forge\runtime-v<version>`，用户设置和会话保存在
`%APPDATA%\JSON Forge`。该本地构建没有商业代码签名，Windows SmartScreen 可能显示提示。

生成的目录和压缩包会放在 `dist/`，文件名中的 `v<version>` 自动读取根目录 `VERSION`（macOS 为 `.tar.gz`，Windows 为 `.zip`，可直接用资源管理器解压）。跨平台制作 Windows 包时建议先安装 [uv](https://docs.astral.sh/uv/)，它可以在非 Windows 电脑上下载对应的 Windows PySide6 依赖；也可以直接在 Windows 电脑上运行脚本。`dist/`、`downloads/` 和运行时文件已加入 `.gitignore`，不会提交到 Git。

同版本重复打包时，已有正式产物不会直接删除，而会先重命名为带本地时间戳前缀的备份，
例如 `bak_20260830_100556_JSON-Forge-v1.0.0-macos-arm64.zip`。时间戳精确到秒；
如果同一秒内名称冲突，会生成 `bak_20260830_100556_2_原文件名`、`..._3_原文件名`。
正常构建清理会保留这些备份文件。

## 项目目录说明

下面列出主要源码、开发文件以及不会提交到 Git 的运行时文件，便于以后迁移或重新打开项目时查找：

```text
json-forge/
├── VERSION                      # 唯一版本号来源，不带 v 前缀
├── version_info.py              # 读取并校验 VERSION，供程序和打包脚本共用
├── release_utils.py             # 发布产物的时间戳备份等共用逻辑
├── app.py                       # 主界面和应用逻辑
├── json_tools.py                # JSON 解析、格式化、压缩、路径和搜索工具
├── requirements.txt             # Python 依赖清单，目前为 PySide6
├── config/
│   ├── settings.default.ini     # 可提交的默认配置，用于“恢复默认配置”
│   └── settings.ini             # 用户当前配置，运行时生成，已加入 .gitignore
├── cache/
│   ├── session.json              # 上次会话内容、Tab、光标、书签等，运行时生成
│   └── json-forge.lock           # 单实例锁文件，程序运行时生成
├── packaging/
│   └── build_release.py          # 生成自带 Python 和 PySide6 的发布包
├── start.sh                      # macOS 启动脚本；发布包优先使用内置 Python
├── start.bat                     # Windows 启动脚本；发布包优先使用内置 Python
├── dist/                         # 打包产物目录，已忽略；包含 macOS/Windows 发布包
├── downloads/                    # 打包时下载的 Python 运行时缓存，已忽略，可重新生成
├── .venv/                        # 本地开发虚拟环境，已忽略，不随项目迁移
├── __pycache__/                  # Python 字节码缓存，已忽略，可随时重新生成
├── test_json_tools.py            # JSON 工具的自动化测试
└── test_app.py                   # 界面和应用功能的自动化测试
```

其中 `dist/` 和 `downloads/` 即使没有提交到 Git，也不要误认为是无用文件：前者是给别人使用的发布包，后者是重新打包时可以复用的下载缓存。`cache/` 和 `config/settings.ini` 则保存本机运行状态和个人设置，复制整个项目目录时可以一并迁移。

用户配置默认保存在程序目录下的 `config/settings.ini`，默认值集中在可提交的 `config/settings.default.ini`；会话快照保存在 `cache/session.json`。复制或迁移整个项目目录时会一并带走配置和上次会话。会话内容和个人配置均已加入 `.gitignore`，不会提交到 Git。如果程序目录没有写权限，设置会自动回退到系统配置存储，会话文件则会在可写位置保存失败时保持原有文件不变。

常用快捷键：`Ctrl/Cmd + Enter` 格式化，`Ctrl/Cmd + Shift + M` 紧凑压缩，
`Ctrl/Cmd + T` 新建标签，`Ctrl/Cmd + W` 关闭当前标签。
