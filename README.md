# Visual Studio Code Github Copilot 聊天历史转换Markdown 小工具

## 简介

本项目是一个小工具，用于将 Visual Studio Code 中 Github Copilot 的聊天历史记录转换为 Markdown 格式。通过此工具，用户可以轻松地将聊天记录整理为文档，便于存档或分享。

## 功能

- 将聊天历史记录从 JSON 格式转换为 Markdown 格式。
- 支持批量处理聊天记录。
- 提供简单的图形用户界面（GUI）以便用户操作。
- 自动生成汇总的 Markdown 文件。

## 文件结构

- `aggregate_markdown.py`：用于汇总多个 Markdown 文件。
- `chat_json_to_md.py`：核心脚本，将聊天记录从 JSON 转换为 Markdown。
- `chat_md_gui.py`：提供图形用户界面以便用户操作。
- `build_exe.ps1`：用于构建可执行文件的脚本。
- `AGGREGATED.md`：汇总生成的 Markdown 文件。
- `build/`：存放构建生成的文件。
- `__pycache__/`：Python 缓存文件夹。

## 使用方法

### 环境要求

- Python 3.10 或更高版本。
- Windows 操作系统。

### 安装依赖

1. 确保已安装 Python。
2. 安装所需依赖：

```bash
pip install -r requirements.txt
```

### 运行工具

1. 使用命令行运行核心脚本：

```bash
python chat_json_to_md.py
```

2. 或运行图形界面：

```bash
python chat_md_gui.py
```

3. 使用 `aggregate_markdown.py` 汇总生成的 Markdown 文件：

```bash
python aggregate_markdown.py
```

### 构建可执行文件

运行 `build_exe.ps1` 脚本以生成可执行文件：

```powershell
./build_exe.ps1
```

## 贡献

欢迎提交问题（Issues）和拉取请求（Pull Requests）以改进本项目。

## 许可证

本项目采用 MIT 许可证，详情请参阅 LICENSE 文件。
