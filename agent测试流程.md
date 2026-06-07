# Agent 测试流程

> **固定登录凭据**：用户名 `aha`，密码 `1111`
> **测试地址**：`http://127.0.0.1:10000`
> **服务器启动**：`cd C:\Users\chen7\Desktop\lucawriter && .\.venv\Scripts\python.exe backend\main.py`

---

## 1. 启动前检查

```powershell
# 1.1 检查端口是否已被占用（确认没有残留进程）
netstat -ano | Select-String "10000.*LISTEN"

# 1.2 如果有残留进程，先杀掉
taskkill /PID <PID> /F

# 1.3 确认 users.json 没有被锁定
Get-Content usrdata\users.json | ConvertFrom-Json | ConvertTo-Json
# 确保 aha 用户没有 failed_attempts / locked_until 字段
```

## 2. 启动服务器

```powershell
cd C:\Users\chen7\Desktop\lucawriter
.\.venv\Scripts\python.exe backend\main.py
```

看到 `Server running on http://127.0.0.1:10000` 即启动成功。

## 3. 登录

1. 打开浏览器，访问 `http://127.0.0.1:10000`
2. 输入用户名 `aha`、密码 `1111`
3. 点击 Login

**预期**：进入书架页面，显示已有作品/书本。

## 4. 书架页测试

| 检查项 | 操作 | 预期 |
|--------|------|------|
| 作品卡片显示 | 观察书架 | 每个作品显示标题、书本数、章节数 |
| 新建作品 | 点击「+ 新建系列」 | 弹出创建对话框 |
| 深色/浅色切换 | 点击右上角主题按钮 | 主题正常切换 |
| 设置入口 | 点击右上角齿轮 | 弹出设置面板 |

## 5. 作品页测试

| 检查项 | 操作 | 预期 |
|--------|------|------|
| 进入作品 | 点击书架上的作品卡片 | 进入作品详情页 |
| 书本列表 | 观察左侧 | 显示作品下所有书本 |
| 点击书本 | 点击某书本 | 进入编辑页 |

## 6. 编辑页测试（核心）

### 6.1 章节列表

| 检查项 | 操作 | 预期 |
|--------|------|------|
| 侧栏可见 | 鼠标移到左侧边缘 18px 触发区 | 章节侧栏滑出显示 |
| 章节列表 | 观察侧栏 | 列出所有章节，每项显示标题和字数 |
| 切换章节 | 点击某章节 | 编辑器加载该章节内容 |
| 新建章节 | 点击「+ 新建章节」 | 创建新章节并自动选中 |
| 删除章节 | hover 章节项，点 × | 章节移到回收站 |
| 回收站 | 点击「回收站」按钮 | 显示已删除章节，可恢复 |
| 章节搜索 | 在搜索框输入关键词 | 实时过滤章节列表 |
| 拖拽排序 | 拖拽目录管理中的章节 | 顺序正确保存 |

### 6.2 编辑器

| 检查项 | 操作 | 预期 |
|--------|------|------|
| 内容加载 | 切换章节 | 编辑器显示章节正文 |
| 编辑保存 | 输入文字 | 自动保存（观察保存指示器） |
| 字数统计 | 编辑文字 | topbar 右侧字数实时更新 |
| 章节标题 | 修改 topbar 标题输入框 | 标题自动保存 |
| 搜索 Ctrl+F | 按 Ctrl+F | 弹出搜索栏，输入关键词高亮匹配 |
| 替换 Ctrl+H | 按 Ctrl+H | 弹出搜索+替换栏 |

### 6.3 "下一章"按钮

| 检查项 | 操作 | 预期 |
|--------|------|------|
| 本章写完 | 点击 topbar「✓ 下一章」 | 创建下一章并跳转 |

### 6.4 AI 对话（Luca）

| 检查项 | 操作 | 预期 |
|--------|------|------|
| 对话面板 | 观察左侧 Luca 聊天区 | 聊天框可见 |
| 发送消息 | 输入问题后回车 | AI 回复流式输出 |
| 多轮对话 | 继续追问 | 上下文连贯 |
| 清空对话 | 点击清空按钮 | 对话历史清除 |

### 6.5 底栏 Tab

| 检查项 | 操作 | 预期 |
|--------|------|------|
| 预测 | 点击「预言」Tab | 显示读者预言内容 |
| 大纲 | 点击「大纲」Tab | 显示人物/设定/事件/伏笔 |
| 摘要 | 点击「摘要」Tab | 显示通读状态和笔记 |
| 时间线 | 点击「时间线」Tab | 显示时间线可视化 |

## 7. 设置页测试

| 检查项 | 操作 | 预期 |
|--------|------|------|
| AI Provider | 切换预设 | 列表刷新 |
| 外观-编辑器字号 | 点击不同字号 | 编辑器字号即时变化 |
| 主题切换 | 切换深色/浅色 | 全局主题切换 |

## 8. 导入导出测试

| 检查项 | 操作 | 预期 |
|--------|------|------|
| 导入文件 | 书架→书本菜单→导入 | 支持 txt/md/docx/pdf/epub |
| 导出 ZIP | 书本菜单→导出 ZIP | 下载 JSON 源文件 |
| 导出 MD | 书本菜单→导出 MD | 下载合并 Markdown |
| 导出 EPUB | 书本菜单→导出 EPUB | 下载 EPUB 电子书 |

## 9. 常见问题排查

### 9.1 章节不显示
```powershell
# 检查后端是否有 AttributeError: 'sqlite3.Row' object has no attribute 'get'
Get-Content server_err.txt -Tail 20
# 如有此错误 → 重启服务器即可（最新代码已修复）
```

### 9.2 账户锁定
```powershell
# 编辑 usrdata/users.json，删除 aha 用户下的 failed_attempts 和 locked_until 字段
```

### 9.3 服务器崩溃
```powershell
# 检查是否有多余进程
netstat -ano | Select-String "10000.*LISTEN"
# 全部杀掉后重启
taskkill /PID <所有PID> /F
```

### 9.4 页面空白/加载中
```powershell
# 检查 body 是否有 loading class（应被 init() 移除）
# 检查浏览器 Console 是否有 JS 错误
```

## 10. 一键测试脚本

```powershell
# === 快速测试 ===
$url = "http://127.0.0.1:10000"

# 登录获取 token
$loginBody = @{username="aha";password="1111"} | ConvertTo-Json
try {
    $resp = Invoke-RestMethod -Uri "$url/api/auth/login" -Method Post -Body $loginBody -ContentType "application/json"
    Write-Host "✓ 登录成功" -ForegroundColor Green
} catch {
    Write-Host "✗ 登录失败: $_" -ForegroundColor Red
    # 尝试解锁
    $users = Get-Content usrdata\users.json -Raw | ConvertFrom-Json
    $users.aha.PSObject.Properties.Remove('failed_attempts')
    $users.aha.PSObject.Properties.Remove('locked_until')
    $users | ConvertTo-Json -Depth 5 | Set-Content usrdata\users.json
    Write-Host "已清除锁定" -ForegroundColor Yellow
}
```
