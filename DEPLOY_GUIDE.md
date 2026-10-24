# Racing Community PWA 部署步骤

这个目录已经包含运行网站所需的全部文件。不要把密码或 GitHub Token 发给任何人。

## 1. 上传到 GitHub

1. 打开 GitHub，点击右上角 `+`，选择 `New repository`。
2. Repository name 填 `racing-community-pwa`。
3. 选择 `Public`，勾选 `Add a README file`，然后点击 `Create repository`。
4. 进入新仓库，点击 `Add file` → `Upload files`。
5. 打开本目录 `pwa_deploy`，选中里面的所有文件和 `assets` 文件夹，拖到 GitHub 上传区域。
6. 页面下方点击 `Commit changes`。

## 2. 在 Render 部署

1. 登录 Render，点击 `New` → `Web Service`。
2. 选择 `Build and deploy from a Git repository`，连接 GitHub。
3. 选择刚创建的 `racing-community-pwa` 仓库。
4. Render 会自动识别根目录的 `Dockerfile`。
5. 服务名称可以填 `racing-community`，实例类型选择免费方案。
6. 不需要填写数据库，也不需要环境变量或 API Key。
7. 点击 `Create Web Service`，等待第一次构建完成。
8. 页面顶部会出现一个类似 `https://racing-community-xxxx.onrender.com` 的 HTTPS 地址。

## 3. 安装到手机

Android：

1. 用 Chrome 打开 Render 提供的网址。
2. 点击右上角菜单。
3. 选择“安装应用”或“添加到主屏幕”。

iOS：

1. 必须用 Safari 打开 Render 提供的网址。
2. 点击底部分享按钮。
3. 选择“添加到主屏幕”。

安装后，桌面会出现 Racing Community 图标，打开时使用独立窗口。

## 更新

以后需要更新时，在 GitHub 仓库中替换对应文件并提交一次。Render 会自动重新部署；手机上的 PWA 下次打开时会自动使用新版，不需要重新安装。

PWA 需要联网读取实时赛果、天气和车手照片。断网时会显示应用壳或离线提示。
