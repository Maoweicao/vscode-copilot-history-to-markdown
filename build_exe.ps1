# 检测是否安装了 pyinstaller
if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] 未检测到 pyinstaller，请先安装：pip install pyinstaller"
    exit 1
}

# 设置脚本路径和输出路径
$scriptPath = "chat_md_gui.py"
$outputDir = "dist"

# 检查脚本文件是否存在
if (-not (Test-Path $scriptPath)) {
    Write-Host "[ERROR] 脚本文件 $scriptPath 不存在"
    exit 1
}

# 执行 pyinstaller 打包命令
Write-Host "正在使用 pyinstaller 打包..."
pyinstaller --onefile --noconsole -y --distpath $outputDir $scriptPath

# 检查打包是否成功
if (-not (Test-Path "$outputDir/chat_md_gui.exe")) {
    Write-Host "[ERROR] 打包失败，请检查日志"
    exit 1
}

Write-Host "[SUCCESS] 打包完成，生成的可执行文件位于 $outputDir/chat_md_gui.exe"