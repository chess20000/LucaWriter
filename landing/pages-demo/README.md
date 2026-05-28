# LucaWriter Pages Demo

这是 LucaWriter 的 Cloudflare Pages 静态只读演示版。

## 特点

- 无 Python 后端、无数据库、无构建步骤。
- 所有演示数据都写在 `index.html` 的 JavaScript 常量里。
- 支持切换章节、搜索正文、查看摘要/大纲/吃书雷达/时间线/读者预言。
- AI 对话为预设演示回复，不连接任何模型服务。

## 部署到 Cloudflare Pages

在 Cloudflare Pages 中：

- Build command: 留空
- Build output directory: `landing/pages-demo`
- Root directory: 仓库根目录

如果只上传这个文件夹，则发布目录直接选择当前目录即可。

## 与完整版的差异

这个演示版不会登录、不会保存、不会导入导出、不会读写 `usrdata/`，也不会使用 `kb.db`、ChromaDB 或本地 llama.cpp。它的目标只是给访问者快速体验 LucaWriter 的界面和信息架构。
