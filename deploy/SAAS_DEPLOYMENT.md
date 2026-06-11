# LucaWriter SaaS 部署

## 首次安装

服务器目录：

```text
/home/ubuntu/lucawriter-core/  程序，只读
/home/ubuntu/lucawriter-data/  多租户数据
/home/ubuntu/lucawriter.env    服务环境变量，权限 0600
```

同步程序时排除用户数据、Git 元数据和本地构建产物：

```bash
rsync -az --delete \
  --exclude .git --exclude usrdata --exclude .venv --exclude local_llm \
  ./ aws:/home/ubuntu/lucawriter-core/
```

在服务器安装运行环境：

```bash
python3 -m venv /home/ubuntu/lucawriter-core/.venv
/home/ubuntu/lucawriter-core/.venv/bin/pip install -r /home/ubuntu/lucawriter-core/requirements.txt
install -d -m 0700 /home/ubuntu/lucawriter-data
install -m 0600 /home/ubuntu/lucawriter-core/deploy/lucawriter.env.example /home/ubuntu/lucawriter.env
```

编辑 `/home/ubuntu/lucawriter.env`，确保两个共享 secret 与 Coobox 配置一致。

```bash
sudo install -m 0644 /home/ubuntu/lucawriter-core/deploy/lucawriter.service /etc/systemd/system/lucawriter.service
sudo systemctl daemon-reload
sudo systemctl enable --now lucawriter
curl http://127.0.0.1:21000/api/auth/status
```

未带 Coobox 签名时返回 401 是正确结果。服务必须只监听 `127.0.0.1:21000`。

## Coobox 配置

`/home/ubuntu/Coobox/.env` 至少加入：

```text
LUCA_UPSTREAM=127.0.0.1:21000
LUCA_SAAS_SECRET=与 lucawriter.env 相同
LUCA_INTERNAL_SECRET=与 lucawriter.env 相同
DEEPSEEK_API_KEY=站长填写
DEEPSEEK_API_BASE=https://api.deepseek.com
# 2026-06-11 deepseek-v4-flash 缓存未命中价；官网变价后同步更新
COOBOX_DS_PRICE_IN=1
COOBOX_DS_PRICE_OUT=2
```

Gunicorn 配置在导入 Coobox `.env` 前读取线程数，因此线程数通过 systemd override 注入：

```bash
sudo mkdir -p /etc/systemd/system/coobox.service.d
printf '[Service]\nEnvironment=COOBOX_THREADS=16\n' \
  | sudo tee /etc/systemd/system/coobox.service.d/saas.conf >/dev/null
```

修改后执行：

```bash
sudo systemctl restart coobox lucawriter
sudo systemctl status coobox lucawriter --no-pager
```

## 日常升级

1. 先执行 Coobox 备份。
2. `rsync` 新版 LucaWriter 到 `/home/ubuntu/lucawriter-core/`，不要覆盖 `lucawriter-data`。
3. 更新依赖并重启。
4. 验证本机端口、`/write/` 登录墙、余额查询和一次 AI 对话。

```bash
stamp=$(date +%Y%m%d-%H%M%S)
backup_dir="/home/ubuntu/coobox-backups/pre-upgrade-$stamp"
mkdir -p "$backup_dir"
BACKUP_DB="$backup_dir/coobox.sqlite3" python3 -c \
  "import os,sqlite3; s=sqlite3.connect('/home/ubuntu/Coobox/data/coobox.sqlite3'); d=sqlite3.connect(os.environ['BACKUP_DB']); s.backup(d); d.close(); s.close()"
tar -czf "$backup_dir/files.tar.gz" --exclude coobox.sqlite3 \
  -C /home/ubuntu/Coobox data .env
/home/ubuntu/lucawriter-core/.venv/bin/pip install -r /home/ubuntu/lucawriter-core/requirements.txt
sudo systemctl daemon-reload
sudo systemctl restart lucawriter coobox
curl -I http://127.0.0.1:8000/write/
sudo journalctl -u lucawriter -u coobox -n 100 --no-pager
```

## 回滚

程序回滚不动 `/home/ubuntu/lucawriter-data`。恢复上一版程序后重新安装对应依赖并重启：

```bash
sudo systemctl restart lucawriter coobox
```

数据库表均通过 `CREATE TABLE IF NOT EXISTS` 增量创建；不要删除已有 `kb.db` 或租户目录。
