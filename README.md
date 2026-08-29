# JSON Studio

版本：v1.0.0

一个使用 Python + PySide6 编写的本地 JSON 桌面工具。项目目录名为
`json-viewer-venus`。

## 功能

- 从日志或其他混杂文本中提取首个完整 JSON 对象/数组并格式化
- 一键紧凑压缩
- 键名切换为双引号、单引号或无引号（不适合裸写的特殊键名会保留引号）
- 光标所在位置实时显示 JSONPath，可复制带 `$` 或不带 `$` 的路径
- JSON / JSON-like 语法高亮，支持对象和数组路径
- “设置”菜单可切换浅色/深色主题，并自动记住选择
- 支持双引号、单引号和无引号属性名混用的 JSON-like 输入；转换前会提示是否净化
- Chrome 风格多标签编辑，可新增、关闭、拖动排序，双击标签标题可重命名
- “设置 → 关于”显示软件版本及当前 Python 版本
- 非空标签关闭前确认；“设置 → 更多设置 → General”可控制退出确认提示
- “Tab样式”可在默认的“实用模式”和经典“扁平模式”之间切换并记住选择
- 编辑器默认显示行号，可在 General 中关闭；对象与数组支持点击 gutter 箭头折叠/展开
- 工具栏“折叠/展开”按钮可一次收起或展开当前标签的全部节点

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

常用快捷键：`Ctrl/Cmd + Enter` 格式化，`Ctrl/Cmd + Shift + M` 紧凑压缩，
`Ctrl/Cmd + T` 新建标签，`Ctrl/Cmd + W` 关闭当前标签。
