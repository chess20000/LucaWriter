# 贡献指南

欢迎！LucaWriter 是一个个人项目，但如果你发现了 bug 或有改进想法，欢迎提 issue 或 pull request。

## 提 Issue

- 请先搜一下已有的 issue，避免重复
- 标题简明扼要，说清楚问题或建议
- Bug 请附上：
  - 操作系统版本
  - 后端日志（`usrdata/logs/` 下最新日志文件）
  - 重现步骤

## 提 Pull Request

1. Fork 本仓库，从 `master` 创建新分支
2. 代码风格与现有代码保持一致（见 `CLAUDE.md`）
3. 修改后确保后端能正常启动：`python backend/main.py`
4. 提交 PR，描述清楚改了什么、为什么改

## 开发环境

```bash
pip install -r requirements.txt
python backend/main.py
```

浏览器访问 `http://127.0.0.1:10000`

Python 版本：3.12（见 `.python-version`）

## 代码规范

- 不引入非必要的依赖
- 不改没有坏的东西
- 知识库变更必须向后兼容（见 `KB_REWRITE_DESIGN.md`）
- UI 修改请参考 `CLAUDE.md` 中的主题/布局/动效约定

## 许可

提交代码即表示你同意将其以 [Apache-2.0](LICENSE) 许可发布。
