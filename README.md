# 朱雀TV 直播源自动更新

自动检测并更新朱雀TV直播源，清理广告频道后保存。

## 使用方法

### 1. Fork 或创建仓库

将此仓库 Fork 到你自己的 GitHub 账号。

### 2. 启用 GitHub Actions

进入仓库 -> Settings -> Actions -> General -> 勾选 **Allow all actions**，保存。

### 3. 获取直播源

**方式一：直接下载**

访问仓库中的 `source.txt` 文件，复制内容导入到 Fongmi 影视。

**方式二：Raw 链接（推荐）**

在 Fongmi 影视的直播源设置中，添加订阅地址：
```
https://raw.githubusercontent.com/你的用户名/zqtv-live-source/main/source.txt
```

### 4. 手动触发更新

进入 Actions 页面 -> 左侧选 "朱雀TV直播源自动更新" -> 右侧 "Run workflow" 按钮。

## 自动运行

默认每 2 小时自动检测一次。如果直播源有更新，会自动下载解密并提交到仓库。

## 更新日志

查看 `update_log.md` 了解每次检测的结果。
